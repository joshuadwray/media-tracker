"""Letterboxd-style list data, built from lists/*.yaml.

  python -m tracker lists

Reads every YAML file in lists/, resolves book covers via iTunes/Open
Library (build-time, cached in lists/covers-cache.json), and writes
docs/data/lists.json plus ~1KB shells at docs/lists/<stem>.html and
index.html. File order IS the rank; a text editor is the ranking UX.

The grid itself is drawn by docs/assets/diary.js in the browser — lists
are human-authored, so an edit has to show up immediately rather than
after a CI run and a Pages deploy. What stays here is the part a page
cannot do for itself: the cover lookups.

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
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yaml

from . import site
from .matching import fold

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


def _squash(text: str) -> str:
    """Fold accents, lowercase, drop everything that isn't a letter/digit.

    Punctuation is the other way the same name gets spelled two ways.
    Phone keyboards produce a curly apostrophe, so a log typed on the
    phone says "O\u2019Farrell" where Apple says "O'Farrell"; catalogs
    also write "O Farrell" and "OFarrell". None of those distinguish two
    people, and all of them defeated a raw substring test.
    """
    return re.sub(r"[^a-z0-9]+", "", fold(text).lower())


def _author_ok(author: str, *names: str) -> bool:
    """Surname guard: never return a cover credited to a different author.

    Squashed on both sides. The guard exists to reject a *different*
    author, but a literal substring test also rejects the same one spelled
    differently: a log typed "perez-carbonell" never matches Apple's
    "Pérez-Carbonell", and "Maggie O\u2019Farrell" (curly apostrophe, as an
    iPhone types it) never matches Apple's "Maggie O'Farrell". Either way
    the book silently renders as a blank tile with no page count and no
    error anywhere. Normalizing costs nothing — an accent or an apostrophe
    has never been what distinguishes two authors.
    """
    if not author:
        return True
    surname = _squash(author.split()[-1])
    return bool(surname) and any(surname in _squash(n or "") for n in names)


def _lookup(session, title: str, author: str, log=None) -> dict:
    entry = (_itunes_lookup(session, title, author)
             or _openlibrary_lookup(session, title, author)
             or {"cover_url": None, "source": None, "matched": None})
    if log:
        status = entry["source"] or "no cover"
        log(f"  lookup: {title!r} -> {status}")
    return entry


def _itunes_lookup(session, title: str, author: str) -> dict | None:
    """Search title+author, then title alone if that found nothing.

    Normalizing the *comparison* can't rescue a misspelled author, because
    the misspelling also goes into the query: Apple's search does not read
    "maggie ofarrell" as "Maggie O'Farrell", so a log typed without the
    apostrophe got back Hamnet and nothing else — Land was never in the
    result set to be compared against. Dropping the author widens the
    search to the title, and _author_ok still has to accept whatever comes
    back, so a wrong author is rejected exactly as before.
    """
    terms = [f"{title} {author}".strip()]
    if author.strip():
        terms.append(title.strip())
    for term in terms:
        hit = _itunes_search(session, term, title, author)
        if hit:
            return hit
    return None


def _itunes_search(session, term: str, title: str, author: str) -> dict | None:
    resp = session.get(ITUNES_URL,
                       params={"term": term,
                               "media": "ebook", "limit": 25, "country": "US"},
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
       border-radius: 6px; border: 1px solid var(--line); }
.cov { object-fit: cover; background: var(--surface-sunk); }
.noimg { display: flex; flex-direction: column; justify-content: center;
         align-items: center; text-align: center; padding: 12px;
         color: #fff; }
.noimg .nt { font-weight: 700; font-size: .95rem; }
.noimg .na { font-size: .8rem; opacity: .85; margin-top: 6px; }
.rank { position: absolute; top: 6px; left: 6px; z-index: 1;
        background: rgba(16,34,28,.82); color: #F5F7F4; font-size: .78rem;
        font-weight: 700; padding: 1px 8px; border-radius: 999px; }
.rate { position: absolute; top: 6px; right: 6px; z-index: 1;
        background: rgba(16,34,28,.82); color: var(--gold); font-size: .78rem;
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


def lists_bundle(blists: list, covers_by_stem: dict,
                 reading_links: dict | None = None) -> dict:
    """View-ready list data for docs/assets/diary.js.

    covers_by_stem: {stem: [url-or-None, ...]} in item order, as resolved
    by the (network-bound, cached) cover chain — the one part of a list
    page the browser cannot work out for itself.
    """
    reading_links = reading_links or {}
    out = []
    for blist in blists:
        covers = covers_by_stem.get(blist.stem) or []
        items = []
        for item, cover in zip(blist.items, covers):
            entry = reading_links.get(item.cache_key) or {}
            items.append({
                "title": item.title,
                "author": item.author,
                "cover": cover,
                "hue": _tile_hue(item.title),
                "href": entry.get("href"),
                "rating": entry.get("rating"),
            })
        out.append({
            "stem": blist.stem,
            "title": blist.title,
            "ranked": blist.ranked,
            "kind": blist.kind,
            "items": items,
        })
    return {"lists": out}



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

    written = list(site.write_sheets((("lists", _CSS),)))
    blists = []
    covers_by_stem: dict = {}
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
        covers_by_stem[blist.stem] = covers
        out = out_dir / f"{blist.stem}.html"
        out.write_text(
            site.shell(html.escape(blist.title), "list", 1,
                       (("lists", _CSS),),
                       attrs=f" data-stem='{html.escape(blist.stem)}'"),
            encoding="utf-8")
        written.append(out)
        with_cover = sum(1 for c in covers if c)
        if log:
            log(f"{path.name}: {len(blist.items)} items, "
                f"{with_cover} covers -> {out}")

    index = out_dir / "index.html"
    index.write_text(
        site.shell("lists", "lists-index", 1, (("lists", _CSS),),
                   nav_active="lists"),
        encoding="utf-8")
    written.append(index)

    bundle, _ = site.write_data(
        "lists.json", lists_bundle(blists, covers_by_stem, links))
    written.append(bundle)

    if len(cache) != known:
        save_cache(cache, cache_path)
        if log:
            log(f"cached {len(cache) - known} new lookup(s) -> {cache_path}")
    elif log:
        log("cover cache: all hits, no network lookups needed")
    return written
