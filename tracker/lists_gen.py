"""Static Letterboxd-style list pages, rendered from lists/*.yaml.

  python -m tracker lists

Reads every YAML file in lists/, resolves book covers via Open Library
(build-time, cached in lists/covers-cache.json), and writes
docs/lists/<stem>.html plus docs/lists/index.html. File order IS the
rank; a text editor is the ranking UX.

Cover rules:
  - a manual `cover:` URL on an item always wins
  - otherwise the cache is consulted (hand-editable; delete an entry to
    force a fresh lookup, or set its "cover_url" to fix a bad match)
  - otherwise iTunes/Apple Books search (US store -> English editions,
    consistent high-res artwork), then Open Library as fallback; the
    result — including "no cover found" — is cached
  - no cover -> typographic tile, never a broken image

Both lookups verify the author surname against the result, so a wrong
author in the YAML yields a typographic tile, never a wrong-book cover.
"""
from __future__ import annotations

import html
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yaml

from . import site

ROOT = Path(__file__).resolve().parent.parent
LISTS_DIR = ROOT / "lists"
OUT_DIR = ROOT / "docs" / "lists"
CACHE_PATH = LISTS_DIR / "covers-cache.json"

SEARCH_URL = "https://openlibrary.org/search.json"
COVER_URL = "https://covers.openlibrary.org/b/id/{}-L.jpg"
ITUNES_URL = "https://itunes.apple.com/search"
ARTWORK_SIZE = "600x600bb"  # mzstatic keeps aspect ratio within the box
USER_AGENT = "media-tracker-lists (personal project)"
OL_SPACING = 0.25  # seconds between Open Library hits
ITUNES_SPACING = 3.0  # Apple's informal Search API limit is ~20/min
RECENT_YEAR = 2023  # prefer editions at least this new


@dataclass
class ListItem:
    title: str
    author: str = ""
    cover: str = ""  # manual override URL; always wins

    @property
    def cache_key(self) -> str:
        return f"{self.title.strip().lower()}|{self.author.strip().lower()}"


@dataclass
class BookList:
    title: str
    stem: str  # output file stem, from the yaml filename
    ranked: bool = True
    kind: str = "books"
    items: list = field(default_factory=list)


def parse_list(path: Path) -> BookList:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = []
    for raw in data.get("items") or []:
        if isinstance(raw, str):
            raw = {"title": raw}
        if not raw.get("title"):
            raise ValueError(f"{path.name}: item missing a title: {raw!r}")
        items.append(ListItem(title=str(raw["title"]),
                              author=str(raw.get("author") or ""),
                              cover=str(raw.get("cover") or "")))
    # str() guard: YAML types bare scalars ("2026" -> int, "no" -> bool)
    return BookList(title=str(data.get("title") or path.stem),
                    stem=path.stem,
                    ranked=bool(data.get("ranked", True)),
                    kind=data.get("kind") or "books",
                    items=items)


# ---------------------------------------------------------------- covers

def load_cache(path: Path = CACHE_PATH) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict, path: Path = CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=1, ensure_ascii=False,
                               sort_keys=True) + "\n", encoding="utf-8")


def resolve_cover(item: ListItem, cache: dict,
                  session=None, log=None) -> str | None:
    """Return a cover URL for the item, or None (-> typographic tile).

    Only hits the network for items absent from the cache, and only when
    a session is provided. Every lookup result is cached, including
    misses, so a full rebuild is normally zero network calls.
    """
    if item.cover:
        return item.cover
    entry = cache.get(item.cache_key)
    if entry is None:
        if session is None:
            return None
        entry = _lookup(session, item.title, item.author, log=log)
        cache[item.cache_key] = entry
    return _entry_url(entry)


def _entry_url(entry: dict) -> str | None:
    if entry.get("cover_url"):
        return entry["cover_url"]
    cover_id = entry.get("cover_id")  # older cache entries
    return COVER_URL.format(cover_id) if cover_id else None


def _author_ok(author: str, *names: str) -> bool:
    """Surname guard: never return a cover credited to a different author."""
    if not author:
        return True
    surname = author.split()[-1].lower()
    return any(surname in (n or "").lower() for n in names)


def _lookup(session, title: str, author: str, log=None) -> dict:
    entry = (_itunes_lookup(session, title, author)
             or _openlibrary_lookup(session, title, author)
             or {"cover_url": None, "source": None, "matched": None})
    if log:
        status = entry["source"] or "no cover"
        log(f"  lookup: {title!r} -> {status}")
    return entry


def _itunes_lookup(session, title: str, author: str) -> dict | None:
    resp = session.get(ITUNES_URL,
                       params={"term": f"{title} {author}".strip(),
                               "media": "ebook", "limit": 5, "country": "US"},
                       timeout=30)
    resp.raise_for_status()
    results = resp.json().get("results") or []
    time.sleep(ITUNES_SPACING)
    for hit in results:
        track = hit.get("trackName") or ""
        artist = hit.get("artistName") or ""
        art = hit.get("artworkUrl100")
        if (art and title.lower() in track.lower()
                and _author_ok(author, artist)):
            return {"cover_url": art.replace("100x100bb", ARTWORK_SIZE),
                    "source": "itunes",
                    "matched": f"{track} — {artist}"}
    return None


def _openlibrary_lookup(session, title: str, author: str) -> dict | None:
    params = {"title": title, "limit": 20,
              "fields": "title,author_name,first_publish_year,cover_i"}
    if author:
        params["author"] = author
    resp = session.get(SEARCH_URL, params=params, timeout=30)
    resp.raise_for_status()
    docs = resp.json().get("docs") or []
    time.sleep(OL_SPACING)
    with_cover = [d for d in docs
                  if d.get("cover_i")
                  and _author_ok(author, *(d.get("author_name") or []))]
    # Prefer a recent edition's cover; fall back to any cover. When an
    # author was given we do NOT retry title-only: a wrong-book cover is
    # worse than a typographic tile.
    pick = next((d for d in with_cover
                 if (d.get("first_publish_year") or 0) >= RECENT_YEAR),
                None) or (with_cover[0] if with_cover else None)
    if not pick:
        return None
    return {"cover_url": COVER_URL.format(pick["cover_i"]),
            "source": "openlibrary",
            "matched": f"{pick.get('title')} — "
                       f"{(pick.get('author_name') or ['?'])[0]} "
                       f"({pick.get('first_publish_year')})"}


# ------------------------------------------------------------------ html

_CSS = """
.meta { margin-bottom: 18px; }
a.back { font-size: .85rem; }
ol.grid { list-style: none; margin: 0; padding: 0; display: grid;
          gap: 16px 12px; grid-template-columns: repeat(2, 1fr); }
@media (min-width: 520px) { ol.grid { grid-template-columns: repeat(3, 1fr); } }
@media (min-width: 760px) { ol.grid { grid-template-columns: repeat(4, 1fr); } }
.tile { position: relative; }
.cov, .noimg { display: block; width: 100%; aspect-ratio: 2 / 3;
       border-radius: 6px; border: 1px solid rgba(128,128,128,.35); }
.cov { object-fit: cover; background: rgba(128,128,128,.12); }
.noimg { display: flex; flex-direction: column; justify-content: center;
         align-items: center; text-align: center; padding: 12px;
         color: #fff; }
.noimg .nt { font-weight: 700; font-size: .95rem; }
.noimg .na { font-size: .8rem; opacity: .85; margin-top: 6px; }
.rank { position: absolute; top: 6px; left: 6px; z-index: 1;
        background: rgba(0,0,0,.72); color: #fff; font-size: .78rem;
        font-weight: 700; padding: 1px 8px; border-radius: 999px; }
.rate { position: absolute; top: 6px; right: 6px; z-index: 1;
        background: rgba(0,0,0,.72); color: #ffd166; font-size: .78rem;
        font-weight: 700; padding: 1px 8px; border-radius: 999px; }
.cap { margin-top: 6px; }
.cap .t { font-weight: 600; font-size: .9rem; }
.cap .a { font-size: .8rem; opacity: .65; }
ul.lists { padding-left: 20px; }
ul.lists li { margin: 6px 0; }
a.tl { color: inherit; text-decoration: none; display: block; }
"""


def _tile_hue(title: str) -> int:
    return sum(ord(c) for c in title) % 360


def render_list(blist: BookList, covers: list,
                reading_links: dict | None = None) -> str:
    """covers: one URL-or-None per item, same order as blist.items.

    reading_links: {'title|author': {'href': ..., 'rating': ...}} — items
    with a reading-log entry get their tile wrapped in a link; finished
    books with a rating get a star badge.
    """
    e = html.escape
    reading_links = reading_links or {}
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>{e(blist.title)}</title>",
        f"<style>{site.BASE_CSS}{_CSS}</style></head>"
        "<body style='--pagew:860px'>",
        site.nav(None, 1),
        "<a class='back' href='./'>&larr; all lists</a> &middot; "
        f"<a class='back' href='edit.html?list={e(blist.stem)}'>edit</a>",
        f"<h1>{e(blist.title)}</h1>",
        f"<div class='meta'>{len(blist.items)} "
        f"{'titles, ranked' if blist.ranked else 'titles'}</div>",
        "<ol class='grid'>",
    ]
    for rank, (item, cover) in enumerate(zip(blist.items, covers), 1):
        badge = f"<span class='rank'>{rank}</span>" if blist.ranked else ""
        if cover:
            img = (f"<img class='cov' src='{e(cover)}' loading='lazy' "
                   f"alt='{e(item.title)} cover'>")
        else:
            hue = _tile_hue(item.title)
            author = (f"<div class='na'>{e(item.author)}</div>"
                      if item.author else "")
            img = (f"<div class='noimg' style='background:"
                   f"hsl({hue},35%,32%)'>"
                   f"<div class='nt'>{e(item.title)}</div>{author}</div>")
        author_cap = (f"<div class='a'>{e(item.author)}</div>"
                      if item.author else "")
        body = (f"{img}<div class='cap'><div class='t'>{e(item.title)}</div>"
                f"{author_cap}</div>")
        rate = ""
        entry = reading_links.get(item.cache_key)
        if entry:
            rating = entry["rating"]
            if rating is not None:
                rate = (f"<span class='rate' title='{rating:g}/5'>"
                        f"\u2605 {rating:g}</span>")
            body = f"<a class='tl' href='{e(entry['href'])}'>{body}</a>"
        parts.append(f"<li class='tile'>{badge}{rate}{body}</li>")
    parts.append("</ol></body></html>")
    return "".join(parts)


def render_index(blists: list) -> str:
    e = html.escape
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<title>lists</title>",
        f"<style>{site.BASE_CSS}{_CSS}</style></head>"
        "<body style='--pagew:860px'>",
        site.nav("lists", 1),
        "<h1>Lists</h1>",
        "<a class='back' href='edit.html?new=1'>+ new list</a>",
        "<ul class='lists'>",
    ]
    for bl in blists:
        parts.append(f"<li><a href='{e(bl.stem)}.html'>{e(bl.title)}</a> "
                     f"<span class='meta'>({len(bl.items)}) &middot; "
                     f"<a href='edit.html?list={e(bl.stem)}'>edit</a></span>"
                     "</li>")
    parts.append("</ul>")
    parts.append("</body></html>")
    return "".join(parts)


# ----------------------------------------------------------------- build

def build_all(lists_dir: Path = LISTS_DIR, out_dir: Path = OUT_DIR,
              cache_path: Path = CACHE_PATH, fetch: bool = True,
              log=print) -> list:
    yaml_paths = sorted(lists_dir.glob("*.yaml"))
    if not yaml_paths:
        raise SystemExit(f"no list files found in {lists_dir}")
    cache = load_cache(cache_path)
    known = len(cache)
    session = None
    if fetch:
        session = requests.Session()
        session.headers["User-Agent"] = USER_AGENT

    from .reading_gen import reading_links as _reading_links
    links = _reading_links()

    written = []
    blists = []
    out_dir.mkdir(parents=True, exist_ok=True)
    for path in yaml_paths:
        blist = parse_list(path)
        blists.append(blist)
        covers = []
        for item in blist.items:
            try:
                covers.append(resolve_cover(item, cache, session, log=log))
            except Exception as exc:  # noqa: BLE001 — leave uncached, tile it
                if log:
                    log(f"  cover lookup failed for {item.title!r}: {exc}")
                covers.append(None)
        out = out_dir / f"{blist.stem}.html"
        out.write_text(render_list(blist, covers, links), encoding="utf-8")
        written.append(out)
        with_cover = sum(1 for c in covers if c)
        if log:
            log(f"{path.name}: {len(blist.items)} items, "
                f"{with_cover} covers -> {out}")

    index = out_dir / "index.html"
    index.write_text(render_index(blists), encoding="utf-8")
    written.append(index)

    if len(cache) != known:
        save_cache(cache, cache_path)
        if log:
            log(f"cached {len(cache) - known} new lookup(s) -> {cache_path}")
    elif log:
        log("cover cache: all hits, no network lookups needed")
    return written
