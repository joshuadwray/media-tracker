"""One-way Letterboxd diary sync: RSS feed -> watching/log.json.

  python -m tracker letterboxd

Fetches the public diary RSS feed for the user named in
watching/log.json settings (letterboxd.com/<user>/rss/) and upserts
film entries watched on or after the settings "since" date. The feed
covers roughly the last ~50 diary entries, so entries that scroll out
of the feed window are left untouched — this is strictly one-way and
additive/corrective, never deleting.

Gotcha the upsert key handles: a plain watch has guid
"letterboxd-watch-<id>" but the SAME entry becomes
"letterboxd-review-<id>" if a review is added later — so entries are
keyed on the numeric id suffix, not the full guid.
"""
from __future__ import annotations

import html as _html
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from . import http

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "watching" / "log.json"

FEED_URL = "https://letterboxd.com/{}/rss/"

NS = {
    "letterboxd": "https://letterboxd.com",
    "tmdb": "https://themoviedb.org",
    "dc": "http://purl.org/dc/elements/1.1/",
}

DEFAULT_SETTINGS = {"letterboxd_user": "joshwray", "since": "2026-01-01"}

# Fixed key order; serialization must be byte-stable so an unchanged
# sync produces an unchanged file (idempotent runs, no bot-commit churn).
FILM_KEYS = ("title", "year", "slug", "watched", "rating", "rewatch",
             "liked", "review", "tmdb_id", "poster_url", "letterboxd_uri",
             "guid")

GUID_ID_RE = re.compile(r"(\d+)$")
IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"')
P_RE = re.compile(r"<p>(.*?)</p>", re.S)
TAG_RE = re.compile(r"<[^>]+>")


def load_log(path=LOG_PATH):
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("settings") or dict(DEFAULT_SETTINGS), \
            list(data.get("films") or [])
    return dict(DEFAULT_SETTINGS), []


def dump_log(settings, films):
    ordered = [{k: f.get(k) for k in FILM_KEYS} for f in films]
    data = {"settings": settings, "films": ordered}
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def entry_key(guid):
    """Numeric id shared by letterboxd-watch-<id> / letterboxd-review-<id>."""
    m = GUID_ID_RE.search(guid or "")
    return int(m.group(1)) if m else None


def fetch_feed(user):
    sess = http.session()
    resp = http.get(sess, FEED_URL.format(user))
    resp.raise_for_status()
    return resp.text


def _text(item, tag):
    el = item.find(tag, NS)
    return el.text if el is not None else None


def _parse_description(desc):
    """(poster_url, review) from the description CDATA html."""
    poster = None
    m = IMG_RE.search(desc or "")
    if m:
        poster = m.group(1)
    paras = []
    for p in P_RE.findall(desc or ""):
        if "<img" in p:
            continue
        text = _html.unescape(TAG_RE.sub("", p)).strip()
        if not text:
            continue
        if text.startswith("Watched on "):
            continue
        if text.startswith("This review may contain spoilers"):
            continue
        paras.append(text)
    review = "\n\n".join(paras) or None
    return poster, review


def parse_feed(xml_text):
    """(films, list_items_ignored). Bad individual items are skipped."""
    root = ET.fromstring(xml_text)
    films = []
    ignored = 0
    for item in root.iter("item"):
        guid = _text(item, "guid")
        try:
            watched = _text(item, "letterboxd:watchedDate")
            if not watched:  # list activity, not a diary entry
                ignored += 1
                continue
            link = (_text(item, "link") or "").strip()
            # rewatch links get a trailing viewing number
            # (/film/<slug>/2/), so take the segment after /film/
            m = re.search(r"/film/([^/]+)", link)
            slug = m.group(1) if m else None
            rating = _text(item, "letterboxd:memberRating")
            year = _text(item, "letterboxd:filmYear")
            tmdb_id = _text(item, "tmdb:movieId")
            poster, review = _parse_description(_text(item, "description"))
            films.append({
                "title": _text(item, "letterboxd:filmTitle"),
                "year": int(year) if year else None,
                "slug": slug,
                "watched": watched,
                "rating": float(rating) if rating else None,
                "rewatch": _text(item, "letterboxd:rewatch") == "Yes",
                "liked": _text(item, "letterboxd:memberLike") == "Yes",
                "review": review,
                "tmdb_id": int(tmdb_id) if tmdb_id else None,
                "poster_url": poster,
                "letterboxd_uri": link or None,
                "guid": guid,
            })
        except Exception as exc:  # noqa: BLE001 — one bad item shouldn't kill the run
            print(f"skipping feed item {guid!r}: {type(exc).__name__}: {exc}")
    return films, ignored


def merge(films, incoming, since):
    """Upsert incoming (watched >= since) into films. Returns
    (films, added, updated, unchanged, skipped_before_since)."""
    by_id = {entry_key(f.get("guid")): f for f in films}
    # data-export backfills have synthetic guids; match those by
    # title+date so an RSS entry upgrades them instead of duplicating
    imported = {((f.get("title") or "").lower(), f.get("watched")): f
                for f in films
                if (f.get("guid") or "").startswith("letterboxd-import-")}
    added = updated = unchanged = skipped = 0
    for inc in incoming:
        if inc["watched"] < since:
            skipped += 1
            continue
        key = entry_key(inc["guid"])
        cur = by_id.get(key)
        if cur is None:
            cur = imported.get(((inc["title"] or "").lower(),
                                inc["watched"]))
        if cur is None:
            films.append(inc)
            by_id[key] = inc
            added += 1
        elif {k: cur.get(k) for k in FILM_KEYS} != inc:
            cur.clear()
            cur.update(inc)
            updated += 1
        else:
            unchanged += 1
    films.sort(key=lambda f: (f.get("watched") or "",
                              entry_key(f.get("guid")) or 0),
               reverse=True)
    return films, added, updated, unchanged, skipped


def sync():
    settings, films = load_log()
    user = settings.get("letterboxd_user") or DEFAULT_SETTINGS["letterboxd_user"]
    since = settings.get("since") or DEFAULT_SETTINGS["since"]
    try:
        xml_text = fetch_feed(user)
        incoming, ignored = parse_feed(xml_text)
    except Exception as exc:  # noqa: BLE001
        print(f"letterboxd sync failed: {type(exc).__name__}: {exc}")
        return 1
    films, added, updated, unchanged, skipped = merge(films, incoming, since)
    out = dump_log(settings, films)
    old = LOG_PATH.read_text(encoding="utf-8") if LOG_PATH.exists() else None
    if out != old:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text(out, encoding="utf-8")
    print(f"letterboxd ({user}): added {added}, updated {updated}, "
          f"unchanged {unchanged}, skipped-before-since {skipped}, "
          f"list-items-ignored {ignored} — {len(films)} film(s) on file")
    return 0
