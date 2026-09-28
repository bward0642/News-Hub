"""Pull APPROVED staff notes and teaching tips from the Google Form's response sheet.

Staff submit through the Google Form; an admin ticks the "Approved" checkbox in the
linked Google Sheet. This script reads the sheet (published as CSV) and writes the
approved rows to data/staff_notes.json for build_site.py.

Usage:
  NOTES_CSV_URL=... python scripts/fetch_notes.py     (GitHub Actions: set as a secret)
  python scripts/fetch_notes.py --csv responses.csv   (local testing)

When run in GitHub Actions it sets the step output `changed=true|false`, so the
hourly job can skip rebuilding when nothing new was approved.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")  # quiet urllib3/LibreSSL noise on older Macs

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT_FILE = ROOT / "data" / "staff_notes.json"

# Column header -> field. A header matches when it contains the phrase (case-insensitive).
# Order matters: the first matching phrase wins, so specific phrases come first.
COLUMNS = [
    ("approved", "approved"),
    ("timestamp", "timestamp"),
    ("what are you adding", "kind"),
    ("short title", "title"),
    ("headline", "headline"),
    ("your note", "text"),
    ("note or tip", "text"),
    ("your name", "author"),
    ("name", "author"),
    ("pin", "pin"),
    ("link", "link"),
]
YES = {"true", "yes", "y", "x", "✓", "✔", "approved", "1"}


def map_headers(headers: list[str]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    taken = set()
    for i, h in enumerate(headers):
        low = h.strip().lower()
        for phrase, field in COLUMNS:
            if phrase in low and field not in taken:
                mapping[i] = field
                taken.add(field)
                break
    return mapping


def parse_date(raw: str) -> str:
    raw = (raw or "").strip()
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return datetime.now().strftime("%Y-%m-%d")


def parse_rows(text: str) -> dict:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return {"notes": [], "tips": []}
    mapping = map_headers(rows[0])
    if "approved" not in mapping.values():
        print("  ! No 'Approved' column found in the sheet. Nothing will be published "
              "until an admin adds one (see README).", file=sys.stderr)
    notes, tips = [], []
    for raw in rows[1:]:
        row = {field: (raw[i].strip() if i < len(raw) else "") for i, field in mapping.items()}
        if row.get("approved", "").lower() not in YES:
            continue
        text = row.get("text", "")
        if not text:
            continue
        date = parse_date(row.get("timestamp", ""))
        author = row.get("author", "")
        is_tip = "tip" in row.get("kind", "").lower() or (not row.get("link") and bool(row.get("title")))
        if is_tip:
            tips.append({
                "title": row.get("title") or text.split(".")[0][:80],
                "tip": text,
                "link": row.get("link", ""),
                "author": author,
                "added": date,
            })
        elif row.get("link"):
            notes.append({
                "url": row["link"],
                "headline": row.get("headline", ""),
                "note": text,
                "author": author,
                "pin": row.get("pin", "").lower() in YES,
                "added": date,
            })
    return {"notes": notes, "tips": tips}


def set_output(changed: bool) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            fh.write(f"changed={'true' if changed else 'false'}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", help="read responses from a local CSV file instead of the web")
    args = parser.parse_args()

    if args.csv:
        text = Path(args.csv).read_text(encoding="utf-8")
    else:
        url = os.environ.get("NOTES_CSV_URL", "").strip()
        if not url:
            print("NOTES_CSV_URL is not set; skipping staff notes (the form isn't connected yet).")
            set_output(False)
            return 0
        try:
            resp = requests.get(url, timeout=(10, 30))
            resp.raise_for_status()
            resp.encoding = "utf-8"
            text = resp.text
            if text.lstrip().startswith("<"):
                raise ValueError("got a web page, not CSV. Is the sheet published as CSV?")
        except Exception as exc:
            # Keep whatever was published before so notes never disappear on a hiccup.
            print(f"  ! Could not read the notes sheet ({exc}). Keeping existing notes.", file=sys.stderr)
            set_output(False)
            return 0

    data = parse_rows(text)
    new = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
    old = OUT_FILE.read_text(encoding="utf-8") if OUT_FILE.exists() else ""
    changed = new != old
    if changed:
        OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUT_FILE.write_text(new, encoding="utf-8")
    print(f"{len(data['notes'])} approved note(s), {len(data['tips'])} approved tip(s). "
          f"{'Updated' if changed else 'No change'}.")
    set_output(changed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
