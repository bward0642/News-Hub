"""Build the Market Watch data: key indicators from FRED, a factual "week in brief",
official economic releases, Federal Reserve items, and neutral market commentary.

Writes data/markets.json. Free sources only; no API keys.

Usage:  python scripts/fetch_markets.py
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from statistics import median

import requests

from common import (HEADERS, ROOT, TIMEOUT, Neutrality, fetch_google_news, fetch_rss, load_yaml,
                    phrase_regex)

OUT_FILE = ROOT / "data" / "markets.json"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}&cosd={start}"
MAX_SPARK_POINTS = 30


# ---------------------------------------------------------------- indicators

def fetch_fred(series_id: str, start: date) -> list[tuple[date, float]]:
    resp = requests.get(FRED_CSV.format(id=series_id, start=start.isoformat()),
                        headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    rows = list(csv.reader(io.StringIO(resp.text)))
    points = []
    for row in rows[1:]:
        if len(row) < 2 or row[1] in ("", "."):
            continue
        try:
            points.append((date.fromisoformat(row[0]), float(row[1])))
        except ValueError:
            continue
    if not points:
        raise ValueError(f"no data returned for {series_id}")
    return points


def year_over_year(points: list[tuple[date, float]]) -> list[tuple[date, float]]:
    by_month = {(d.year, d.month): v for d, v in points}
    out = []
    for d, v in points:
        prev = by_month.get((d.year - 1, d.month))
        if prev:
            out.append((d, (v / prev - 1) * 100))
    return out


def frequency(points: list[tuple[date, float]]) -> str:
    tail = points[-15:]
    gaps = [(b[0] - a[0]).days for a, b in zip(tail, tail[1:])]
    g = median(gaps) if gaps else 1
    return "daily" if g <= 4 else "weekly" if g <= 10 else "monthly"


def as_of_label(d: date, freq: str) -> str:
    if freq == "monthly":
        return d.strftime("%B %Y")
    if freq == "weekly":
        return "Week of " + d.strftime("%b %-d, %Y")
    return d.strftime("%b %-d, %Y")


def build_indicator(cfg: dict, points: list[tuple[date, float]], spark_days: int) -> dict:
    if cfg.get("transform") == "yoy":
        points = year_over_year(points)
    freq = frequency(points)
    last_d, last_v = points[-1]

    if cfg.get("compare") == "week":
        target = last_d - timedelta(days=7)
        earlier = [p for p in points if p[0] <= target]
        prior = earlier[-1] if earlier else points[0]
        period = "over the week"
        vs = "vs. a week earlier"
    else:
        prior = points[-2] if len(points) > 1 else points[-1]
        noun = {"daily": "day", "weekly": "week", "monthly": "month"}[freq]
        period = f"from the previous {noun}" if freq != "monthly" or (last_d - prior[0]).days < 40 \
            else "from the previous reading"
        vs = f"vs. previous {noun}" if period.endswith(noun) else "vs. previous reading"

    kind = cfg.get("change", "points")
    raw = last_v - prior[1]
    if kind == "percent":
        change = (last_v / prior[1] - 1) * 100 if prior[1] else 0.0
        flat = abs(change) < 0.05
        change_text = f"{abs(change):.1f}%"
    elif kind == "dollars":
        change = raw
        flat = abs(change) < 0.005
        cents = round(abs(change) * 100)
        change_text = f"{cents} cent{'s' if cents != 1 else ''}" if cents < 100 else f"${abs(change):.2f}"
    else:
        # Match the precision the value is shown with (CPI shows 1 decimal, rates 2).
        m = re.search(r"\.(\d)f", cfg.get("format", ""))
        decimals = int(m.group(1)) if m else 2
        change = round(round(last_v, decimals) - round(prior[1], decimals), decimals)
        flat = change == 0
        unit = "point" if abs(change) == 1 else "points"
        change_text = f"{abs(change):.{decimals}f} {unit}"
    direction = "flat" if flat else ("up" if change > 0 else "down")

    fmt = cfg.get("format", "{:,.2f}")
    # Sparkline: recent window for daily/weekly series, last 12 readings for monthly ones.
    if freq == "monthly":
        spark = points[-12:]
    else:
        spark = [p for p in points if p[0] >= last_d - timedelta(days=spark_days)]
    if len(spark) > MAX_SPARK_POINTS:  # thin evenly, always keeping the latest point
        step = len(spark) / MAX_SPARK_POINTS
        spark = [spark[int(i * step)] for i in range(MAX_SPARK_POINTS - 1)] + [spark[-1]]

    return {
        "id": cfg["id"],
        "label": cfg["label"],
        "sentence": cfg.get("sentence") or f"The {cfg['label']}",
        "in_brief": cfg.get("in_brief", True),
        "why": cfg.get("why", ""),
        "value": last_v,
        "value_text": fmt.format(last_v),
        "prior_text": fmt.format(prior[1]),
        "as_of": last_d.isoformat(),
        "as_of_label": as_of_label(last_d, freq),
        "frequency": freq,
        "direction": direction,
        "change_text": change_text,
        "delta_text": ("No change" if flat else ("▲ " if direction == "up" else "▼ ") + change_text) + f" {vs}",
        "period": period,
        "spark": [[d.isoformat(), round(v, 4)] for d, v in spark],
        "spark_start_label": as_of_label(spark[0][0], freq) if spark else "",
        "source_url": f"https://fred.stlouisfed.org/series/{cfg['id']}",
    }


def brief_sentence(ind: dict) -> str:
    if ind["direction"] == "flat":
        return f"{ind['sentence']} was unchanged at {ind['value_text']}."
    verb = "rose" if ind["direction"] == "up" else "fell"
    return f"{ind['sentence']} {verb} {ind['change_text']} {ind['period']}, to {ind['value_text']}."


# ---------------------------------------------------------------- articles

def recent(items: list[dict], days: int, now: datetime) -> list[dict]:
    since = now - timedelta(days=days)
    out = [it for it in items if it.get("published") and it["published"] >= since]
    return sorted(out, key=lambda it: it["published"], reverse=True)


def to_record(it: dict, source_name: str, official: bool) -> dict:
    return {
        "title": it["title"],
        "url": it["url"],
        "summary": it.get("summary", ""),
        "source_name": it.get("publisher") or source_name,
        "official": official,
        "published": it["published"].strftime("%Y-%m-%d"),
    }


def official_list(section: dict, now: datetime, failures: list) -> list[dict]:
    out = []
    for src in section.get("sources", []):
        try:
            items = recent(fetch_rss(src["url"]), src.get("days", section.get("days", 14)), now)
        except Exception as exc:
            failures.append(f"{src['name']}: {exc}")
            continue
        kw = phrase_regex(src.get("include_keywords", []))
        if kw:
            items = [it for it in items if kw.search(it["title"] + " " + it.get("summary", ""))]
        if src.get("latest_only"):
            items = items[:1]
        elif src.get("max_items"):
            items = items[: src["max_items"]]
        out += [to_record(it, src["name"], True) for it in items]
    return sorted(out, key=lambda r: r["published"], reverse=True)


def commentary_list(section: dict, now: datetime, failures: list) -> list[dict]:
    neutral = Neutrality()
    news_cfg = load_yaml("sources.yaml")
    exclude_re = phrase_regex(news_cfg.get("exclude_titles", []))
    blocked = {p.lower() for p in news_cfg.get("exclude_publishers", [])}
    allowed = tuple(news_cfg.get("allowed_publisher_domains", []))
    kw = phrase_regex(section.get("keywords", []))
    skip = phrase_regex(section.get("exclude_keywords", []))

    def load(src):
        try:
            if src["type"] == "google_news":
                return src, fetch_google_news(src["query"])
            return src, fetch_rss(src["url"])
        except Exception as exc:
            return src, exc

    out, seen = [], set()
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(load, section.get("sources", [])))
    for src, res in results:
        if isinstance(res, Exception):
            failures.append(f"{src['name']}: {res}")
            continue
        for it in recent(res, section.get("days", 7), now):
            text = it["title"] + " " + it.get("summary", "")
            key = re.sub(r"\W+", " ", it["title"].lower()).strip()
            if key in seen or (kw and not kw.search(text)):
                continue
            if (exclude_re and exclude_re.search(it["title"])) or (skip and skip.search(text)):
                continue
            if src["type"] == "google_news":
                host = it.get("publisher_url", "").lower().rstrip("/")
                if it.get("publisher", "").lower() in blocked or (allowed and host and not host.endswith(allowed)):
                    continue
            if not neutral.ok(it, allow_opinion=True):
                continue
            seen.add(key)
            out.append(to_record(it, src["name"], False))
    out.sort(key=lambda r: r["published"], reverse=True)
    return out[: section.get("max_items", 12)]


# ---------------------------------------------------------------- main

def main() -> int:
    cfg = load_yaml("markets.yaml")
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=800)).date()  # enough history for year-over-year and trends
    previous = json.loads(OUT_FILE.read_text(encoding="utf-8")) if OUT_FILE.exists() else {}
    prev_by_id = {i["id"]: i for i in previous.get("indicators", [])}
    failures: list[str] = []

    def load(ind_cfg):
        try:
            return ind_cfg, fetch_fred(ind_cfg["id"], start)
        except Exception as exc:
            return ind_cfg, exc

    indicators = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for ind_cfg, res in pool.map(load, cfg.get("indicators", [])):
            if isinstance(res, Exception):
                failures.append(f"{ind_cfg['label']}: {res}")
                if ind_cfg["id"] in prev_by_id:  # keep last good value rather than a gap
                    indicators.append(dict(prev_by_id[ind_cfg["id"]], stale=True))
                continue
            try:
                indicators.append(build_indicator(ind_cfg, res, cfg.get("sparkline_days", 92)))
            except Exception as exc:
                failures.append(f"{ind_cfg['label']}: {exc}")

    data = {
        "fetched_at": now.isoformat(timespec="seconds"),
        "indicators": indicators,
        "brief": [brief_sentence(i) for i in indicators if i.get("in_brief", True) and not i.get("stale")],
        "releases": official_list(cfg.get("releases", {}), now, failures),
        "fed": official_list(cfg.get("fed", {}), now, failures),
        "commentary": commentary_list(cfg.get("commentary", {}), now, failures),
        "failures": failures,
    }
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for f in failures:
        print(f"  ✗ {f}", file=sys.stderr)
    print(f"{len(indicators)} indicators, {len(data['releases'])} releases, {len(data['fed'])} Fed items, "
          f"{len(data['commentary'])} commentary. {len(failures)} problem(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
