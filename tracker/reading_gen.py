"""Book reading log: the diary's data bundle, from reading/log.json.

  python -m tracker reading

Reads reading/log.json (written by docs/reading/log.html or by hand),
resolves page counts (manual override > pagecount cache > ISBN bridge
from the lists covers cache > iTunes lookup > Open Library median) and
writes docs/data/diary.json plus ~1KB shells at docs/reading/index.html,
list.html and <slug>.html. docs/reading/log.html is hand-written and is
NEVER touched by this module.

This module does NOT render the diary any more — docs/assets/diary.js
does, in the browser. The diary is human-authored: you type a session and
want to see it, and a CI run plus a Pages deploy (~56s measured) sat
between those two moments. So CI now contributes only what a browser
cannot do for itself — the network-bound cover and page-count lookups —
and the page draws itself from that bundle plus anything still sitting in
localStorage from a save seconds ago. Machine-authored pages (the film
diary in watching_gen, the watchlist dashboard) are still rendered here,
because nobody is sitting there waiting on them.

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

import requests

from . import lists_gen, site

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
RESERVED_SLUGS = {"index", "log", "list"}
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
        # still piggyback a cover lookup if missing from cache
        if session and book.cache_key not in covers_cache:
            hit = (lists_gen._itunes_lookup(session, book.title, book.author)
                   or lists_gen._openlibrary_lookup(session, book.title,
                                                    book.author))
            if hit:
                covers_cache[book.cache_key] = hit
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

    # Open Library goes down for hours at a time, and a raised timeout
    # here would skip the Apple Books hop below — the one source that
    # answers for the books OL is missing. Demote its failures to misses,
    # but remember them so a source-outage miss is never cached as fact.
    outages = []

    def _soft(what, fn, *args):
        try:
            return fn(*args)
        except requests.RequestException as exc:
            outages.append(what)
            if log:
                log(f"  {what} unavailable for {book.title!r}: {exc}")
            return None

    pages, source = None, None
    if isbn:
        pages = _soft("openlibrary", _ol_pages_by_isbn, session, isbn)
        if pages:
            source = "openlibrary-isbn"
        else:
            pages = _apple_books_pages(session, isbn)
            if pages:
                source = "apple-books"
    if not pages:
        pages = _soft("openlibrary search", _ol_pages_by_search, session,
                      book.title, book.author)
        if pages:
            source = "openlibrary-median"
    if pages or not outages:
        cache[book.cache_key] = {"page_count": pages, "isbn13": isbn,
                                 "source": source, "matched": book.title}
    if log:
        stale = (" — left uncached, sources were down"
                 if not pages and outages else "")
        log(f"  page count: {book.title!r} -> {pages or 'not found'}"
            f"{f' ({source})' if source else stale}")
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


def group_reads(books: list) -> tuple:
    """Group re-reads of the same book. -> (groups, base_of).

    A re-read is a separate log entry (slug <base>-2, -3, ...) with the
    same title|author; groups collects them by cache_key in file order.
    The first entry is the base read whose slug names the shared book
    page. base_of maps every slug to its base slug for link resolution.
    """
    groups: dict = {}
    for b in books:
        groups.setdefault(b.cache_key, []).append(b)
    base_of = {b.slug: reads[0].slug
               for reads in groups.values() for b in reads}
    return groups, base_of


# ------------------------------------------------------------------ html

_CSS = """
a.back { font-size: .85rem; }
.stats { display: flex; gap: 10px; margin: 14px 0; flex-wrap: wrap; }
.stat { border: 1px solid var(--line); border-radius: 10px;
        background: var(--surface); padding: 8px 14px;
        text-align: center; }
.stat .n { font-size: 1.25rem; font-weight: 700; }
.stat .l { font-size: .75rem; opacity: .65; }
.cur { display: flex; gap: 12px; align-items: center; margin: 10px 0;
       border: 1px solid var(--line); border-radius: 10px;
       background: var(--surface); padding: 10px; }
.cur img, .cur .noimg { width: 48px; aspect-ratio: 2 / 3; border-radius: 4px;
       border: 1px solid var(--line); object-fit: cover; flex: none; }
.cur .noimg { background: var(--teal); color: #fff; }
.cur .t { font-weight: 600; }
.cur .bar { height: 6px; border-radius: 3px; background: var(--surface-sunk);
       margin-top: 6px; overflow: hidden; }
.cur .bar div { height: 100%; background: var(--teal); }
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
.thumbs { display: flex; gap: 2px; margin-top: 2px; }
.thumbs img { width: 22px; aspect-ratio: 2 / 3; object-fit: cover;
       border-radius: 3px; }
.thumbs .dot { width: 22px; aspect-ratio: 2 / 3; border-radius: 3px;
       background: var(--teal); }
.thumbs .th { position: relative; display: block; }
.thumbs .film img { box-shadow: 0 0 0 1.5px var(--accent); }
.thumbs .more { width: 22px; aspect-ratio: 2 / 3; border-radius: 3px;
       background: var(--surface-sunk); color: var(--ink-soft);
       display: flex; align-items: center; justify-content: center;
       font-size: .6rem; font-weight: 700; }
.heart { color: var(--gold); }
.fchip { position: absolute; bottom: 2px; left: 50%;
       transform: translateX(-50%); background: rgba(16,34,28,.82);
       color: var(--gold); font-size: .58rem; font-weight: 700;
       padding: 0 4px; border-radius: 999px; white-space: nowrap; }
@media (min-width: 520px) { .day { min-height: 80px; font-size: .78rem; }
       .thumbs img, .thumbs .dot, .thumbs .more { width: 28px; } }
@media (min-width: 760px) { .day { min-height: 92px; }
       .thumbs img, .thumbs .dot, .thumbs .more { width: 34px; } }
.cover, .bignoimg { width: 140px; aspect-ratio: 2 / 3; border-radius: 8px;
       border: 1px solid var(--line); object-fit: cover; }
.bignoimg { display: flex; align-items: center; justify-content: center;
       text-align: center; padding: 10px; color: #fff; font-weight: 700; }
.head { display: flex; gap: 16px; margin: 14px 0; }
.stars { color: var(--amber); font-size: 1.2rem; letter-spacing: 1px; }
.stars .half { display: inline-block; width: .55em; overflow: hidden;
       vertical-align: bottom; }
table { border-collapse: collapse; margin-top: 8px; }
td, th { padding: 4px 12px 4px 0; text-align: left; font-size: .9rem;
       border-bottom: 1px dashed var(--line); }
.chart { display: flex; gap: 3px; align-items: flex-end; height: 90px;
       margin-top: 14px; }
.chart .b { flex: 1; max-width: 34px; background: var(--teal);
       border-radius: 3px 3px 0 0; min-height: 2px; }
.chart .b span { display: none; }
.vt { font-size: .85rem; margin: 4px 0 0; }
.vt strong { color: inherit; }
.dl { margin-top: 18px; }
.dl h3 { font-size: .95rem; margin: 18px 0 6px; }
.row { display: flex; gap: 10px; align-items: center; padding: 5px 0;
       border-bottom: 1px dashed var(--line); font-size: .9rem; }
.row img, .row .dot { width: 34px; aspect-ratio: 2 / 3; object-fit: cover;
       border-radius: 3px; flex: none; }
.row .dot { background: var(--teal); }
.row.film img { box-shadow: 0 0 0 1.5px var(--accent); }
.row .rt { flex: 1; min-width: 0; }
.row .rt .by { opacity: .6; }
.row .rm { flex: none; text-align: right; font-size: .82rem; opacity: .85;
       white-space: nowrap; }
.row .rm .stars { font-size: .95rem; }
.rowwrap { display: flex; align-items: center; gap: 8px;
       border-bottom: 1px dashed var(--line); }
.rowwrap .row { flex: 1; min-width: 0; border-bottom: none; }
.rowedit { font: inherit; font-size: .8rem; border: 1px solid var(--line);
       background: transparent; border-radius: 6px; padding: 2px 8px;
       cursor: pointer; color: var(--mut); flex: none; }
.mted { border: 1px solid var(--line); border-radius: 10px; padding: 10px;
       background: var(--surface); margin: 10px 0; font-size: .9rem;
       max-width: 480px; }
.mted label { display: block; font-size: .75rem; opacity: .65;
       margin: 8px 0 3px; }
.mted input, .mted select { font: inherit; padding: 8px;
       border-radius: 8px; border: 1px solid var(--line);
       background: transparent; width: 100%; color: inherit; }
.mted .g2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.mted .srow { display: flex; gap: 8px; margin-bottom: 6px; }
.mted .srow input { width: 5.5em; text-align: center; }
.mted .srow input[type=date] { flex: 1; width: auto; text-align: left; }
.mted button { font: inherit; padding: 8px 10px; border-radius: 8px;
       border: 1px solid var(--line); background: transparent;
       cursor: pointer; color: inherit; }
.mted button:disabled { opacity: .5; }
.mted .danger { color: var(--err); }
.mted .btnrow { display: flex; gap: 8px; margin-top: 10px; }
.mted .btnrow button { flex: 1; }
.edstatus { position: fixed; left: 0; right: 0; bottom: 0;
       padding: 10px 16px; background: rgba(20,25,22,.95); color: #F5F7F4;
       font-size: .9rem; z-index: 9; }
.edstatus:empty { display: none; }
.edstatus.ok { color: #A9D48F; } .edstatus.err { color: #F2A491; }
.readsec { margin-top: 26px; }
.readsec h2 a.back { font-size: .8rem; font-weight: 400; margin-left: 8px; }
.mnav { display: flex; justify-content: space-between; align-items: center;
       margin: 14px 0 6px; }
.mnav button { font: inherit; font-size: .85rem; padding: 6px 12px;
       border: 1px solid var(--line); border-radius: 8px;
       background: transparent; color: inherit; cursor: pointer; }
.mnav button:disabled { opacity: .35; cursor: default; }
.mnav select { font: inherit; font-size: .85rem; padding: 6px 8px;
       border: 1px solid var(--line); border-radius: 8px;
       background: transparent; color: inherit; cursor: pointer; }

"""


# Shared client-side editor for the generated diary pages. Written to
# docs/reading/edit.js by build_all so the serializer (which must stay
# byte-identical to dump_log above AND to the copy in the hand-written
# docs/reading/log.html) has a single generated source. Editors always
# fetch a FRESH reading/log.json from the GitHub Contents API — never
# the possibly-stale data baked into the page — then PUT the whole file
# back with the blob-sha conflict guard. Token: same localStorage
# mt_pat as log.html / add.html / lists/edit.html.
_EDIT_JS = r"""/* generated by tracker/reading_gen.py — do not edit */
(function () {
'use strict';
const REPO = 'joshuadwray/media-tracker';
const API = `https://api.github.com/repos/${REPO}/contents/`;

const BOOK_KEYS = ['title', 'author', 'slug', 'status', 'rating',
                   'page_count', 'started', 'finished', 'sessions'];
function serialize(d) {
  const books = d.books.map(b => {
    const o = {};
    for (const k of BOOK_KEYS) o[k] = b[k] === undefined ? null : b[k];
    return o;
  });
  return JSON.stringify({ settings: d.settings, books }, null, 2) + '\n';
}

const b64decode = s => new TextDecoder().decode(
  Uint8Array.from(atob(s.replace(/\n/g, '')), c => c.charCodeAt(0)));
function b64encode(s) {
  const bytes = new TextEncoder().encode(s);
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}
const today = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-` +
         String(d.getDate()).padStart(2, '0');
};
const dateSort = a =>
  a.sort((x, y) => x.slice(0, 10) < y.slice(0, 10) ? -1 :
                   x.slice(0, 10) > y.slice(0, 10) ? 1 : 0);

let statusEl = null;
function status(msg, cls) {
  if (!statusEl) {
    statusEl = document.createElement('div');
    document.body.append(statusEl);
  }
  statusEl.textContent = msg;
  statusEl.className = 'edstatus ' + (cls || '');
}

async function fetchLog() {
  const r = await fetch(API + 'reading/log.json');
  if (!r.ok) throw new Error('GitHub said ' + r.status);
  const blob = await r.json();
  const data = JSON.parse(b64decode(blob.content));
  data.settings = data.settings || {};
  data.books = data.books || [];
  return { sha: blob.sha, data };
}

async function putLog(sha, data) {
  const token = localStorage.getItem('mt_pat');
  if (!token) {
    status('no token in this browser \u2014 set one up under ' +
           '"token setup" on the log page first', 'err');
    return false;
  }
  try {
    const r = await fetch(API + 'reading/log.json', {
      method: 'PUT',
      headers: { 'Authorization': 'Bearer ' + token,
                 'Accept': 'application/vnd.github+json',
                 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: 'reading log (edit)',
                             content: b64encode(serialize(data)), sha }) });
    if (r.ok) {
      // Hand the just-saved log to the renderer so the diary shows it NOW
      // instead of after the ~1 min CI + Pages round trip. diary.js drops
      // this overlay once a build contains it.
      if (window.mtSavePending) window.mtSavePending(data);
      return true;
    }
    if (r.status === 409 || r.status === 422)
      status('conflict: the log changed since loading \u2014 reload ' +
             'and redo the edit', 'err');
    else {
      const resp = await r.json().catch(() => ({}));
      status(`GitHub said ${r.status}: ${resp.message || 'no detail'}`,
             'err');
    }
  } catch (e) { status('network error: ' + e.message, 'err'); }
  return false;
}

function ratingSelect(current, noneLabel) {
  const sel = document.createElement('select');
  sel.append(new Option(noneLabel || 'rating\u2026', ''));
  for (let r = 5; r >= 0.5; r -= 0.5)
    sel.append(new Option('\u2605'.repeat(Math.floor(r)) +
                          (r % 1 ? '\u00bd' : '') + ` ${r}`, r));
  if (current != null) sel.value = String(current);
  return sel;
}
const lbl = t => { const l = document.createElement('label');
  l.textContent = t; return l; };
const inp = (v, ph) => { const i = document.createElement('input');
  i.value = v == null ? '' : v; if (ph) i.placeholder = ph; return i; };

/* ------------------- book page: full-book editor -------------------- */

function buildBookEditor(b, data, sha, onDone) {
  const ed = document.createElement('div'); ed.className = 'mted';
  const ti = inp(b.title, 'title');
  const ai = inp(b.author, 'author');
  const statusSel = document.createElement('select');
  for (const s of ['reading', 'finished', 'abandoned'])
    statusSel.append(new Option(s, s));
  statusSel.value = b.status;
  const rateSel = ratingSelect(b.rating, 'no rating');
  const pcI = inp(b.page_count, 'total pages'); pcI.inputMode = 'numeric';
  const stI = inp(b.started); stI.type = 'date';
  const finI = inp(b.finished); finI.type = 'date';
  statusSel.onchange = () => {   // un-finish clears; user can re-set
    if (statusSel.value === 'reading') { finI.value = ''; rateSel.value = ''; }
  };
  ed.append(lbl('title'), ti, lbl('author'), ai);
  const g1 = document.createElement('div'); g1.className = 'g2';
  g1.append(statusSel, rateSel);
  ed.append(lbl('status / rating'), g1);
  const g2 = document.createElement('div'); g2.className = 'g2';
  g2.append(stI, finI);
  ed.append(lbl('started / finished'), g2, lbl('total pages'), pcI);

  ed.append(lbl('sessions'));
  const srows = [];
  const sbox = document.createElement('div');
  const addRow = (d, p) => {
    const row = document.createElement('div'); row.className = 'srow';
    const di = document.createElement('input'); di.type = 'date';
    di.value = d || '';
    const pi = document.createElement('input');
    pi.inputMode = 'numeric'; pi.placeholder = 'page';
    pi.value = p == null ? '' : p;
    const del = document.createElement('button');
    del.className = 'danger'; del.textContent = '\u00d7';
    del.onclick = () => { row.remove();
      srows.splice(srows.indexOf(row), 1); };
    row._d = di; row._p = pi;
    row.append(di, pi, del);
    srows.push(row); sbox.append(row);
  };
  for (const s of b.sessions) addRow(s.slice(0, 10), s.slice(11));
  ed.append(sbox);
  const add = document.createElement('button');
  add.textContent = '+ session'; add.onclick = () => addRow('', '');
  ed.append(add);

  const btns = document.createElement('div'); btns.className = 'btnrow';
  const save = document.createElement('button');
  save.textContent = 'save';
  save.onclick = async () => {
    const title = ti.value.trim();
    if (!title) { status('title needed', 'err'); return; }
    let pc = null;
    if (pcI.value.trim() !== '') {
      pc = parseInt(pcI.value, 10);
      if (isNaN(pc) || pc <= 0) { status('bad page count', 'err'); return; }
    }
    const sess = [], seen = new Set();
    for (const row of srows) {
      const d = row._d.value, p = parseInt(row._p.value, 10);
      if (!d) { status('every session needs a date', 'err'); return; }
      if (isNaN(p) || p < 0) { status(`bad page for ${d}`, 'err'); return; }
      if (seen.has(d)) { status(`two sessions on ${d} \u2014 merge them`,
                                'err'); return; }
      seen.add(d); sess.push(`${d} ${p}`);
    }
    dateSort(sess);
    const st = statusSel.value;
    let fin = finI.value || null;
    if (st !== 'reading' && !fin) fin = today();
    b.title = title;
    b.author = ai.value.trim();
    // NOTE: slug is intentionally left alone on title/author edits —
    // it's this page's filename and the dedup key for lists/covers.
    b.status = st;
    b.rating = rateSel.value === '' ? null : parseFloat(rateSel.value);
    b.page_count = pc;
    b.started = stI.value || null;
    b.finished = fin;
    b.sessions = sess;
    save.disabled = delB.disabled = true;
    status('saving\u2026');
    if (await putLog(sha, data)) {
      status('saved', 'ok');
      ed.remove(); onDone();
    } else save.disabled = delB.disabled = false;
  };
  const delB = document.createElement('button');
  delB.className = 'danger'; delB.textContent = 'delete book';
  delB.onclick = async () => {
    if (!confirm(`delete "${b.title}" and all its sessions?`)) return;
    data.books.splice(data.books.indexOf(b), 1);
    save.disabled = delB.disabled = true;
    status('deleting\u2026');
    if (await putLog(sha, data)) {
      status('deleted \u2014 this page goes away on the next rebuild ' +
             '(~2 min)', 'ok');
      ed.remove(); onDone();
    } else {
      data.books.push(b);   // restore the working copy on failure
      save.disabled = delB.disabled = false;
    }
  };
  btns.append(save, delB); ed.append(btns);
  return ed;
}

function slugify(t) {
  const s = t.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-')
             .replace(/^-+|-+$/g, '');
  return s || 'book';
}
function uniqueSlug(t, books) {
  let s = slugify(t), n = 2;
  const taken = new Set(['index', 'log', 'list', ...books.map(b => b.slug)]);
  let out = s;
  while (taken.has(out)) out = `${s}-${n++}`;
  return out;
}

window.mtBookPage = function () {
  // one editor per read: single-read pages have one .mt-edit link at the
  // top; grouped re-read pages have one per "Read N" heading
  let open = null;   // { ed, link }
  for (const link of document.querySelectorAll('.mt-edit'))
    link.onclick = async ev => {
      ev.preventDefault();
      if (open) {
        open.ed.remove();
        const same = open.link === link;
        open = null; status('');
        if (same) return;
      }
      status('loading\u2026');
      let sha, data;
      try { ({ sha, data } = await fetchLog()); }
      catch (e) { status('load failed: ' + e.message, 'err'); return; }
      const b = data.books.find(x => x.slug === link.dataset.slug);
      if (!b) { status('this read is no longer in the log \u2014 the page ' +
                       'is stale', 'err'); return; }
      status('');
      const ed = buildBookEditor(b, data, sha, () => { open = null; });
      (link.closest('h2') || document.querySelector('.head')).after(ed);
      open = { ed, link };
    };

  const ra = document.getElementById('mt-readagain');
  if (ra) ra.onclick = async ev => {
    ev.preventDefault();
    status('loading\u2026');
    let sha, data;
    try { ({ sha, data } = await fetchLog()); }
    catch (e) { status('load failed: ' + e.message, 'err'); return; }
    const b = data.books.find(x => x.slug === ra.dataset.slug);
    if (!b) { status('book is no longer in the log \u2014 this page is ' +
                     'stale', 'err'); return; }
    if (!confirm(`start a new read of "${b.title}"?`)) { status(''); return; }
    data.books.push({
      title: b.title, author: b.author,
      slug: uniqueSlug(b.title, data.books),
      status: 'reading', rating: null,
      page_count: b.page_count,
      started: today(), finished: null, sessions: [] });
    status('saving\u2026');
    if (await putLog(sha, data))
      status('new read started', 'ok');
  };
};

/* ---------------- list diary: single-session editor ----------------- */

window.mtListPage = function () {
  let open = null;   // { ed, btn } — one editor at a time
  for (const btn of document.querySelectorAll('.rowedit'))
    btn.onclick = () => {
      if (open) {
        open.ed.remove();
        const same = open.btn === btn;
        open = null; status('');
        if (same) return;
      }
      const ed = document.createElement('div'); ed.className = 'mted';
      const row = document.createElement('div'); row.className = 'srow';
      const di = document.createElement('input'); di.type = 'date';
      di.value = btn.dataset.date;
      const pi = document.createElement('input');
      pi.inputMode = 'numeric'; pi.placeholder = 'page';
      pi.value = btn.dataset.page;
      const save = document.createElement('button');
      save.textContent = 'save';
      const del = document.createElement('button');
      del.className = 'danger'; del.textContent = 'delete';
      const commit = async remove => {
        const p = parseInt(pi.value, 10);
        if (!remove) {
          if (!di.value) { status('pick a date', 'err'); return; }
          if (isNaN(p) || p < 0) { status('bad page', 'err'); return; }
        }
        save.disabled = del.disabled = true;
        status('saving\u2026');
        let sha, data;
        try { ({ sha, data } = await fetchLog()); }
        catch (e) { status('load failed: ' + e.message, 'err');
          save.disabled = del.disabled = false; return; }
        const b = data.books.find(x => x.slug === btn.dataset.slug);
        const i = b ? b.sessions.findIndex(
          s => s.startsWith(btn.dataset.date + ' ')) : -1;
        if (i < 0) {
          status('session not found \u2014 the log changed; reload the ' +
                 'page', 'err');
          save.disabled = del.disabled = false; return;
        }
        if (remove) b.sessions.splice(i, 1);
        else {
          if (di.value !== btn.dataset.date &&
              b.sessions.some((s, j) => j !== i &&
                              s.startsWith(di.value + ' '))) {
            status(`already a session on ${di.value}`, 'err');
            save.disabled = del.disabled = false; return;
          }
          b.sessions[i] = `${di.value} ${p}`;
          dateSort(b.sessions);
        }
        if (await putLog(sha, data)) {
          status('saved', 'ok');
          ed.remove(); open = null;
        } else save.disabled = del.disabled = false;
      };
      save.onclick = () => commit(false);
      del.onclick = () => {
        if (confirm('delete this session?')) commit(true);
      };
      row.append(di, pi, save, del); ed.append(row);
      btn.closest('.rowwrap').after(ed);
      open = { ed, btn };
    };
};
})();
"""


def _page_head(title: str) -> list:
    e = html.escape
    return [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>{e(title)}</title>",
        site.head_extra(1, (("reading", _CSS),)),
        "</head><body style='--pagew:860px'>",
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


# ------------------------------------------------------------ data bundle

def diary_bundle(rlog: ReadingLog, page_counts: dict, sources: dict,
                 covers_cache: dict, films: list, base_of: dict) -> dict:
    """View-ready diary data for docs/assets/diary.js.

    Carries ENRICHMENT (covers, page counts) alongside RAW sessions rather
    than precomputed totals: the client has to fold in edits saved to
    localStorage that this build has never seen, so streaks, per-day
    deltas and the calendar must stay computable in the browser. CI
    contributes only what needs the network — the iTunes/OpenLibrary/Apple
    Books lookups a page can't make itself.
    """
    books = []
    for b in rlog.books:
        books.append({
            "slug": b.slug,
            "base": base_of.get(b.slug, b.slug),
            "key": b.cache_key,
            "title": b.title,
            "author": b.author,
            "status": b.status,
            "rating": b.rating,
            "started": b.started,
            "finished": b.finished,
            "pageCount": page_counts.get(b.slug),
            "pageSource": sources.get(b.slug),
            "cover": _cover_url(b, covers_cache),
            "hue": lists_gen._tile_hue(b.title),
            "sessions": [[d.isoformat(), p] for d, p in b.parsed_sessions()],
        })
    out_films = []
    for f in films:
        if not f.get("watched"):
            continue
        out_films.append({
            "slug": f.get("slug") or "",
            "title": f.get("title") or f.get("slug") or "?",
            "year": f.get("year"),
            "watched": f["watched"],
            "rating": f.get("rating"),
            "rewatch": bool(f.get("rewatch")),
            "liked": bool(f.get("liked")),
            "poster": f.get("poster_url"),
        })
    # Deliberately NOT sorted: watching/log.json order is what decides the
    # order of several films watched on one day, and the diary shows them
    # that way. Sorting here silently reshuffled those days.
    return {
        "dailyGoal": rlog.daily_goal,
        "books": books,
        "films": out_films,
    }


# ----------------------------------------------------------------- build

def reading_links(log_path: Path = LOG_PATH) -> dict:
    """{'title|author': '../reading/<slug>.html'} for lists_gen tiles."""
    if not log_path.exists():
        return {}
    try:
        log = load_log(log_path)
    except (ValueError, json.JSONDecodeError):
        return {}
    groups, _ = group_reads(log.books)
    out = {}
    for key, reads in groups.items():
        rated = [b for b in reads
                 if b.status == "finished" and b.rating is not None]
        out[key] = {
            "href": f"../reading/{reads[0].slug}.html",
            "rating": (max(rated, key=lambda b: b.finished or "").rating
                       if rated else None),
        }
    return out


def build_all(log_path: Path = LOG_PATH, out_dir: Path = OUT_DIR,
              cache_path: Path = PAGECACHE_PATH, fetch: bool = True,
              log=print) -> list:
    rlog = load_log(log_path)
    cache = load_pagecache(cache_path)
    covers_cache = lists_gen.load_cache()
    known, covers_known = len(cache), len(covers_cache)
    session = None
    if fetch:
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

    from . import watching_gen  # late import: watching_gen uses our helpers
    films = []
    if watching_gen.LOG_PATH.exists():
        _, films = watching_gen.load_log()
    films_by_day = watching_gen.films_by_date(films)

    warn = (lambda msg: log(f"  WARNING: {msg}")) if log else None
    groups, base_of = group_reads(rlog.books)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = list(site.write_sheets((("reading", _CSS),)))
    bundle, _ = site.write_data(
        "diary.json",
        diary_bundle(rlog, page_counts, sources, covers_cache, films, base_of))
    written.append(bundle)
    edit_js = out_dir / "edit.js"
    edit_js.write_text(_EDIT_JS, encoding="utf-8")
    written.append(edit_js)
    # Shells only — docs/assets/diary.js draws these from docs/data/diary.json
    # plus any edit still sitting in localStorage. edit.js loads first so the
    # renderer can hand off to mtListPage()/mtBookPage() once the DOM exists.
    sheets = (("reading", _CSS),)
    edit = ("<script src='edit.js' defer></script>",)
    index = out_dir / "index.html"
    index.write_text(site.shell("diary", "calendar", 1, sheets,
                                nav_active="diary", scripts=edit),
                     encoding="utf-8")
    written.append(index)
    flat = out_dir / "list.html"
    flat.write_text(site.shell("diary", "flatlist", 1, sheets,
                               nav_active="diary", scripts=edit),
                    encoding="utf-8")
    written.append(flat)
    for reads in groups.values():
        base = reads[0]
        out = out_dir / f"{base.slug}.html"
        out.write_text(
            site.shell(html.escape(base.title), "book", 1, sheets,
                       scripts=edit,
                       attrs=f" data-slug='{html.escape(base.slug)}'"),
            encoding="utf-8")
        written.append(out)

    # prune pages for books deleted from the log (log/index/list are in
    # RESERVED_SLUGS, so hand-written pages survive); re-read slugs get
    # no page of their own, so any old <base>-2.html prunes here too
    keep = RESERVED_SLUGS | {reads[0].slug for reads in groups.values()}
    for stale in out_dir.glob("*.html"):
        if stale.stem not in keep:
            stale.unlink()
            if log:
                log(f"  pruned {stale.name} (book no longer in log)")

    if len(cache) != known:
        save_pagecache(cache, cache_path)
        if log:
            log(f"cached {len(cache) - known} page-count lookup(s) "
                f"-> {cache_path}")
    if len(covers_cache) != covers_known:
        lists_gen.save_cache(covers_cache)
    if log:
        log(f"reading: {len(rlog.books)} book(s) -> {out_dir}")
    written += watching_gen.build_all(log=log)
    return written
