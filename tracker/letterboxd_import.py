"""One-time Letterboxd data-export backfill into watching/log.json.

  python -m tracker letterboxd --import letterboxd-<user>-<date>.zip

The RSS feed only exposes ~50 recent entries, so history beyond that
comes from the account data export (Settings -> Import & Export):
diary.csv has date/title/year/rating/rewatch + a boxd.it shortlink,
reviews.csv the review texts, likes/films.csv film-level likes.

The CSV lacks what the RSS sync provides — slug, tmdb_id, poster —
so each new entry costs two requests: the boxd.it redirect resolves
to /<user>/film/<slug>/ and the film page's JSON-LD block carries the
600x900 poster URL + data-tmdb-id (both verified by probe).

Dedup against existing entries is by (title lowercased, watched date)
— CSV rows have no guid. Imported entries get a deterministic
"letterboxd-import-<md5-of-shortlink>" guid, safely outside the RSS
window (which is why RSS upserts can't collide with them). Idempotent:
a re-run skips every existing row and touches nothing.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import time
import zipfile
from pathlib import Path

from . import http
from .letterboxd_sync import LOG_PATH, dump_log, entry_key, load_log

FILM_URL = "https://letterboxd.com/film/{}/"
SLUG_RE = re.compile(r"/film/([^/]+)")
TMDB_RE = re.compile(r'data-tmdb-id="(\d+)"')
LD_RE = re.compile(r'<script type="application/ld\+json">\s*'
                   r"(?:/\*.*?\*/)?\s*(\{.*?\})\s*(?:/\*.*?\*/)?\s*"
                   r"</script>", re.S)
DELAY = 1.0  # be polite; ~2 requests per film


def _guid(uri: str) -> str:
    n = int(hashlib.md5(uri.encode()).hexdigest()[:12], 16)
    return f"letterboxd-import-{n}"


def _read_csv(zf: zipfile.ZipFile, name: str) -> list[dict]:
    with zf.open(name) as fh:
        return list(csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8")))


def _film_details(sess, slug: str) -> tuple[int | None, str | None]:
    """(tmdb_id, poster_url) from the film page."""
    resp = http.get(sess, FILM_URL.format(slug))
    resp.raise_for_status()
    html = resp.text
    m = TMDB_RE.search(html)
    tmdb_id = int(m.group(1)) if m else None
    poster = None
    m = LD_RE.search(html)
    if m:
        try:
            poster = json.loads(m.group(1)).get("image")
        except json.JSONDecodeError:
            pass
    return tmdb_id, poster


def run(path: Path, since: str = "2025-01-01", log=print) -> int:
    settings, films = load_log()
    existing = {((f.get("title") or "").lower(), f.get("watched"))
                for f in films}

    with zipfile.ZipFile(path) as zf:
        diary = _read_csv(zf, "diary.csv")
        reviews = {(r["Name"].lower(), r["Watched Date"]): r["Review"]
                   for r in _read_csv(zf, "reviews.csv")}
        likes = {(r["Name"].lower(), r["Year"])
                 for r in _read_csv(zf, "likes/films.csv")}

    rows = [r for r in diary
            if r["Watched Date"] >= since
            and (r["Name"].lower(), r["Watched Date"]) not in existing]
    log(f"letterboxd import: {len(rows)} new entr(ies) since {since} "
        f"({len(diary)} in diary.csv)")
    if not rows:
        return 0

    sess = http.session()
    slug_by_uri: dict[str, str] = {}
    details_by_slug: dict[str, tuple] = {}
    added = 0
    for r in rows:
        uri = r["Letterboxd URI"]
        slug = slug_by_uri.get(uri)
        if slug is None:
            time.sleep(DELAY)
            resp = http.get(sess, uri)
            m = SLUG_RE.search(resp.url)
            if not m:
                log(f"  ! {r['Name']}: no /film/ slug in {resp.url} — "
                    f"skipped")
                continue
            slug = slug_by_uri[uri] = m.group(1)
        if slug not in details_by_slug:
            time.sleep(DELAY)
            try:
                details_by_slug[slug] = _film_details(sess, slug)
            except Exception as exc:  # noqa: BLE001 — entry still worth keeping
                log(f"  ! {slug}: film page failed "
                    f"({type(exc).__name__}: {exc}) — no poster/tmdb")
                details_by_slug[slug] = (None, None)
        tmdb_id, poster = details_by_slug[slug]
        key = (r["Name"].lower(), r["Watched Date"])
        films.append({
            "title": r["Name"],
            "year": int(r["Year"]) if r["Year"] else None,
            "slug": slug,
            "watched": r["Watched Date"],
            "rating": float(r["Rating"]) if r["Rating"] else None,
            "rewatch": r["Rewatch"] == "Yes",
            "liked": (r["Name"].lower(), r["Year"]) in likes,
            "review": reviews.get(key) or None,
            "tmdb_id": tmdb_id,
            "poster_url": poster,
            "letterboxd_uri": uri,
            "guid": _guid(uri),
        })
        added += 1
        log(f"  + {r['Watched Date']} {r['Name']} ({slug})")

    films.sort(key=lambda f: (f.get("watched") or "",
                              entry_key(f.get("guid")) or 0),
               reverse=True)
    if settings.get("since", "") > since:
        settings["since"] = since
    LOG_PATH.write_text(dump_log(settings, films), encoding="utf-8")
    log(f"letterboxd import: added {added} -> {LOG_PATH} "
        f"({len(films)} film(s) on file)")
    return added
