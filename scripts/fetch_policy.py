"""Build the Policy tab data: enacted and in-progress policy, federal and state. No API keys.

Sources:
  Federal (official records)
    - Federal Register final and proposed rules from key agencies
    - GovInfo: enacted public laws, and bills as they move through Congress
  State (news coverage, labeled as such)
    - One Google News search per state; the status shown is read from the headline

Items are merged into data/policy.json and kept for `months` (config/policy.yaml), so
coverage builds up week to week.

Usage:  python scripts/fetch_policy.py
"""
from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from common import (ROOT, Neutrality, build_classifier, fetch_federal_register, fetch_google_news,
                    fetch_rss, item_id, load_yaml, phrase_regex)

OUT_FILE = ROOT / "data" / "policy.json"

STATES = {
    "US": "Federal", "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}

# "H.R. 1234 (IH) - Title", "S.J.Res. 12 (ENR) - Title"
BILL_TITLE_RE = re.compile(r"^(?P<num>[A-Z][A-Za-z.\s]*?\d+)\s*\((?P<ver>[A-Z]+)\)\s*-\s*(?P<title>.+)$")
PLAW_TITLE_RE = re.compile(r"^Public Law (?P<congress>\d+)\s*-\s*(?P<n>\d+)\s*-\s*(?P<title>.+)$")


# ---------------------------------------------------------------- federal: rules

def federal_rules(cfg: dict, topics_cfg: dict, since: datetime, failures: list) -> list[dict]:
    fr = cfg.get("federal_register", {})
    classify = build_classifier(topics_cfg)
    title_re = phrase_regex(fr.get("title_keywords", []))
    default_topics = fr.get("default_topics", {})
    skip_re = phrase_regex(fr.get("exclude_titles", []))
    today = date.today().isoformat()

    def load(agency):
        try:
            return agency, fetch_federal_register(agency, since, doc_types=["RULE", "PRORULE"], max_pages=3)
        except Exception as exc:
            return agency, exc

    out = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        for agency, res in pool.map(load, fr.get("agencies", [])):
            if isinstance(res, Exception):
                failures.append(f"Federal Register ({agency}): {res}")
                continue
            for d in res:
                if skip_re and skip_re.search(d["title"]):
                    continue
                if title_re and not title_re.search(d["title"]):
                    continue
                topics, _ = classify(d, None)
                topics = topics or [default_topics.get(agency, default_topics.get("default", "consumer-credit"))]
                final = d["doc_type"] == "Rule"
                if final:
                    eff = d.get("effective_on")
                    label = "Final rule" + (f" · takes effect {eff}" if eff and eff > today else "")
                else:
                    due = d.get("comments_close_on")
                    label = "Proposed rule" + (f" · comments due {due}" if due and due >= today else "")
                out[d["url"]] = official_record(
                    origin="federal_register", key=d["url"], number="", title=d["title"], summary=d["summary"],
                    status="enacted" if final else "in_progress", label=label,
                    action="Published as a final rule" if final else "Published as a proposed rule",
                    when=d["published"], source=", ".join(d.get("agency_names") or []) or "Federal Register",
                    url=d["url"], topics=topics)
    return list(out.values())


def official_record(*, origin, key, number, title, summary, status, label, action, when, source, url,
                    topics):
    return {
        "id": item_id(key),
        "kind": "official",
        "origin": origin,  # federal_register | congress
        "jurisdiction": "US",
        "jurisdiction_name": "Federal",
        "number": number,
        "title": title,
        "summary": summary,
        "status": status,
        "status_label": label,
        "last_action": action,
        "last_action_date": when.strftime("%Y-%m-%d"),
        "source_name": source,
        "url": url,
        "topics": topics,
    }


# ---------------------------------------------------------------- federal: Congress (GovInfo)

def bill_version_status(title: str, versions: dict) -> tuple[str, str, str, str] | None:
    """(bill number, clean title, status, label) from a GovInfo bill title, or None to skip."""
    m = BILL_TITLE_RE.match(title.strip())
    if not m:
        return None
    number = re.sub(r"\s+", " ", m.group("num")).strip()
    if "Res." in number and "J.Res." not in number:  # simple/concurrent resolutions aren't law
        return None
    label = versions.get(m.group("ver"))
    if not label:
        return None
    return number, m.group("title").strip(), "in_progress", label


def congress_items(cfg: dict, topics_cfg: dict, since: str, failures: list) -> list[dict]:
    gi = cfg.get("govinfo", {})
    title_re = phrase_regex(gi.get("title_keywords", []))
    classify = build_classifier(topics_cfg)
    current_congress = (date.today().year - 1789) // 2 + 1  # 119th in 2025–26
    out = []

    if gi.get("public_laws"):
        try:
            for it in fetch_rss(gi["public_laws"]):
                m = PLAW_TITLE_RE.match(it["title"])
                if not m or not it["published"] or it["published"].strftime("%Y-%m-%d") < since:
                    continue
                if int(m.group("congress")) < current_congress - 1:  # older laws GovInfo re-published
                    continue
                title = m.group("title").strip().strip('"').replace('" or the "', " / ")
                if not (title_re and title_re.search(title)):
                    continue
                topics, _ = classify({"title": title, "summary": ""}, "consumer-credit")
                number = f"Public Law {m.group('congress')}-{m.group('n')}"
                out.append(official_record(
                    origin="congress", key=number, number=number, title=title, summary="", status="enacted",
                    label="Enacted · Public law", action="Signed into law and published",
                    when=it["published"], source="Congress (GovInfo)", url=it["url"], topics=topics))
        except Exception as exc:
            failures.append(f"GovInfo public laws: {exc}")

    if gi.get("bills"):
        try:
            for it in fetch_rss(gi["bills"]):
                parsed = bill_version_status(it["title"], gi.get("versions", {}))
                if not parsed or not it["published"]:
                    continue
                number, title, status, label = parsed
                if not (title_re and title_re.search(title)):
                    continue
                topics, _ = classify({"title": title, "summary": ""}, "consumer-credit")
                out.append(official_record(
                    origin="congress", key=f"bill:{number}", number=number, title=title, summary="", status=status,
                    label=label, action=f"New version published: {label.lower()}",
                    when=it["published"], source="Congress (GovInfo)", url=it["url"], topics=topics))
        except Exception as exc:
            failures.append(f"GovInfo bills: {exc}")
    return out


# ---------------------------------------------------------------- states: news coverage

def state_pattern(code: str, name: str, aliases: list[str]) -> re.Pattern:
    """Matches a state's name (incl. 'Pennsylvanians') or aliases, avoiding look-alikes."""
    if name == "Virginia":
        core = r"(?<!West )\bVirginia"
    elif name == "Washington":
        core = r"\bWashington(?!,?\s*D\.?\s*C\b)"
    else:
        core = r"\b" + re.escape(name)
    parts = [core] + [r"(?<![\w.])" + re.escape(a) + r"(?![\w])" for a in aliases]
    return re.compile("|".join(parts), re.IGNORECASE)


def headline_status(title: str, phrases: dict) -> tuple[str, str]:
    """Status read from a news headline: (filter status, label)."""
    for key, label, status in (("enacted", "Reported: signed into law", "enacted"),
                               ("advancing", "Reported: advancing", "in_progress"),
                               ("proposed", "Reported: proposed", "in_progress")):
        rx = phrase_regex(phrases.get(key, []))
        if rx and rx.search(title):
            return status, label
    return "in_progress", "In the news"


def state_news(cfg: dict, topics_cfg: dict, window_days: int, failures: list) -> list[dict]:
    sn = cfg.get("state_news", {})
    news_cfg = load_yaml("sources.yaml")
    neutral = Neutrality()
    classify = build_classifier(topics_cfg)
    policy_re = phrase_regex(sn.get("policy_words", []))
    exclude_re = phrase_regex(news_cfg.get("exclude_titles", []))
    blocked = {p.lower() for p in news_cfg.get("exclude_publishers", [])}
    allowed = tuple(news_cfg.get("allowed_publisher_domains", []))
    aliases = sn.get("state_aliases", {}) or {}
    national = {o.lower() for o in sn.get("national_outlets", [])}
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    topic_q = " OR ".join(sn.get("topic_phrases", []))
    policy_q = "bill OR law OR legislature OR lawmakers OR governor OR \"signed into law\""

    def search(code):
        name = STATES[code]
        query = f'"{name}" ({topic_q}) ({policy_q})'
        try:
            return code, fetch_google_news(query, window=f"{window_days}d")
        except Exception as exc:
            return code, exc

    codes = [c for c in STATES if c != "US"]
    out = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for code, res in pool.map(search, codes):
            if isinstance(res, Exception):
                failures.append(f"State news ({STATES[code]}): {res}")
                continue
            state_re = state_pattern(code, STATES[code], [str(a) for a in aliases.get(code, [])])
            code_re = re.compile(rf"(?<![A-Za-z]){code}(?![A-Za-z])")  # "Spotlight PA"
            kept, seen = [], set()
            for it in sorted(res, key=lambda i: i["published"] or since, reverse=True):
                title, publisher = it["title"], it.get("publisher", "")
                key = re.sub(r"\W+", " ", title.lower()).strip()
                if not it["published"] or it["published"] < since or key in seen:
                    continue
                local_outlet = publisher.lower() not in national and (
                    state_re.search(publisher) or code_re.search(publisher))
                if not (state_re.search(title) or local_outlet):
                    continue
                if not (policy_re and policy_re.search(title)):
                    continue
                if exclude_re and exclude_re.search(title):
                    continue
                host = it.get("publisher_url", "").lower().rstrip("/")
                if publisher.lower() in blocked or (allowed and host and not host.endswith(allowed)):
                    continue
                if not neutral.ok(it):
                    continue
                topics, _ = classify({"title": title, "summary": ""}, None)
                if not topics:
                    continue
                status, label = headline_status(title, sn.get("status_phrases", {}))
                seen.add(key)
                kept.append({
                    "id": item_id(it["url"]),
                    "kind": "news",
                    "origin": "news",
                    "jurisdiction": code,
                    "jurisdiction_name": STATES[code],
                    "number": "",
                    "title": title,
                    "summary": "",
                    "status": status,
                    "status_label": label,
                    "last_action": "",
                    "last_action_date": it["published"].strftime("%Y-%m-%d"),
                    "source_name": publisher or "News",
                    "url": it["url"],
                    "topics": topics,
                })
            out += kept[: sn.get("max_per_state", 15)]
    return out


# ---------------------------------------------------------------- main

def main() -> int:
    cfg = load_yaml("policy.yaml")
    topics_cfg = load_yaml("topics.yaml")
    now = datetime.now(timezone.utc)
    months = int(cfg.get("months", 12))
    since_dt = now - timedelta(days=months * 31)
    since = since_dt.strftime("%Y-%m-%d")
    previous = json.loads(OUT_FILE.read_text(encoding="utf-8")) if OUT_FILE.exists() else {}
    failures: list[str] = []

    sn = cfg.get("state_news", {})
    had_news = any(i.get("kind") == "news" for i in previous.get("items", []))
    window = int(sn.get("days", 14) if had_news else sn.get("first_run_days", 60))

    fresh = federal_rules(cfg, topics_cfg, since_dt, failures)
    fresh += congress_items(cfg, topics_cfg, since, failures)
    fresh += state_news(cfg, topics_cfg, window, failures)

    # Merge with history: new data wins; a bill's newer version replaces its older status.
    # Federal Register rules are re-fetched in full each run, so old copies are replaced.
    merged = {i["id"]: i for i in previous.get("items", [])
              if i.get("origin") in ("congress", "news")}
    for item in fresh:
        old = merged.get(item["id"])
        if old and old.get("last_action_date", "") > item["last_action_date"]:
            continue
        merged[item["id"]] = item

    per_state: dict[str, int] = {}
    items = []
    for it in sorted(merged.values(), key=lambda i: i["last_action_date"], reverse=True):
        if it["last_action_date"] < since:
            continue
        if it["kind"] == "news":
            n = per_state.get(it["jurisdiction"], 0)
            if n >= int(sn.get("max_per_state", 15)) * 3:  # keep a few months of history per state
                continue
            per_state[it["jurisdiction"]] = n + 1
        items.append(it)

    data = {
        "fetched_at": now.isoformat(timespec="seconds"),
        "default_state": cfg.get("default_state", "PA"),
        "states": STATES,
        "items": items,
        "failures": failures,
    }
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for f in failures:
        print(f"  ✗ {f}", file=sys.stderr)
    fed = sum(1 for i in items if i["kind"] == "official")
    print(f"{len(items)} policy items: {fed} federal official, {len(items) - fed} state news "
          f"({len(per_state)} states). News window {window} days. {len(failures)} problem(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
