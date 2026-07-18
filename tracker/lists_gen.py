"""Static Letterboxd-style list pages, rendered from lists/*.yaml.

  python -m tracker lists

Reads every YAML file in lists/, resolves book covers via Open Library
(build-time, cached in lists/covers-cache.json), and writes
docs/lists/<stem>.html plus docs/lists/index.html. File order IS the
rank; a text editor is the ranking UX.

Cover rules:
  - a manual `cover:` URL on an item always wins
  - otherwise the cache is consulted (hand-editable; delete an entry to
    force a fresh lookup, or set its "cover_id" to fix a bad match)
  - otherwise one Open Library search per item (polite UA, spaced out);
    the result — including "no cover found" — is cached
  - no cover -> typographic tile, never a broken image
"""
from __future__ import annotations

import html
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
LISTS_DIR = ROOT / "lists"
OUT_DIR = ROOT / "docs" / "lists"
CACHE_PATH = LISTS_DIR / "covers-cache.json"

SEARCH_URL = "https://openlibrary.org/search.json"
COVER_URL = "https://covers.openlibrary.org/b/id/{}-L.jpg"
USER_AGENT = "media-tracker-lists (personal project)"
REQUEST_SPACING = 0.25  # seconds between Open Library hits
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
    return BookList(title=data.get("title") or path.stem,
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
        time.sleep(REQUEST_SPACING)
    cover_id = entry.get("cover_id")
    return COVER_URL.format(cover_id) if cover_id else None


def _lookup(session, title: str, author: str, log=None) -> dict:
    params = {"title": title, "limit": 20,
              "fields": "title,author_name,first_publish_year,cover_i"}
    if author:
        params["author"] = author
    resp = session.get(SEARCH_URL, params=params, timeout=30)
    resp.raise_for_status()
    docs = resp.json().get("docs") or []
    with_cover = [d for d in docs if d.get("cover_i")]
    # Prefer a recent edition's cover; fall back to any cover. When an
    # author was given we do NOT retry title-only: a wrong-book cover is
    # worse than a typographic tile.
    pick = next((d for d in with_cover
                 if (d.get("first_publish_year") or 0) >= RECENT_YEAR),
                None) or (with_cover[0] if with_cover else None)
    if pick:
        entry = {"cover_id": pick["cover_i"],
                 "matched": f"{pick.get('title')} — "
                            f"{(pick.get('author_name') or ['?'])[0]} "
                            f"({pick.get('first_publish_year')})"}
    else:
        entry = {"cover_id": None, "matched": None}
    if log:
        status = "ok" if pick else "no cover"
        log(f"  lookup: {title!r} -> {status}")
    return entry


# ------------------------------------------------------------------ html

_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
       sans-serif; margin: 0 auto; max-width: 860px; padding: 16px;
       line-height: 1.4; }
h1 { font-size: 1.35rem; margin: 0 0 2px; }
.meta { font-size: .85rem; opacity: .6; margin-bottom: 18px; }
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
.cap { margin-top: 6px; }
.cap .t { font-weight: 600; font-size: .9rem; }
.cap .a { font-size: .8rem; opacity: .65; }
ul.lists { padding-left: 20px; }
ul.lists li { margin: 6px 0; }
"""


def _tile_hue(title: str) -> int:
    return sum(ord(c) for c in title) % 360


def render_list(blist: BookList, covers: list) -> str:
    """covers: one URL-or-None per item, same order as blist.items."""
    e = html.escape
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>{e(blist.title)}</title>",
        f"<style>{_CSS}</style></head><body>",
        "<a class='back' href='./'>&larr; all lists</a>",
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
        parts.append(f"<li class='tile'>{badge}{img}"
                     f"<div class='cap'><div class='t'>{e(item.title)}</div>"
                     f"{author_cap}</div></li>")
    parts.append("</ol></body></html>")
    return "".join(parts)


def render_index(blists: list) -> str:
    e = html.escape
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<title>lists</title>",
        f"<style>{_CSS}</style></head><body>",
        "<h1>Lists</h1><ul class='lists'>",
    ]
    for bl in blists:
        parts.append(f"<li><a href='{e(bl.stem)}.html'>{e(bl.title)}</a> "
                     f"<span class='meta'>({len(bl.items)})</span></li>")
    parts.append("</ul></body></html>")
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
        out.write_text(render_list(blist, covers), encoding="utf-8")
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
