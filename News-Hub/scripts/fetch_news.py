"""Fetch the week's financial-literacy news and merge it into data/items.json.

Runs every Monday from GitHub Actions. Uses only free sources: RSS feeds,
the Federal Register API and Google News search feeds. No API keys needed.

Usage:  python scripts/fetch_news.py [--days 8]
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from common import (ROOT, Neutrality, build_classifier, fetch_federal_register,
                    fetch_google_news, fetch_rss, item_id, load_yaml, phrase_regex)

DATA_FILE = ROOT / "data" / "items.json"
KEEP_DAYS = 400  # history kept for the archive


def fetch_source(src: dict, since: datetime) -> list[dict]:
    kind = src["type"]
    if kind == "rss":
        return fetch_rss(src["url"])
    if kind == "google_news":
        return fetch_google_news(src["query"])
    if kind == "federal_register":
        return fetch_federal_register(src["agency"], since)
    raise ValueError(f"unknown source type {kind!r}")


# ---------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=8,
                        help="how far back to look (default 8, overlapping last week's run)")
    args = parser.parse_args()

    sources_cfg = load_yaml("sources.yaml")
    classify = build_classifier(load_yaml("topics.yaml"))
    exclude_re = phrase_regex(sources_cfg.get("exclude_titles", []))
    blocked_publishers = {p.lower() for p in sources_cfg.get("exclude_publishers", [])}
    neutral = Neutrality()
    allowed_domains = tuple(d.lower() for d in sources_cfg.get("allowed_publisher_domains", []))

    def publisher_ok(it: dict) -> bool:
        if it.get("publisher", "").lower() in blocked_publishers:
            return False
        host = it.get("publisher_url", "").lower().rstrip("/")
        return not (allowed_domains and host and not host.endswith(allowed_domains))

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=args.days)

    existing: dict[str, dict] = {}
    if DATA_FILE.exists():
        for it in json.loads(DATA_FILE.read_text(encoding="utf-8")):
            # Re-check history too, so filter changes also clean up the archive.
            if it.get("official") or neutral.ok(it):
                existing[it["id"]] = it

    sources = sources_cfg["sources"]
    results: dict[str, list[dict] | Exception] = {}

    def run(src):
        try:
            return src["id"], fetch_source(src, since)
        except Exception as exc:  # one bad source must not stop the rest
            return src["id"], exc

    with ThreadPoolExecutor(max_workers=8) as pool:
        for sid, res in pool.map(run, sources):
            results[sid] = res

    added = updated = 0
    failures = []
    seen_titles = {it["title"].lower() for it in existing.values()}
    for src in sources:
        res = results[src["id"]]
        if isinstance(res, Exception):
            failures.append(f"{src['name']}: {type(res).__name__}: {res}")
            print(f"  ✗ {src['name']}: {res}", file=sys.stderr)
            continue
        kept = 0
        fresh = [it for it in res if it["published"] and it["published"] >= since]
        fresh.sort(key=lambda it: it["published"], reverse=True)
        if src.get("max_items"):
            fresh = fresh[: src["max_items"]]
        for it in fresh:
            if exclude_re and exclude_re.search(it["title"]):
                continue
            if src["type"] == "google_news" and not publisher_ok(it):
                continue
            if not src.get("official") and not neutral.ok(it):
                continue
            topics, urgent = classify(it, src.get("default_topic"))
            if not topics:
                continue
            iid = item_id(it["url"])
            record = {
                "id": iid,
                "title": it["title"],
                "url": it["url"],
                "summary": it.get("summary", ""),
                "source_id": src["id"],
                "source_name": it.get("publisher") or src["name"],
                "official": bool(src.get("official")),
                "published": it["published"].strftime("%Y-%m-%d"),
                "topics": topics,
                "urgent": urgent,
                "doc_type": it.get("doc_type"),
                "effective_on": it.get("effective_on"),
                "comments_close_on": it.get("comments_close_on"),
            }
            if iid in existing:
                record["first_seen"] = existing[iid].get("first_seen", record["published"])
                existing[iid] = record
                updated += 1
            else:
                # Same headline syndicated under a different link: skip.
                if record["title"].lower() in seen_titles:
                    continue
                record["first_seen"] = now.strftime("%Y-%m-%d")
                existing[iid] = record
                seen_titles.add(record["title"].lower())
                added += 1
            kept += 1
        print(f"  ✓ {src['name']}: {len(res)} in feed, {len(fresh)} recent, {kept} kept")

    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    items = sorted((it for it in existing.values() if it["published"] >= cutoff),
                   key=lambda it: (it["published"], it["title"]), reverse=True)
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(items, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    status = {"fetched_at": now.isoformat(timespec="seconds"), "failures": failures}
    (ROOT / "data" / "last_run.json").write_text(json.dumps(status, indent=1) + "\n", encoding="utf-8")

    print(f"\n{added} new, {updated} refreshed, {len(items)} total. {len(failures)} source(s) failed.")
    # Only fail the run if every source failed (so the site still updates on partial outages).
    return 1 if len(failures) == len(sources) else 0


if __name__ == "__main__":
    sys.exit(main())
