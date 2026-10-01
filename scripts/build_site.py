"""Build the static website from data/items.json and the config files.

Usage:  python scripts/build_site.py      (writes to ./site)
"""
from __future__ import annotations

import json
import shutil
from collections import OrderedDict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site"
TZ = ZoneInfo("America/New_York")
SITE_NAME = "Financial Literacy News Hub"
ORG_NAME = "Sokolov-Miller Family Financial & Life Skills Center"
THIS_WEEK_DAYS = 7


def load_yaml(name: str) -> dict:
    path = ROOT / "config" / name
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}


def pretty_date(iso: str | None) -> str:
    if not iso:
        return ""
    d = date.fromisoformat(str(iso)[:10])
    return d.strftime("%b %-d, %Y")


def load_json(name: str, default):
    path = ROOT / "data" / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


SPARK_W, SPARK_H, SPARK_PAD = 240, 56, 6


def sparkline(points: list, fmt: str, label: str) -> tuple[str, str]:
    """Inline SVG trend line (2px, muted) with the latest point marked in the accent color.
    Returns (svg, json of [x, y, date label, value label] for the hover tooltip)."""
    if len(points) < 2:
        return "", "[]"
    values = [v for _, v in points]
    lo, hi = min(values), max(values)
    if hi == lo:  # flat series: draw it through the middle
        lo, hi = lo - 1, hi + 1
    span = hi - lo
    n = len(points) - 1
    xy = [(SPARK_PAD + i * (SPARK_W - 2 * SPARK_PAD) / n,
           SPARK_PAD + (1 - (v - lo) / span) * (SPARK_H - 2 * SPARK_PAD)) for i, (_, v) in enumerate(points)]
    path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in xy)
    lx, ly = xy[-1]
    first_d, last_d = pretty_date(points[0][0]), pretty_date(points[-1][0])
    aria = (f"{label}: {fmt.format(values[0])} on {first_d} to {fmt.format(values[-1])} on {last_d}; "
            f"low {fmt.format(lo)}, high {fmt.format(hi)}")
    svg = (f'<svg viewBox="0 0 {SPARK_W} {SPARK_H}" width="{SPARK_W}" height="{SPARK_H}" role="img" '
           f'aria-label="{html_escape(aria)}">'
           f'<path d="{path}" class="spark-line"/>'
           f'<circle class="spark-focus" r="4" cx="{lx:.1f}" cy="{ly:.1f}" hidden/>'
           f'<circle class="spark-last" r="4" cx="{lx:.1f}" cy="{ly:.1f}"/></svg>')
    tips = [[round(x, 1), round(y, 1), pretty_date(d), fmt.format(v)] for (x, y), (d, v) in zip(xy, points)]
    return svg, json.dumps(tips)


def html_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;"))


def week_start(iso: str) -> date:
    d = date.fromisoformat(iso)
    return d - timedelta(days=d.weekday())  # Monday


def main() -> None:
    topics_cfg = load_yaml("topics.yaml")
    topics = topics_cfg["topics"]
    topic_by_id = {t["id"]: t for t in topics}
    resources = load_yaml("resources.yaml").get("groups", [])

    items = json.loads((ROOT / "data" / "items.json").read_text(encoding="utf-8"))
    last_run_path = ROOT / "data" / "last_run.json"
    last_run = json.loads(last_run_path.read_text()) if last_run_path.exists() else {}

    now = datetime.now(TZ)
    today = now.date()

    for it in items:
        it["topics"] = [t for t in it["topics"] if t in topic_by_id] or [topics[0]["id"]]
        it["primary"] = it["topics"][0]
        it["date_label"] = pretty_date(it["published"])
    cutoff = (today - timedelta(days=THIS_WEEK_DAYS)).isoformat()

    def rank(it):  # official sources, then urgent, then newest
        return (not it["official"], not it["urgent"], _neg_date(it["published"]))

    this_week = [it for it in items if it["published"] >= cutoff]
    sections = []
    for t in topics:
        group = sorted((it for it in this_week if it["primary"] == t["id"]), key=rank)
        sections.append({
            "topic": t,
            "official": [it for it in group if it["official"]],
            "news": [it for it in group if not it["official"]],
            "count": len(group),
        })

    alerts = sorted((it for it in this_week if it["urgent"]), key=rank)[:8]

    # Upcoming dates from official rules/notices (effective dates, comment deadlines).
    key_dates = []
    for it in items:
        for field, label in (("effective_on", "Takes effect"), ("comments_close_on", "Comments due")):
            if it.get(field) and it[field] >= today.isoformat():
                key_dates.append({"date": it[field], "date_label": pretty_date(it[field]),
                                  "label": label, "item": it})
    key_dates.sort(key=lambda k: k["date"])

    tips = sorted(load_yaml("tips.yaml").get("tips") or [],
                  key=lambda t: str(t.get("added", "")), reverse=True)

    archive = OrderedDict()
    for it in sorted(items, key=lambda i: i["published"], reverse=True):
        ws = week_start(it["published"])
        archive.setdefault(ws, []).append(it)
    archive_weeks = [{"label": f"Week of {ws.strftime('%B %-d, %Y')}", "id": f"w{ws.isoformat()}",
                      "entries": sorted(group, key=rank)} for ws, group in archive.items()]

    # ---- Market Watch
    markets = load_json("markets.json", {})
    fmts = {i["id"]: i.get("format", "{:,.2f}") for i in load_yaml("markets.yaml").get("indicators", [])}
    for ind in markets.get("indicators", []):
        ind["spark_svg"], ind["spark_json"] = sparkline(ind.get("spark", []), fmts.get(ind["id"], "{:,.2f}"),
                                                        ind["label"])
    for key in ("releases", "fed", "commentary"):
        for r in markets.get(key, []):
            r["date_label"] = pretty_date(r.get("published"))

    # ---- Policy
    policy = load_json("policy.json", {})
    states = policy.get("states", {})
    for p in policy.get("items", []):
        p["date_label"] = pretty_date(p.get("last_action_date"))
        p["status_class"] = ("enacted" if p["status"] == "enacted"
                             else "passed" if p["status_label"].startswith("Passed") else "progress")
    by_date = sorted(policy.get("items", []), key=lambda p: p.get("last_action_date", ""), reverse=True)
    official = [p for p in by_date if p.get("kind") == "official"]
    news = [p for p in by_date if p.get("kind") == "news"]
    for p in news:
        p["status_class"] = ("enacted" if p["status"] == "enacted"
                             else "passed" if "advancing" in p["status_label"] else "progress")
    policy_view = {
        "default_state": policy.get("default_state", "PA"),
        "state_options": sorted(((c, n) for c, n in states.items() if c != "US"), key=lambda x: x[1]),
        # USA.gov has an official page for every state, with links to its legislature and agencies.
        "state_links": {c: "https://www.usa.gov/states/" + n.lower().replace(" ", "-")
                        for c, n in states.items() if c != "US"},
        "groups": [
            {"status": "enacted", "title": "Enacted", "icon": "✅",
             "note": "Signed into law or published as a final rule.",
             "entries": [p for p in official if p["status"] == "enacted"]},
            {"status": "in_progress", "title": "In progress", "icon": "⏳",
             "note": "Bills moving through Congress and proposed rules open or recently open for comment.",
             "entries": [p for p in official if p["status"] != "enacted"]},
        ],
        "news": news,
    }

    env = Environment(loader=FileSystemLoader(ROOT / "templates"),
                      autoescape=select_autoescape(["html"]))
    common = {
        "site_name": SITE_NAME,
        "org_name": ORG_NAME,
        "topics": topics,
        "topic_by_id": topic_by_id,
        "updated_label": now.strftime("%A, %B %-d, %Y at %-I:%M %p ET"),
        "week_label": f"{pretty_date((today - timedelta(days=THIS_WEEK_DAYS)).isoformat())} – {pretty_date(today.isoformat())}",
        "failures": last_run.get("failures", []),
        "sidebar_links": [l for g in resources for l in g["links"] if l.get("sidebar")],
    }

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    shutil.copytree(ROOT / "static", OUT / "static")

    pages = {
        "index.html": ("index.html", dict(page="home", sections=sections, alerts=alerts,
                                          tips=tips[:3], key_dates=key_dates[:8],
                                          week_total=len(this_week))),
        "archive.html": ("archive.html", dict(page="archive", weeks=archive_weeks, total=len(items))),
        "markets.html": ("markets.html", dict(page="markets", markets=markets)),
        "policy.html": ("policy.html", dict(page="policy", policy=policy_view)),
        "resources.html": ("resources.html", dict(page="resources", groups=resources, tips=tips)),
    }
    for out_name, (tpl, ctx) in pages.items():
        html = env.get_template(tpl).render(**common, **ctx)
        (OUT / out_name).write_text(html, encoding="utf-8")

    (OUT / ".nojekyll").write_text("")
    print(f"Built {len(pages)} pages → {OUT}  ({len(this_week)} items this week, {len(items)} total)")


def _neg_date(iso: str) -> int:
    return -int(iso.replace("-", ""))


if __name__ == "__main__":
    main()
