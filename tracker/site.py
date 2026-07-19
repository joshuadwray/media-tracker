"""Shared site navigation strip for all generated (and hand-written) pages.

nav(active, depth) renders a one-line strip linking the four main
surfaces. Generators call it; the hand-written pages (docs/add.html,
docs/reading/log.html, docs/lists/edit.html) carry a pasted copy of the
rendered snippet — keep those in sync by hand if the items change.
"""
from __future__ import annotations

NAV_CSS = (".sitenav{font-size:.85rem;margin-bottom:12px}"
           " .sitenav b{font-weight:700} .sitenav .sep{opacity:.4}")

_ITEMS = (
    ("tracker", "index.html"),
    ("diary", "reading/"),
    ("lists", "lists/"),
    ("+ add", "add.html"),
)


def nav(active: str | None, depth: int = 0) -> str:
    """Nav strip html. active names the bolded current page (None on
    subpages, which link everything); depth = directory levels below
    docs/ (prefixes hrefs with ../)."""
    prefix = "../" * depth
    bits = []
    for name, href in _ITEMS:
        if name == active:
            bits.append(f"<b>{name}</b>")
        else:
            bits.append(f"<a href='{prefix}{href}'>{name}</a>")
    sep = " <span class='sep'>&middot;</span> "
    return f"<div class='sitenav'>{sep.join(bits)}</div>"
