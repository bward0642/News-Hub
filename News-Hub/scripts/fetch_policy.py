"""Build the Policy tab data: enacted and in-progress policy at the federal and state level.

Sources (factual records, no commentary):
  - Federal Register final and proposed rules from key agencies (free, no key)
  - Bills in Congress and all 50 states from LegiScan (free key in LEGISCAN_API_KEY)

Writes data/policy.json.

Usage:
  LEGISCAN_API_KEY=... python scripts/fetch_policy.py
  python scripts/fetch_policy.py --legiscan-fixture sample.json   (test without a key)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import requests

from common import (HEADERS, ROOT, TIMEOUT, build_classifier, fetch_federal_register, item_id,
                    load_yaml, phrase_regex)

OUT_FILE = ROOT / "data" / "policy.json"
LEGISCAN_URL = "https://api.legiscan.com/"

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

# How a bill's latest action maps to a status. Checked in this order.
DROPPED_RE = re.compile(r"\b(veto(?:ed)?|failed|died|dead|withdrawn|indefinitely postponed|"
                        r"tabled|rejected|lost|stricken|laid on the table)\b", re.I)
OVERRIDE_RE = re.compile(r"veto(?:es)? overridden|override", re.I)
ENACTED_RE = re.compile(r"(signed by (?:the )?governor|approved by (?:the )?governor|governor signed|"
                        r"chaptered|became law|became effective|public law|\bact no\b|\bact \d+|"
                        r"\benacted\b|law without (?:governor'?s? )?signature|effective without|"
                        r"signed by (?:the )?president|became public law|"
                        r"\beffective (?:on|immediately|upon|\d)|\bchapter \d+|statutes of \d{4})", re.I)
TO_GOVERNOR_RE = re.compile(r"(to (?:the )?governor|presented to|enrolled|sent to (?:the )?president|"
                            r"signed in (?:the )?(?:house|senate)|signed by (?:the )?(?:speaker|president of the senate))", re.I)
PASSED_RE = re.compile(r"(passed|passage|third reading|3rd reading|third consideration|concur)", re.I)
RESOLUTION_RE = re.compile(r"^[A-Z]*R\d+[A-Z]?$")  # HR, SR, HJR, SCR... (bills end in B/F)


def bill_status(last_action: str) -> tuple[str, str] | None:
    """(status, label) for a bill, or None if it's no longer moving."""
    text = last_action or ""
    if OVERRIDE_RE.search(text) or ENACTED_RE.search(text):
        return "enacted", "Enacted"
    if DROPPED_RE.search(text):
        return None
    if TO_GOVERNOR_RE.search(text):
        return "in_progress", "Passed legislature"
    if PASSED_RE.search(text):
        return "in_progress", "Passed a chamber"
    return "in_progress", "Introduced / in committee"


# ---------------------------------------------------------------- federal rules

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
                out[d["url"]] = {
                    "id": item_id(d["url"]),
                    "jurisdiction": "US",
                    "jurisdiction_name": "Federal",
                    "kind": "rule",
                    "number": "",
                    "title": d["title"],
                    "summary": d["summary"],
                    "status": "enacted" if final else "in_progress",
                    "status_label": label,
                    "last_action": "Published as a final rule" if final else "Published as a proposed rule",
                    "last_action_date": d["published"].strftime("%Y-%m-%d"),
                    "source_name": ", ".join(d.get("agency_names") or []) or "Federal Register",
                    "url": d["url"],
                    "topics": topics,
                }
    return list(out.values())


# ---------------------------------------------------------------- bills (LegiScan)

def legiscan_search(key: str, query: str, page: int) -> dict:
    resp = requests.get(LEGISCAN_URL, params={"key": key, "op": "getSearch", "state": "ALL",
                                              "query": query, "page": page},
                        headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "OK":
        raise RuntimeError(data.get("alert", {}).get("message") or "LegiScan error")
    return data["searchresult"]


def parse_bills(pages: list[tuple[dict, dict]], cfg: dict, topics_cfg: dict, since: str) -> list[dict]:
    """pages: [(search_cfg, searchresult), ...] → bill records."""
    lc = cfg.get("legiscan", {})
    classify = build_classifier(topics_cfg)
    skip_re = phrase_regex(lc.get("exclude_titles", []))
    min_rel = lc.get("min_relevance", 0)
    best: dict = {}
    for search, result in pages:
        for k, b in result.items():
            if k == "summary" or not isinstance(b, dict):
                continue
            state = b.get("state", "")
            if state not in STATES:
                continue
            number = b.get("bill_number", "")
            title = b.get("title", "")
            if (b.get("relevance") or 0) < min_rel or RESOLUTION_RE.match(number):
                continue
            if skip_re and skip_re.search(title):
                continue
            if (b.get("last_action_date") or "") < since:
                continue
            status = bill_status(b.get("last_action", ""))
            if not status:
                continue
            topics, _ = classify({"title": title, "summary": ""}, search.get("topic"))
            bid = str(b.get("bill_id") or b.get("url"))
            record = {
                "id": f"ls{bid}",
                "jurisdiction": state,
                "jurisdiction_name": STATES[state],
                "kind": "bill",
                "number": number,
                "title": title,
                "summary": "",
                "status": status[0],
                "status_label": status[1],
                "last_action": b.get("last_action", ""),
                "last_action_date": b.get("last_action_date", ""),
                "source_name": "LegiScan",
                "url": b.get("url") or b.get("research_url") or "",
                "topics": topics,
                "relevance": b.get("relevance") or 0,
            }
            if bid not in best or record["relevance"] > best[bid]["relevance"]:
                best[bid] = record
    return list(best.values())


def state_bills(cfg: dict, topics_cfg: dict, since: str, key: str, failures: list) -> list[dict]:
    lc = cfg.get("legiscan", {})
    pages = []
    for search in lc.get("searches", []):
        for page in range(1, lc.get("max_pages", 1) + 1):
            try:
                result = legiscan_search(key, search["query"], page)
            except Exception as exc:
                failures.append(f"LegiScan ({search['query']}): {exc}")
                break
            pages.append((search, result))
            summary = result.get("summary", {})
            if page >= int(summary.get("page_total") or 1):
                break
    return parse_bills(pages, cfg, topics_cfg, since)


# ---------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legiscan-fixture", help="use a saved LegiScan getSearch JSON (testing)")
    args = parser.parse_args()

    cfg = load_yaml("policy.yaml")
    topics_cfg = load_yaml("topics.yaml")
    now = datetime.now(timezone.utc)
    since_dt = now - timedelta(days=int(cfg.get("months", 12)) * 31)
    since = since_dt.strftime("%Y-%m-%d")
    previous = json.loads(OUT_FILE.read_text(encoding="utf-8")) if OUT_FILE.exists() else {}
    failures: list[str] = []

    items = federal_rules(cfg, topics_cfg, since_dt, failures)

    key = os.environ.get("LEGISCAN_API_KEY", "").strip()
    connected = bool(key or args.legiscan_fixture)
    if args.legiscan_fixture:
        fixture = json.loads(open(args.legiscan_fixture, encoding="utf-8").read())
        searches = cfg.get("legiscan", {}).get("searches", [{}])
        bills = parse_bills([(searches[0], fixture["searchresult"])], cfg, topics_cfg, since)
    elif key:
        bills = state_bills(cfg, topics_cfg, since, key, failures)
        if not bills and failures:  # LegiScan down: keep last week's bills rather than none
            bills = [i for i in previous.get("items", []) if i.get("kind") == "bill"]
    else:
        bills = []
    items += bills

    items.sort(key=lambda i: (i["status"] != "enacted", i["last_action_date"]), reverse=False)
    items.sort(key=lambda i: i["last_action_date"], reverse=True)
    data = {
        "fetched_at": now.isoformat(timespec="seconds"),
        "default_state": cfg.get("default_state", "PA"),
        "legiscan_connected": connected,
        "states": STATES,
        "items": items,
        "failures": failures,
    }
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for f in failures:
        print(f"  ✗ {f}", file=sys.stderr)
    fed = sum(1 for i in items if i["jurisdiction"] == "US")
    print(f"{len(items)} policy items ({fed} federal, {len(items) - fed} state). "
          f"LegiScan {'connected' if connected else 'not connected'}. {len(failures)} problem(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
