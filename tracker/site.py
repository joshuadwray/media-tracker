"""Shared base CSS + site navigation tabs for all generated pages.

nav(active, depth) renders the tab strip linking the four main
surfaces; BASE_CSS carries the cross-page look (vars, body, headings,
tabs). Generators prepend BASE_CSS to their own <style> and set a page
width via <body style='--pagew:NNNpx'> where the 640px default doesn't
fit. The hand-written pages (docs/add.html, docs/reading/log.html,
docs/lists/edit.html) carry a pasted copy of the nav markup + tab CSS —
keep those in sync by hand if the items change.
"""
from __future__ import annotations

BASE_CSS = """
:root { color-scheme: light dark;
  --line: rgba(128,128,128,.35); --mut: rgba(128,128,128,.85);
  --ok: #2e7d32; --err: #c62828; --accent: #1565c0; }
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
       sans-serif; margin: 0 auto; max-width: var(--pagew, 640px);
       padding: 16px; line-height: 1.45; }
h1 { font-size: 1.3rem; margin: 0 0 2px; }
h2 { font-size: 1rem; margin: 22px 0 8px; text-transform: uppercase;
     letter-spacing: .04em; opacity: .65; }
.meta { font-size: .85rem; opacity: .6; }
a { color: var(--accent); }
button { font: inherit; padding: 8px 12px; border-radius: 8px;
         border: 1px solid var(--line); background: transparent;
         cursor: pointer; }
.sitenav { display: flex; gap: 8px; flex-wrap: wrap; font-size: .85rem;
           margin-bottom: 16px; }
.sitenav a, .sitenav b { padding: 5px 12px; border-radius: 999px;
           border: 1px solid var(--line); text-decoration: none;
           color: inherit; }
.sitenav b { background: var(--accent); border-color: var(--accent);
           color: #fff; font-weight: 600; }
"""

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
