"""Build the static website from data/items.json and the config files.

Usage:  python scripts/build_site.py      (writes to ./site)
"""
from __future__ import annotations

import json
import os
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


def week_start(iso: str) -> date:
    d = date.fromisoformat(iso)
    return d - timedelta(days=d.weekday())  # Monday


def main() -> None:
    topics_cfg = load_yaml("topics.yaml")
    topics = topics_cfg["topics"]
    topic_by_id = {t["id"]: t for t in topics}
    notes_cfg = load_yaml("staff_notes.yaml")
    resources = load_yaml("resources.yaml").get("groups", [])

    items = json.loads((ROOT / "data" / "items.json").read_text(encoding="utf-8"))
    last_run_path = ROOT / "data" / "last_run.json"
    last_run = json.loads(last_run_path.read_text()) if last_run_path.exists() else {}

    # Attach staff notes (matched by link).
    notes_by_url = {n["url"].strip(): n for n in (notes_cfg.get("notes") or []) if n.get("url")}
    for it in items:
        it["topics"] = [t for t in it["topics"] if t in topic_by_id] or [topics[0]["id"]]
        it["primary"] = it["topics"][0]
        it["note"] = notes_by_url.get(it["url"])
        it["date_label"] = pretty_date(it["published"])

    now = datetime.now(TZ)
    today = now.date()
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
    pinned = [it for it in items if it["note"] and it["note"].get("pin")]

    # Upcoming dates from official rules/notices (effective dates, comment deadlines).
    key_dates = []
    for it in items:
        for field, label in (("effective_on", "Takes effect"), ("comments_close_on", "Comments due")):
            if it.get(field) and it[field] >= today.isoformat():
                key_dates.append({"date": it[field], "date_label": pretty_date(it[field]),
                                  "label": label, "item": it})
    key_dates.sort(key=lambda k: k["date"])

    tips = sorted(notes_cfg.get("tips") or [], key=lambda t: str(t.get("added", "")), reverse=True)

    archive = OrderedDict()
    for it in sorted(items, key=lambda i: i["published"], reverse=True):
        ws = week_start(it["published"])
        archive.setdefault(ws, []).append(it)
    archive_weeks = [{"label": f"Week of {ws.strftime('%B %-d, %Y')}", "id": f"w{ws.isoformat()}",
                      "entries": sorted(group, key=rank)} for ws, group in archive.items()]

    repo = os.environ.get("GITHUB_REPOSITORY")  # "owner/name" when running in GitHub Actions
    edit_notes_url = f"https://github.com/{repo}/edit/main/config/staff_notes.yaml" if repo else None
    run_url = f"https://github.com/{repo}/actions/workflows/weekly-update.yml" if repo else None

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
        "edit_notes_url": edit_notes_url,
        "run_url": run_url,
        "sidebar_links": [l for g in resources for l in g["links"] if l.get("sidebar")],
    }

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    shutil.copytree(ROOT / "static", OUT / "static")

    pages = {
        "index.html": ("index.html", dict(page="home", sections=sections, alerts=alerts,
                                          pinned=pinned, tips=tips[:3], key_dates=key_dates[:8],
                                          week_total=len(this_week))),
        "archive.html": ("archive.html", dict(page="archive", weeks=archive_weeks, total=len(items))),
        "resources.html": ("resources.html", dict(page="resources", groups=resources, tips=tips)),
        "about.html": ("about.html", dict(page="about")),
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
