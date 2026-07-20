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
:root {
  --bg: #F4EBD9; --surface: #FBF6EC; --surface-sunk: #EDE3D0;
  --line: #DCCFB8; --line-strong: #CDBB9B;
  --ink: #3A2418; --ink-soft: #6B5240; --ink-mute: #A5765A;
  --amber: #E4A138; --amber-tint: #F1D49A;
  --terracotta: #C25438; --terracotta-2: #A5432B;
  --terracotta-tint: #E7B8A8;
  --font-sans: 'Space Grotesk', system-ui, -apple-system, sans-serif;
  --font-mono: 'Space Mono', ui-monospace, monospace;
  --r-sm: 10px; --r-md: 16px; --r-lg: 24px; --r-pill: 999px;
  --shadow-sm: 0 2px 6px -2px rgba(58,36,24,.20);
  --shadow-md: 0 10px 24px -12px rgba(58,36,24,.35);
  --ease: cubic-bezier(.2,.7,.2,1); --dur: 160ms;
  /* legacy aliases so page-local CSS re-themes for free */
  --mut: var(--ink-mute); --accent: var(--terracotta);
  --ok: #4C7A3F; --err: #A03123; }
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
"""

_FONTS_HREF = ("https://fonts.googleapis.com/css2"
               "?family=Space+Grotesk:wght@400;500;600;700"
               "&family=Space+Mono:wght@400;700&display=swap")


def head_extra(depth: int = 0) -> str:
    """Theme-color, favicon/apple-touch icon links and web-font loads.
    Insert in <head> before the page <style>. depth = directory levels
    below docs/ (icon hrefs are relative for project Pages)."""
    p = "../" * depth
    return (
        "<meta name='theme-color' content='#F4EBD9'>"
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
