"""Fetch the week's financial-literacy news and merge it into data/items.json.

Runs every Monday from GitHub Actions. Uses only free sources: RSS feeds,
the Federal Register API and Google News search feeds. No API keys needed.

Usage:  python scripts/fetch_news.py [--days 8]
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import html
import json
import re
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

warnings.filterwarnings("ignore")  # quiet urllib3/LibreSSL noise on older Macs

import feedparser
import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "items.json"
KEEP_DAYS = 400  # history kept for the archive
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh) FinancialLiteracyNewsHub/1.0",
    "Accept": "application/rss+xml, application/xml, application/json, text/xml, */*",
}
TIMEOUT = (10, 30)


def load_yaml(name: str) -> dict:
    return yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8")) or {}


def clean_text(raw: str | None, limit: int = 400) -> str:
    """Strip HTML tags/entities and collapse whitespace; trim to `limit` chars at a word."""
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + "…"
    return text


def item_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def entry_date(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
    return None


# ---------------------------------------------------------------- fetchers

def fetch_rss(url: str) -> list[dict]:
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    items = []
    for e in feed.entries:
        link = e.get("link") or ""
        title = clean_text(e.get("title"), 300)
        if not link or not title:
            continue
        src = e.get("source") or {}
        items.append({
            "title": title,
            "url": link,
            "publisher_url": src.get("href", ""),
            "summary": clean_text(e.get("summary") or e.get("description")),
            "published": entry_date(e),
        })
    return items


def fetch_google_news(query: str) -> list[dict]:
    url = ("https://news.google.com/rss/search?q=" + quote_plus(query + " when:7d")
           + "&hl=en-US&gl=US&ceid=US:en")
    items = fetch_rss(url)
    for it in items:
        # Google titles look like "Headline - Publisher"; split the publisher off.
        head, sep, publisher = it["title"].rpartition(" - ")
        if sep and head:
            it["title"], it["publisher"] = head, publisher
        # Google's summary is just the headline again, so drop it.
        if it["summary"].startswith(it["title"][:40]):
            it["summary"] = ""
    return items


def fetch_federal_register(agency: str, since: datetime) -> list[dict]:
    params = {
        "conditions[agencies][]": agency,
        "conditions[publication_date][gte]": since.strftime("%Y-%m-%d"),
        "order": "newest",
        "per_page": 50,
        "fields[]": ["title", "html_url", "abstract", "publication_date", "type",
                     "effective_on", "comments_close_on"],
    }
    resp = requests.get("https://www.federalregister.gov/api/v1/documents.json",
                        params=params, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    items = []
    for d in resp.json().get("results", []):
        extra = []
        if d.get("effective_on"):
            extra.append(f"Effective {d['effective_on']}.")
        if d.get("comments_close_on"):
            extra.append(f"Comments due {d['comments_close_on']}.")
        summary = clean_text(d.get("abstract"), 500)
        items.append({
            "title": clean_text(d["title"], 300),
            "url": d["html_url"],
            "summary": (summary + " " + " ".join(extra)).strip(),
            "published": datetime.strptime(d["publication_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc),
            "doc_type": d.get("type"),  # Rule, Proposed Rule, Notice...
            "effective_on": d.get("effective_on"),
            "comments_close_on": d.get("comments_close_on"),
        })
    return items


def fetch_source(src: dict, since: datetime) -> list[dict]:
    kind = src["type"]
    if kind == "rss":
        return fetch_rss(src["url"])
    if kind == "google_news":
        return fetch_google_news(src["query"])
    if kind == "federal_register":
        return fetch_federal_register(src["agency"], since)
    raise ValueError(f"unknown source type {kind!r}")


# ---------------------------------------------------------------- classify

def phrase_regex(words: list[str]) -> re.Pattern | None:
    words = [w for w in words if w]
    if not words:
        return None
    alts = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?<![\w-])(?:{alts})(?![\w-])", re.IGNORECASE)


# "by September 30", "before Oct. 1", "until December 31" -> treat as a deadline
DEADLINE_RE = re.compile(
    r"\b(?:by|before|until|through|no later than)\s+"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}\b",
    re.IGNORECASE)


def build_classifier(topics_cfg: dict):
    topic_res = [(t["id"], phrase_regex(t.get("keywords", []))) for t in topics_cfg["topics"]]
    urgent_re = phrase_regex(topics_cfg.get("urgent_keywords", []))

    def classify(item: dict, default_topic: str | None) -> tuple[list[str], bool]:
        text = f"{item['title']} {item.get('summary', '')}"
        # Most keyword hits first (title hits count double); ties keep config order.
        title = item["title"]
        scored = []
        for order, (tid, rx) in enumerate(topic_res):
            if not rx:
                continue
            hits = len(rx.findall(text)) + len(rx.findall(title))
            if hits:
                scored.append((-hits, order, tid))
        topics = [tid for _, _, tid in sorted(scored)]
        if not topics and default_topic:
            topics = [default_topic]
        urgent = bool(urgent_re and urgent_re.search(text)) or bool(DEADLINE_RE.search(text))
        if item.get("doc_type") in ("Rule", "Proposed Rule"):
            urgent = True
        return topics, urgent

    return classify


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
