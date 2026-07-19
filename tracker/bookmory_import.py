"""One-time import of a Bookmory backup into reading/log.json.

Bookmory's backup.zip contains new_bookmory.db, a sembast_sqflite store:
sqlite table `entry` with store='books' rows whose `value` is a JSON book.
Each book carries `reads` (one per read-through) and each read carries
`page_log_list` (cumulative page + ms timestamp) — which maps 1:1 onto
our "YYYY-MM-DD <cumulative page>" sessions.

Rules (per TODO design):
- NOT_STARTED shelf books (no reads) are skipped.
- DONE -> finished, GIVE_UP -> abandoned, READING -> reading.
- star (0.5 steps) -> rating when > 0.
- page counts: prefer real_total_page, fall back total_page; the pair
  total_page=100 / real_total_page=0 is Bookmory's "unknown" default and
  is treated as no data (the page-count chain fills it later).
- reads with no page logs get one synthesized session at the finish
  (or last-updated) date.
- merge by title|author cache_key: existing log entries are never
  overwritten; report everything.
- Bookmory cover-image URLs seed lists/covers-cache.json for keys not
  already cached (saves ~130 iTunes lookups on first build).
"""
from __future__ import annotations

import json
import re
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .reading_gen import (Book, LOG_PATH, RESERVED_SLUGS, dump_log,
                          load_log, slugify)
from . import lists_gen

TZ = ZoneInfo("America/Chicago")

STATUS_MAP = {"DONE": "finished", "GIVE_UP": "abandoned",
              "READING": "reading"}


def _ms2date(ms) -> str | None:
    if not ms:
        return None
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(TZ)
    return dt.strftime("%Y-%m-%d")


def _load_books(path: Path) -> list[dict]:
    """Read the raw Bookmory book dicts from a backup.zip or the db."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf, \
                tempfile.TemporaryDirectory() as td:
            db = Path(td) / "new_bookmory.db"
            db.write_bytes(zf.read("new_bookmory.db"))
            return _load_books(db)
    con = sqlite3.connect(path)
    try:
        rows = con.execute("SELECT value FROM entry WHERE store='books' "
                           "AND deleted IS NOT 1").fetchall()
    finally:
        con.close()
    return [json.loads(v) for (v,) in rows]


def _ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _clean_title(b: dict) -> str:
    """Strip Goodreads-style series suffixes: "Fetch-22 (Dog Man #8)"."""
    return re.sub(r"\s*\([^()]*#\d+[^()]*\)\s*$", "",
                  _ws(b.get("title") or ""))


def _author(b: dict) -> str:
    # manually-added books leave `author` empty but fill `authors`;
    # fields can carry stray double spaces ("Tom  Lin")
    return (_ws(b.get("author") or "")
            or ", ".join(_ws(a) for a in b.get("authors") or []))


def _page_count(read: dict, book: dict) -> int | None:
    for src in (read, book):
        real = src.get("real_total_page") or 0
        total = src.get("total_page") or 0
        if real > 0:
            return int(real)
        if total > 0 and total != 100:  # 100/0 = Bookmory's unknown default
            return int(total)
    return None


def _sessions(read: dict) -> list[str]:
    # last log wins within a day (pages are cumulative)
    by_day: dict[str, int] = {}
    for pl in sorted(read.get("page_log_list") or [],
                     key=lambda p: p.get("created_at") or 0):
        day = _ms2date(pl.get("created_at"))
        page = int(round(pl.get("page") or 0))
        if day and page > 0:
            by_day[day] = page
    return [f"{day} {page}" for day, page in sorted(by_day.items())]


def run(path: Path, log=print) -> int:
    raw = _load_books(path)
    rlog = load_log(LOG_PATH)
    existing_keys = {b.cache_key for b in rlog.books}
    used_slugs = set(RESERVED_SLUGS) | {b.slug for b in rlog.books}

    imported: list[Book] = []
    skipped_shelf = skipped_existing = synthesized = no_pages = 0

    for b in raw:
        reads = b.get("reads") or []
        if not reads:
            skipped_shelf += 1
            continue
        title = _clean_title(b)
        author = _author(b)
        for read in sorted(reads, key=lambda r: r.get("nth") or 0):
            status = STATUS_MAP.get(read.get("status"))
            if not status:
                log(f"  ? unknown read status {read.get('status')!r} "
                    f"on {title!r} — skipped")
                continue
            book = Book(title=title, author=author, status=status)
            if book.cache_key in existing_keys:
                skipped_existing += 1
                log(f"  = already in log, skipped: {title}")
                break
            star = read.get("star") or 0
            if star > 0:
                book.rating = float(star)
            book.page_count = _page_count(read, b)
            if book.page_count is None:
                no_pages += 1
            book.sessions = _sessions(read)
            book.started = _ms2date(read.get("start"))
            if status in ("finished", "abandoned"):
                book.finished = (_ms2date(read.get("end"))
                                 or _ms2date(read.get("updated_at")))
            if not book.started and book.sessions:
                book.started = book.sessions[0].split()[0]
            if not book.sessions:
                page = int(round(read.get("page") or 0)) or book.page_count
                day = book.finished or _ms2date(read.get("updated_at"))
                if page and day:
                    book.sessions = [f"{day} {page}"]
                    synthesized += 1
            slug = base = slugify(title)
            n = 2
            while slug in used_slugs:
                slug = f"{base}-{n}"
                n += 1
            book.slug = slug
            used_slugs.add(slug)
            existing_keys.add(book.cache_key)
            imported.append(book)

    imported.sort(key=lambda b: (b.started or b.finished or "9999",
                                 b.title.lower()))
    rlog.books.extend(imported)
    LOG_PATH.write_text(dump_log(rlog), encoding="utf-8")

    # seed the covers cache with Bookmory's own cover URLs
    covers = lists_gen.load_cache()
    seeded = 0
    by_key = {}
    for b in raw:
        k = f"{_clean_title(b).lower()}|{_author(b).lower()}"
        by_key[k] = b
    for book in imported:
        img = (by_key.get(book.cache_key, {}).get("image") or "").strip()
        if img.startswith("http") and book.cache_key not in covers:
            covers[book.cache_key] = {
                "cover_url": img,
                "matched": f"{book.title} — {book.author}",
                "source": "bookmory",
            }
            seeded += 1
    if seeded:
        lists_gen.save_cache(covers)

    log(f"bookmory import: {len(imported)} book(s) imported, "
        f"{skipped_shelf} not-started shelf skipped, "
        f"{skipped_existing} already in log, "
        f"{synthesized} session(s) synthesized, "
        f"{no_pages} without a page count, "
        f"{seeded} cover(s) seeded -> {LOG_PATH}")
    return len(imported)
