"""Shared helpers for the fetch scripts: HTTP, feed parsing, keyword matching, neutrality filter."""
from __future__ import annotations

import calendar
import hashlib
import html
import re
import warnings
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse

warnings.filterwarnings("ignore")  # quiet urllib3/LibreSSL noise on older Macs

import feedparser
import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh) FinancialLiteracyNewsHub/1.0",
    "Accept": "application/rss+xml, application/xml, application/json, text/xml, */*",
}
TIMEOUT = (10, 30)


def load_yaml(name: str) -> dict:
    path = ROOT / "config" / name
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}


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


def phrase_regex(words: list[str]) -> re.Pattern | None:
    words = [str(w) for w in words if w not in (None, "")]
    if not words:
        return None
    alts = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?<![\w-])(?:{alts})(?![\w-])", re.IGNORECASE)


# ---------------------------------------------------------------- fetchers

def fetch_rss(url: str) -> list[dict]:
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    items = []
    for e in feed.entries:
        link = urljoin(url, e.get("link") or "")  # some feeds use relative links
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


def fetch_google_news(query: str, window: str = "7d") -> list[dict]:
    url = ("https://news.google.com/rss/search?q=" + quote_plus(f"{query} when:{window}")
           + "&hl=en-US&gl=US&ceid=US:en")
    items = fetch_rss(url)
    for it in items:
        # Google titles look like "Headline - Publisher"; split the publisher off.
        head, sep, publisher = it["title"].rpartition(" - ")
        if sep and head:
            it["title"], it["publisher"] = head.rstrip(" -–—|"), publisher
        # Google's summary is just the headline again, so drop it.
        if it["summary"].startswith(it["title"][:40]):
            it["summary"] = ""
    return items


def fetch_federal_register(agency: str, since: datetime, doc_types: list[str] | None = None,
                           max_pages: int = 1) -> list[dict]:
    """Documents from one agency since `since`. doc_types: e.g. ["RULE", "PRORULE"]."""
    params = {
        "conditions[agencies][]": agency,
        "conditions[publication_date][gte]": since.strftime("%Y-%m-%d"),
        "order": "newest",
        "per_page": 100 if doc_types else 50,
        "fields[]": ["title", "html_url", "abstract", "publication_date", "type",
                     "effective_on", "comments_close_on", "agencies"],
    }
    if doc_types:
        params["conditions[type][]"] = doc_types
    url = "https://www.federalregister.gov/api/v1/documents.json"
    items = []
    for _ in range(max_pages):
        resp = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        for d in data.get("results", []):
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
                "agency_names": [a.get("name") for a in (d.get("agencies") or []) if a.get("name")],
            })
        url = data.get("next_page_url")
        params = None
        if not url:
            break
    return items


# ---------------------------------------------------------------- classify

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


# ---------------------------------------------------------------- neutrality

class Neutrality:
    """Keeps coverage non-partisan (config/neutrality.yaml).

    Drops items from blocked outlets, items whose headline uses charged or party-label
    language, and (unless allow_opinion) opinion pieces. Official sources are exempt;
    callers skip the check for them.
    """

    def __init__(self, cfg: dict | None = None):
        cfg = cfg if cfg is not None else load_yaml("neutrality.yaml")
        self.names = {n.lower() for n in cfg.get("blocked_outlets", [])}
        self.domains = tuple(d.lower().lstrip(".") for d in cfg.get("blocked_domains", []))
        self.charged = phrase_regex(cfg.get("charged_words", []))
        self.opinion = phrase_regex(cfg.get("opinion_markers", []))
        self.opinion_paths = tuple(p.lower() for p in cfg.get("opinion_url_paths", []))

    @staticmethod
    def _host(url: str) -> str:
        host = urlparse(url or "").netloc.lower()
        return host[4:] if host.startswith("www.") else host

    def reason(self, item: dict, allow_opinion: bool = False) -> str | None:
        """Why an item should be dropped, or None if it's fine."""
        publisher = (item.get("publisher") or item.get("source_name") or "").lower()
        if publisher in self.names:
            return f"blocked outlet ({publisher})"
        for url in (item.get("publisher_url", ""), item.get("url", "")):
            host = self._host(url)
            if host and self.domains and any(host == d or host.endswith("." + d) for d in self.domains):
                return f"blocked domain ({host})"
        title = item.get("title", "")
        if self.charged and (m := self.charged.search(title)):
            return f"charged wording ({m.group(0)})"
        if not allow_opinion:
            if self.opinion and self.opinion.search(title):
                return "opinion piece"
            path = urlparse(item.get("url", "")).path.lower()
            if any(p in path for p in self.opinion_paths):
                return "opinion piece"
        return None

    def ok(self, item: dict, allow_opinion: bool = False) -> bool:
        return self.reason(item, allow_opinion) is None
