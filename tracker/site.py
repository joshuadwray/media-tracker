"""Shared base CSS + site navigation tabs for all generated pages.

nav(active, depth) renders the tab strip linking the four main
surfaces; BASE_CSS carries the cross-page look (vars, body, headings,
tabs). Generators set a page width via <body style='--pagew:NNNpx'>
where the 640px default doesn't fit. The hand-written pages
(docs/add.html, docs/reading/log.html, docs/lists/edit.html) carry a
pasted copy of the nav markup + tab CSS — keep those in sync by hand if
the items change.

CSS ships as EXTERNAL files under docs/assets/ rather than inlined in
every page: inlining is what made a one-line style tweak rewrite all
~250 generated pages, which drowned the git history and slowed every
rebuild's commit. Sheets stay SEPARATE per surface (base + reading +
lists + dash) instead of one bundle because the surface sheets collide
on purpose-built class names — dashboard and reading both define .row,
.rm and .ok/.err with different rules, so concatenating them would
silently restyle pages. Hrefs carry a ?v=<content hash> so a changed
sheet is picked up immediately despite Pages' 10-minute
cache-control: max-age=600.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
ASSETS = DOCS / "assets"
DATA = DOCS / "data"

BASE_CSS = """
:root {
  --bg: #F5F7F4; --surface: #FFFFFF; --surface-sunk: #E8ECE6;
  --line: #CBD5C8; --line-strong: #A8B6A4;
  --ink: #1A2118; --ink-soft: #4A5648; --ink-mute: #6E7E6B;
  --gold: #E9B720; --gold-tint: #F0DCAA;
  --teal: #3E8C7E; --teal-2: #2E6E62;
  --teal-tint: #B0D8D0;
  --font-sans: 'Space Grotesk', system-ui, -apple-system, sans-serif;
  --font-mono: 'Space Mono', ui-monospace, monospace;
  --r-sm: 10px; --r-md: 16px; --r-lg: 24px; --r-pill: 999px;
  --shadow-sm: 0 2px 6px -2px rgba(16,34,28,.18);
  --shadow-md: 0 10px 24px -12px rgba(16,34,28,.30);
  --ease: cubic-bezier(.2,.7,.2,1); --dur: 160ms;
  /* legacy aliases — amber/terracotta map to new palette */
  --amber: var(--gold); --amber-tint: var(--gold-tint);
  --terracotta: var(--teal); --terracotta-2: var(--teal-2);
  --terracotta-tint: var(--teal-tint);
  --mut: var(--ink-mute); --accent: var(--teal);
  --ok: var(--teal); --err: #B23A2E; }
* { box-sizing: border-box; }
body { font-family: var(--font-sans); background: var(--bg);
       color: var(--ink); margin: 0 auto; max-width: var(--pagew, 640px);
       padding: 16px; line-height: 1.45; }
h1 { font-size: 1.3rem; margin: 0 0 2px; }
h2 { font-family: var(--font-mono); font-size: .85rem; margin: 22px 0 8px;
     text-transform: uppercase; letter-spacing: .12em;
     color: var(--ink-mute); }
.meta { font-size: .85rem; color: var(--ink-mute); }
a { color: var(--terracotta); }
a:hover { color: var(--terracotta-2); }
button { font: inherit; padding: 8px 12px; border-radius: var(--r-pill);
         border: 1px solid var(--line-strong); background: transparent;
         color: inherit; cursor: pointer;
         transition: background var(--dur) var(--ease); }
button:hover { background: var(--surface-sunk); }
.sitenav { display: flex; gap: 8px; flex-wrap: wrap; font-size: .85rem;
           margin-bottom: 16px; }
.sitenav a, .sitenav b { padding: 5px 12px; border-radius: 999px;
           border: 1px solid var(--line-strong); text-decoration: none;
           color: inherit; }
.sitenav b { background: var(--terracotta); border-color: var(--terracotta);
           color: var(--bg); font-weight: 600; }
/* Rows/cards for edits saved but not yet in a build: shown immediately
   from localStorage, marked so they read as in-flight rather than done. */
.syncing { position: relative; }
.syncing::after { content: ''; position: absolute; left: 0; top: 0;
           bottom: 0; width: 3px; border-radius: 3px;
           background: var(--gold); }
/* z-index below .edstatus (9) so the editor's own save bar covers the
   pill while it is showing, rather than the two fighting for the corner */
.mt-fresh { position: fixed; right: 10px; bottom: 10px; z-index: 8;
           font-size: .75rem; padding: 4px 10px; border-radius: 999px;
           border: 1px solid var(--line-strong); background: var(--surface);
           color: var(--ink-mute); box-shadow: var(--shadow-sm);
           transition: opacity var(--dur) var(--ease); }
.mt-fresh.pending { border-color: var(--gold); color: #8A6A16;
           background: var(--gold-tint); }
.mt-fresh.gone { opacity: 0; pointer-events: none; }
#mt-root:empty::before { content: 'loading\\2026'; color: var(--ink-mute);
           font-size: .85rem; }
"""

_FONTS_HREF = ("https://fonts.googleapis.com/css2"
               "?family=Space+Grotesk:wght@400;500;600;700"
               "&family=Space+Mono:wght@400;700&display=swap")


def asset_hash(text: str) -> str:
    """Short content hash used to cache-bust an asset href."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def write_asset(name: str, text: str, assets_dir: Path = ASSETS) -> Path:
    """Write docs/assets/<name>, returning the path. Idempotent — several
    generators call this for the shared sheets in one build."""
    assets_dir.mkdir(parents=True, exist_ok=True)
    out = assets_dir / name
    if not out.exists() or out.read_text(encoding="utf-8") != text:
        out.write_text(text, encoding="utf-8")
    return out


def css_link(name: str, text: str, depth: int = 0) -> str:
    """<link> for docs/assets/<name>.css, cache-busted by content."""
    p = "../" * depth
    return (f"<link rel='stylesheet' href='{p}assets/{name}.css"
            f"?v={asset_hash(text)}'>")


def js_link(name: str, text: str, depth: int = 0, defer: bool = True) -> str:
    """<script src> for docs/assets/<name>.js, cache-busted by content."""
    p = "../" * depth
    d = " defer" if defer else ""
    return (f"<script src='{p}assets/{name}.js"
            f"?v={asset_hash(text)}'{d}></script>")


def head_extra(depth: int = 0, sheets: tuple = ()) -> str:
    """Theme-color, favicon/apple-touch icon links, web-font loads and
    stylesheet links. depth = directory levels below docs/ (hrefs are
    relative for project Pages). sheets = extra (name, css_text) pairs
    linked after base.css, in order."""
    p = "../" * depth
    links = css_link("base", BASE_CSS, depth) + "".join(
        css_link(name, text, depth) for name, text in sheets)
    return (
        "<meta name='theme-color' content='#F5F7F4'>"
        f"<link rel='icon' href='{p}assets/icons/app-icon.svg'"
        " type='image/svg+xml'>"
        f"<link rel='icon' href='{p}assets/icons/favicon-32.png'"
        " sizes='32x32' type='image/png'>"
        f"<link rel='icon' href='{p}assets/icons/favicon-16.png'"
        " sizes='16x16' type='image/png'>"
        f"<link rel='apple-touch-icon' href='{p}assets/icons/"
        "apple-touch-icon.png' sizes='180x180'>"
        f"<link rel='manifest' href='{p}manifest.json'>"
        "<link rel='preconnect' href='https://fonts.googleapis.com'>"
        "<link rel='preconnect' href='https://fonts.gstatic.com'"
        " crossorigin>"
        f"<link rel='stylesheet' href='{_FONTS_HREF}'>"
        + links
    )


def write_data(name: str, obj, data_dir: Path = DATA) -> tuple:
    """Write docs/data/<name> as compact JSON. Returns (path, changed).

    Also refreshes docs/data/build.json, the stamp the pages poll to
    notice a fresh deploy. The stamp is keyed off bundle CONTENT, not the
    clock: a time-based stamp would differ on every scheduled check and
    commit + redeploy the site every 30 minutes for no reason.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    out = data_dir / name
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
    changed = not out.exists() or out.read_text(encoding="utf-8") != text
    if changed:
        out.write_text(text, encoding="utf-8")
    _update_stamp(out.stem, asset_hash(text), data_dir)
    return out, changed


def _update_stamp(key: str, digest: str, data_dir: Path = DATA) -> Path:
    """Merge one bundle's content hash into build.json, rewriting the file
    only when a hash actually moved (see write_data)."""
    stamp = data_dir / "build.json"
    cur = {}
    if stamp.exists():
        try:
            cur = json.loads(stamp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cur = {}
    if cur.get(key) == digest:
        return stamp
    cur[key] = digest
    cur["built"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stamp.write_text(
        json.dumps(cur, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8")
    return stamp


def write_sheets(sheets: tuple = (), assets_dir: Path = ASSETS) -> list:
    """Write base.css plus the given (name, css_text) sheets to
    docs/assets/. Every generator calls this for the sheets its pages
    link; base.css is shared and rewritten only when it changes."""
    out = [write_asset("base.css", BASE_CSS, assets_dir)]
    out += [write_asset(f"{name}.css", text, assets_dir)
            for name, text in sheets]
    return out

def asset_text(name: str, assets_dir: Path = ASSETS) -> str:
    """Contents of a hand-written asset, for cache-busting its href."""
    path = assets_dir / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


def shell(title: str, page: str, depth: int = 1, sheets: tuple = (),
          nav_active: str | None = None, pagew: str = "860px",
          attrs: str = "", scripts: tuple = ()) -> str:
    """A page shell for docs/assets/diary.js to render into.

    The human-authored surfaces (diary, lists) ship as ~1KB shells instead
    of baked HTML: the data they show has to be current the moment it is
    typed, which a CI build plus a Pages deploy cannot be. data-page tells
    diary.js what to draw, data-depth how far below docs/ it sits.
    Machine-authored pages (watching, the watchlist dashboard) are still
    rendered server-side — nobody is waiting on those.
    """
    extra = "".join(scripts) + js_link("diary", asset_text("diary.js"), depth)
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{title}</title>"
        + head_extra(depth, sheets) + extra
        + f"</head><body style='--pagew:{pagew}' data-page='{page}'"
        f" data-depth='{depth}'{attrs}>"
        + nav(nav_active, depth)
        + "<div id='mt-root'></div></body></html>"
    )


_ITEMS = (
    ("tracker", "index.html"),
    ("diary", "reading/"),
    ("lists", "lists/"),
    ("+ add", "add.html"),
)


def nav(active: str | None, depth: int = 0) -> str:
    """Nav tab strip html. active names the highlighted current page
    (None on subpages, which link everything); depth = directory levels
    below docs/ (prefixes hrefs with ../)."""
    prefix = "../" * depth
    bits = []
    for name, href in _ITEMS:
        if name == active:
            bits.append(f"<b>{name}</b>")
        else:
            bits.append(f"<a href='{prefix}{href}'>{name}</a>")
    return f"<div class='sitenav'>{''.join(bits)}</div>"
