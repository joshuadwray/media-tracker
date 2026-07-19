"""Book reading log: calendar + per-book pages from reading/log.json.

  python -m tracker reading

Reads reading/log.json (written by docs/reading/log.html or by hand),
resolves page counts (manual override > pagecount cache > ISBN bridge
from the lists covers cache > iTunes lookup > Open Library median) and
writes docs/reading/index.html (calendar) plus docs/reading/<slug>.html
(one page per book). docs/reading/log.html is hand-written and is NEVER
touched by this module.

Session lines are "YYYY-MM-DD <page reached>" (cumulative). Pages/day is
the delta vs the previous session; a lower page than the previous one is
treated as a correction (delta 0, warned at build). A finished book
whose last session is short of the page count has the remainder credited
to the finish date.

Page counts found via iTunes artwork URLs piggyback on the lists covers
cache: the artwork filename embeds the ISBN-13, which the Open Library
editions API turns into a page count — zero iTunes calls for any book
that already appears on a list.
"""
from __future__ import annotations

import calendar as _calendar
import html
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from . import lists_gen

ROOT = Path(__file__).resolve().parent.parent
READING_DIR = ROOT / "reading"
LOG_PATH = READING_DIR / "log.json"
PAGECACHE_PATH = READING_DIR / "pagecount-cache.json"
OUT_DIR = ROOT / "docs" / "reading"

OL_ISBN_URL = "https://openlibrary.org/isbn/{}.json"
ITUNES_LOOKUP_URL = "https://itunes.apple.com/lookup"
ISBN13_RE = re.compile(r"(97[89]\d{10})")
LDJSON_RE = re.compile(
    r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.S)
# books.apple.com serves the JSON-LD (with numberOfPages) only to
# browser-looking user agents; the plain project UA gets a stub page.
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/605.1.15 (KHTML, like Gecko) "
              "Version/17.4 Safari/605.1.15")
RESERVED_SLUGS = {"index", "log"}
STATUSES = {"reading", "finished", "abandoned"}

# Fixed key order for books; the JS stringifier in docs/reading/log.html
# must emit byte-identical output (indent 2, same order, trailing \n).
BOOK_KEYS = ("title", "author", "slug", "status", "rating", "page_count",
             "started", "finished", "sessions")


@dataclass
class Book:
    title: str
    author: str = ""
    slug: str = ""
    status: str = "reading"
    rating: float | None = None
    page_count: int | None = None  # manual override only
    started: str | None = None
    finished: str | None = None
    sessions: list = field(default_factory=list)  # "YYYY-MM-DD <page>"

    @property
    def cache_key(self) -> str:
        return f"{self.title.strip().lower()}|{self.author.strip().lower()}"

    def parsed_sessions(self) -> list:
        """[(date, page), ...] in file order. Raises on bad lines."""
        out = []
        for raw in self.sessions:
            m = re.fullmatch(r"(\d{4}-\d{2}-\d{2}) (\d+)", str(raw))
            if not m:
                raise ValueError(
                    f"{self.slug}: bad session {raw!r} "
                    "(want 'YYYY-MM-DD <page>')")
            out.append((date.fromisoformat(m.group(1)), int(m.group(2))))
        return out

    def last_page(self) -> int:
        pages = [p for _, p in self.parsed_sessions()]
        return max(pages) if pages else 0


@dataclass
class ReadingLog:
    settings: dict
    books: list

    @property
    def daily_goal(self) -> int:
        return int(self.settings.get("daily_goal_pages") or 0)


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.strip().lower()).strip("-")
    return s or "book"


def load_log(path: Path = LOG_PATH) -> ReadingLog:
    data = json.loads(path.read_text(encoding="utf-8"))
    books = []
    for raw in data.get("books") or []:
        book = Book(title=str(raw.get("title") or ""),
                    author=str(raw.get("author") or ""),
                    slug=str(raw.get("slug") or ""),
                    status=str(raw.get("status") or "reading"),
                    rating=raw.get("rating"),
                    page_count=raw.get("page_count"),
                    started=raw.get("started"),
                    finished=raw.get("finished"),
                    sessions=list(raw.get("sessions") or []))
        if not book.title:
            raise ValueError(f"book missing a title: {raw!r}")
        if not book.slug:
            book.slug = slugify(book.title)
        if book.slug in RESERVED_SLUGS:
            raise ValueError(f"{book.title!r}: slug {book.slug!r} is reserved")
        if book.status not in STATUSES:
            raise ValueError(f"{book.slug}: bad status {book.status!r}")
        book.parsed_sessions()  # validate
        books.append(book)
    slugs = [b.slug for b in books]
    dupes = {s for s in slugs if slugs.count(s) > 1}
    if dupes:
        raise ValueError(f"duplicate slugs: {sorted(dupes)}")
    return ReadingLog(settings=data.get("settings") or {}, books=books)


def dump_log(log: ReadingLog) -> str:
    """Canonical serialization; must match the JS stringifier byte-for-byte."""
    def num(v):
        # JS prints 4.0 as 4 — normalize integral floats so both sides agree
        if isinstance(v, float) and v.is_integer():
            return int(v)
        return v
    books = [{k: num(getattr(b, k)) for k in BOOK_KEYS} for b in log.books]
    data = {"settings": log.settings, "books": books}
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


# ----------------------------------------------------------- page counts

def load_pagecache(path: Path = PAGECACHE_PATH) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_pagecache(cache: dict, path: Path = PAGECACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=1, ensure_ascii=False,
                               sort_keys=True) + "\n", encoding="utf-8")


def isbn_from_cover_url(url: str) -> str | None:
    m = ISBN13_RE.search(url or "")
    return m.group(1) if m else None


def _ol_pages_by_isbn(session, isbn: str) -> int | None:
    resp = session.get(OL_ISBN_URL.format(isbn), timeout=30,
                       allow_redirects=True)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    pages = resp.json().get("number_of_pages")
    return int(pages) if pages else None


def _pages_from_ldjson(text: str) -> int | None:
    """numberOfPages from any schema.org JSON-LD block in an HTML page."""
    for block in LDJSON_RE.findall(text):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for obj in data if isinstance(data, list) else [data]:
            if isinstance(obj, dict) and obj.get("numberOfPages"):
                return int(obj["numberOfPages"])
    return None


def _apple_books_pages(session, isbn: str) -> int | None:
    """Apple's store page has page counts for new releases long before
    Open Library does — and iTunes matched the book already, so the
    ISBN->store-page hop stays within the same catalog."""
    resp = session.get(ITUNES_LOOKUP_URL,
                       params={"isbn": isbn, "country": "US"}, timeout=30)
    resp.raise_for_status()
    time.sleep(lists_gen.ITUNES_SPACING)
    url = next((r.get("trackViewUrl")
                for r in resp.json().get("results") or []
                if r.get("trackViewUrl")), None)
    if not url:
        return None
    page = session.get(url.split("?")[0], timeout=30,
                       headers={"User-Agent": BROWSER_UA})
    page.raise_for_status()
    return _pages_from_ldjson(page.text)


def _ol_pages_by_search(session, title: str, author: str) -> int | None:
    # Fielded search first, then the looser q= (OL indexes some titles
    # without their leading article — "Antidote" for "The Antidote").
    fielded = {"title": title, "limit": 10,
               "fields": "title,author_name,number_of_pages_median"}
    if author:
        fielded["author"] = author
    loose = {"q": f"{title} {author}".strip(), "limit": 10,
             "fields": "title,author_name,number_of_pages_median"}
    for params in (fielded, loose):
        resp = session.get(lists_gen.SEARCH_URL, params=params, timeout=30)
        resp.raise_for_status()
        time.sleep(lists_gen.OL_SPACING)
        for doc in resp.json().get("docs") or []:
            median = doc.get("number_of_pages_median")
            if median and lists_gen._author_ok(
                    author, *(doc.get("author_name") or [])):
                return int(median)
    return None


def resolve_page_count(book: Book, cache: dict, covers_cache: dict,
                       session=None, log=None) -> tuple:
    """-> (page_count or None, source str). Caches lookups incl. misses."""
    if book.page_count:
        return int(book.page_count), "manual"
    entry = cache.get(book.cache_key)
    if entry is not None:
        return entry.get("page_count"), entry.get("source") or "cache"
    if session is None:
        return None, "unresolved"

    isbn = None
    cover = covers_cache.get(book.cache_key)
    if cover and cover.get("source") == "itunes":
        isbn = isbn_from_cover_url(cover.get("cover_url") or "")
    if not isbn:
        hit = lists_gen._itunes_lookup(session, book.title, book.author)
        if hit:
            isbn = isbn_from_cover_url(hit.get("cover_url") or "")
            # piggyback: a book we looked up now has a cover for free
            covers_cache.setdefault(book.cache_key, hit)

    pages, source = None, None
    if isbn:
        pages = _ol_pages_by_isbn(session, isbn)
        if pages:
            source = "openlibrary-isbn"
        else:
            pages = _apple_books_pages(session, isbn)
            if pages:
                source = "apple-books"
    if not pages:
        pages = _ol_pages_by_search(session, book.title, book.author)
        if pages:
            source = "openlibrary-median"
    cache[book.cache_key] = {"page_count": pages, "isbn13": isbn,
                             "source": source, "matched": book.title}
    if log:
        log(f"  page count: {book.title!r} -> {pages or 'not found'}"
            f"{f' ({source})' if source else ''}")
    return pages, source or "unresolved"


# ------------------------------------------------------------- page math

def daily_pages(book: Book, page_count: int | None = None,
                warn=None) -> dict:
    """{date: pages read} for one book, from cumulative session deltas."""
    out: dict = {}
    prev = 0
    for day, page in book.parsed_sessions():
        delta = page - prev
        if delta < 0:
            if warn:
                warn(f"{book.slug}: session {day} p.{page} is below the "
                     f"previous page {prev} — treating as a correction")
            delta = 0
        out[day] = out.get(day, 0) + delta
        prev = max(prev, page)
    if (book.status == "finished" and book.finished
            and page_count and prev < page_count):
        fin = date.fromisoformat(book.finished)
        out[fin] = out.get(fin, 0) + (page_count - prev)
    return out


def pages_by_date(books: list, page_counts: dict, warn=None) -> tuple:
    """-> ({date: total pages}, {date: [Book, ...]})."""
    totals: dict = {}
    readers: dict = {}
    for book in books:
        for day, pages in daily_pages(book, page_counts.get(book.slug),
                                      warn=warn).items():
            totals[day] = totals.get(day, 0) + pages
            if pages > 0 and book not in readers.setdefault(day, []):
                readers[day].append(book)
    return totals, readers


def _streak(totals: dict, today: date) -> int:
    day = today if totals.get(today, 0) > 0 else today - timedelta(days=1)
    n = 0
    while totals.get(day, 0) > 0:
        n += 1
        day -= timedelta(days=1)
    return n


# ------------------------------------------------------------------ html

_CSS = """
:root { color-scheme: light dark;
  --line: rgba(128,128,128,.35); --mut: rgba(128,128,128,.85);
  --ok: #2e7d32; --accent: #1565c0; }
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
       sans-serif; margin: 0 auto; max-width: 860px; padding: 16px;
       line-height: 1.4; }
h1 { font-size: 1.35rem; margin: 0 0 2px; }
h2 { font-size: 1rem; margin: 22px 0 8px; text-transform: uppercase;
     letter-spacing: .04em; opacity: .65; }
.meta { font-size: .85rem; opacity: .6; }
a { color: var(--accent); } a.back { font-size: .85rem; }
.stats { display: flex; gap: 10px; margin: 14px 0; flex-wrap: wrap; }
.stat { border: 1px solid var(--line); border-radius: 10px;
        padding: 8px 14px; text-align: center; }
.stat .n { font-size: 1.25rem; font-weight: 700; }
.stat .l { font-size: .75rem; opacity: .65; }
.cur { display: flex; gap: 12px; align-items: center; margin: 10px 0;
       border: 1px solid var(--line); border-radius: 10px; padding: 10px; }
.cur img, .cur .noimg { width: 48px; aspect-ratio: 2 / 3; border-radius: 4px;
       border: 1px solid var(--line); object-fit: cover; flex: none; }
.cur .noimg { background: hsl(210,35%,32%); }
.cur .t { font-weight: 600; }
.cur .bar { height: 6px; border-radius: 3px; background: rgba(128,128,128,.2);
       margin-top: 6px; overflow: hidden; }
.cur .bar div { height: 100%; background: var(--accent); }
.cur .info { flex: 1; min-width: 0; }
.month { margin-bottom: 22px; }
.month h3 { font-size: .95rem; margin: 0 0 6px; }
.cal { display: grid; grid-template-columns: repeat(7, 1fr); gap: 4px; }
.dow { font-size: .68rem; text-align: center; opacity: .55;
       text-transform: uppercase; }
.day { border: 1px solid var(--line); border-radius: 6px; min-height: 64px;
       padding: 3px 4px; font-size: .72rem; position: relative; }
.day.blank { border: none; }
.day.goal { border-color: var(--ok); box-shadow: inset 0 0 0 1px var(--ok); }
.day .dn { opacity: .55; }
.day .pg { position: absolute; bottom: 2px; right: 4px; font-weight: 700; }
.day.goal .pg { color: var(--ok); }
.thumbs { display: flex; gap: 2px; margin-top: 2px; }
.thumbs img { width: 22px; aspect-ratio: 2 / 3; object-fit: cover;
       border-radius: 3px; }
.thumbs .dot { width: 22px; aspect-ratio: 2 / 3; border-radius: 3px;
       background: hsl(210,35%,40%); }
@media (min-width: 520px) { .day { min-height: 80px; font-size: .78rem; }
       .thumbs img, .thumbs .dot { width: 28px; } }
@media (min-width: 760px) { .day { min-height: 92px; }
       .thumbs img, .thumbs .dot { width: 34px; } }
.cover, .bignoimg { width: 140px; aspect-ratio: 2 / 3; border-radius: 8px;
       border: 1px solid var(--line); object-fit: cover; }
.bignoimg { display: flex; align-items: center; justify-content: center;
       text-align: center; padding: 10px; color: #fff; font-weight: 700; }
.head { display: flex; gap: 16px; margin: 14px 0; }
.stars { color: #e8a512; font-size: 1.2rem; letter-spacing: 1px; }
.stars .half { display: inline-block; width: .55em; overflow: hidden;
       vertical-align: bottom; }
table { border-collapse: collapse; margin-top: 8px; }
td, th { padding: 4px 12px 4px 0; text-align: left; font-size: .9rem;
       border-bottom: 1px dashed rgba(128,128,128,.25); }
.chart { display: flex; gap: 3px; align-items: flex-end; height: 90px;
       margin-top: 14px; }
.chart .b { flex: 1; max-width: 34px; background: var(--accent);
       border-radius: 3px 3px 0 0; min-height: 2px; }
.chart .b span { display: none; }
"""

_DOWS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def _page_head(title: str) -> list:
    e = html.escape
    return [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>{e(title)}</title>",
        f"<style>{_CSS}</style></head><body>",
    ]


def _cover_url(book: Book, covers_cache: dict) -> str | None:
    entry = covers_cache.get(book.cache_key)
    return lists_gen._entry_url(entry) if entry else None


def _stars(rating: float) -> str:
    full = int(rating)
    half = (rating - full) >= 0.5
    out = "★" * full
    if half:
        out += "<span class='half'>★</span>"
    out += "☆" * (5 - full - (1 if half else 0))
    return f"<span class='stars' title='{rating:g}/5'>{out}</span>"


def render_calendar(log: ReadingLog, page_counts: dict, covers_cache: dict,
                    today: date | None = None, warn=None) -> str:
    e = html.escape
    today = today or date.today()
    totals, readers = pages_by_date(log.books, page_counts, warn=warn)
    goal = log.daily_goal
    parts = _page_head("reading")
    parts.append("<a class='back' href='../lists/'>&larr; lists</a> &middot; "
                 "<a class='back' href='log.html'>log a session</a>")
    parts.append("<h1>Reading</h1>")

    reading_now = [b for b in log.books if b.status == "reading"]
    if reading_now:
        parts.append("<h2>Currently reading</h2>")
    for book in reading_now:
        pc = page_counts.get(book.slug)
        at = book.last_page()
        pct = min(100, round(at * 100 / pc)) if pc else 0
        cover = _cover_url(book, covers_cache)
        img = (f"<img src='{e(cover)}' alt='' loading='lazy'>" if cover
               else "<div class='noimg'></div>")
        prog = f"p.{at} / {pc} &middot; {pct}%" if pc else f"p.{at}"
        parts.append(
            f"<a class='cur' style='text-decoration:none;color:inherit' "
            f"href='{e(book.slug)}.html'>{img}<div class='info'>"
            f"<div class='t'>{e(book.title)}</div>"
            f"<div class='meta'>{e(book.author)} &middot; {prog}</div>"
            f"<div class='bar'><div style='width:{pct}%'></div></div>"
            "</div></a>")

    week_start = today - timedelta(days=6)
    week = sum(p for d, p in totals.items() if week_start <= d <= today)
    parts.append("<div class='stats'>"
                 f"<div class='stat'><div class='n'>{_streak(totals, today)}"
                 "</div><div class='l'>day streak</div></div>"
                 f"<div class='stat'><div class='n'>{totals.get(today, 0)}"
                 f"</div><div class='l'>pages today (goal {goal})</div></div>"
                 f"<div class='stat'><div class='n'>{week}</div>"
                 f"<div class='l'>this week (goal {goal * 7})</div></div>"
                 "</div>")

    months = sorted({(d.year, d.month) for d in totals}, reverse=True)
    cal = _calendar.Calendar(firstweekday=6)  # Sunday first
    for year, month in months:
        parts.append(f"<div class='month'><h3>"
                     f"{_calendar.month_name[month]} {year}</h3>"
                     "<div class='cal'>")
        parts.extend(f"<div class='dow'>{d}</div>" for d in _DOWS)
        for day in cal.itermonthdates(year, month):
            if day.month != month:
                parts.append("<div class='day blank'></div>")
                continue
            pages = totals.get(day, 0)
            cls = "day goal" if goal and pages >= goal else "day"
            thumbs = ""
            if pages > 0:
                tt = []
                for book in readers.get(day, [])[:3]:
                    cover = _cover_url(book, covers_cache)
                    tt.append(f"<img src='{e(cover)}' alt='' loading='lazy'>"
                              if cover else "<div class='dot'></div>")
                thumbs = f"<div class='thumbs'>{''.join(tt)}</div>"
            pg = f"<span class='pg'>{pages}</span>" if pages > 0 else ""
            parts.append(f"<div class='{cls}'>"
                         f"<span class='dn'>{day.day}</span>{thumbs}{pg}"
                         "</div>")
        parts.append("</div></div>")
    if not months:
        parts.append("<div class='meta'>no sessions logged yet — "
                     "<a href='log.html'>log one</a></div>")
    parts.append("</body></html>")
    return "".join(parts)


def render_book(book: Book, page_count: int | None, pc_source: str,
                covers_cache: dict, warn=None) -> str:
    e = html.escape
    parts = _page_head(book.title)
    parts.append("<a class='back' href='./'>&larr; calendar</a> &middot; "
                 "<a class='back' href='log.html'>log a session</a>")

    cover = _cover_url(book, covers_cache)
    hue = lists_gen._tile_hue(book.title)
    img = (f"<img class='cover' src='{e(cover)}' alt='{e(book.title)} cover'>"
           if cover else f"<div class='bignoimg' style='background:"
                         f"hsl({hue},35%,32%)'>{e(book.title)}</div>")
    bits = [f"<h1>{e(book.title)}</h1>"]
    if book.author:
        bits.append(f"<div class='meta'>{e(book.author)}</div>")
    if book.rating is not None:
        bits.append(f"<div style='margin-top:6px'>{_stars(book.rating)}</div>")
    status = book.status
    if book.started:
        status += f" &middot; started {e(book.started)}"
    if book.finished:
        status += f" &middot; finished {e(book.finished)}"
    bits.append(f"<div class='meta' style='margin-top:6px'>{status}</div>")
    if page_count:
        bits.append(f"<div class='meta'>{page_count} pages "
                    f"<span title='source'>({e(pc_source)})</span></div>")
    parts.append(f"<div class='head'>{img}<div>{''.join(bits)}</div></div>")

    per_day = daily_pages(book, page_count, warn=warn)
    sessions = book.parsed_sessions()
    if sessions:
        parts.append("<h2>Sessions</h2><table>"
                     "<tr><th>date</th><th>at page</th><th>pages</th></tr>")
        prev = 0
        for day, page in sessions:
            delta = max(0, page - prev)
            prev = max(prev, page)
            parts.append(f"<tr><td>{day}</td><td>{page}</td>"
                         f"<td>{delta}</td></tr>")
        parts.append("</table>")
        days = sorted(per_day)
        peak = max(per_day.values()) or 1
        bars = "".join(
            f"<div class='b' style='height:{max(2, round(per_day[d] * 100 / peak))}%'"
            f" title='{d}: {per_day[d]} pages'><span>{per_day[d]}</span></div>"
            for d in days)
        parts.append(f"<div class='chart'>{bars}</div>")
    parts.append("</body></html>")
    return "".join(parts)


# ----------------------------------------------------------------- build

def reading_links(log_path: Path = LOG_PATH) -> dict:
    """{'title|author': '../reading/<slug>.html'} for lists_gen tiles."""
    if not log_path.exists():
        return {}
    try:
        log = load_log(log_path)
    except (ValueError, json.JSONDecodeError):
        return {}
    return {b.cache_key: f"../reading/{b.slug}.html" for b in log.books}


def build_all(log_path: Path = LOG_PATH, out_dir: Path = OUT_DIR,
              cache_path: Path = PAGECACHE_PATH, fetch: bool = True,
              log=print) -> list:
    rlog = load_log(log_path)
    cache = load_pagecache(cache_path)
    covers_cache = lists_gen.load_cache()
    known, covers_known = len(cache), len(covers_cache)
    session = None
    if fetch:
        import requests
        session = requests.Session()
        session.headers["User-Agent"] = lists_gen.USER_AGENT

    page_counts, sources = {}, {}
    for book in rlog.books:
        try:
            pages, source = resolve_page_count(book, cache, covers_cache,
                                               session, log=log)
        except Exception as exc:  # noqa: BLE001 — leave uncached, retry later
            if log:
                log(f"  page-count lookup failed for {book.title!r}: {exc}")
            pages, source = None, "unresolved"
        page_counts[book.slug] = pages
        sources[book.slug] = source

    warn = (lambda msg: log(f"  WARNING: {msg}")) if log else None
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    index = out_dir / "index.html"
    index.write_text(render_calendar(rlog, page_counts, covers_cache,
                                     warn=warn), encoding="utf-8")
    written.append(index)
    for book in rlog.books:
        out = out_dir / f"{book.slug}.html"
        out.write_text(render_book(book, page_counts[book.slug],
                                   sources[book.slug], covers_cache),
                       encoding="utf-8")
        written.append(out)

    if len(cache) != known:
        save_pagecache(cache, cache_path)
        if log:
            log(f"cached {len(cache) - known} page-count lookup(s) "
                f"-> {cache_path}")
    if len(covers_cache) != covers_known:
        lists_gen.save_cache(covers_cache)
    if log:
        log(f"reading: {len(rlog.books)} book(s) -> {out_dir}")
    return written
