"""Static, self-contained HTML dashboard.

Written to docs/index.html on every check run. Once the project has its
own repo with GitHub Pages enabled ("deploy from branch", /docs folder),
this page auto-updates after each scheduled run and is bookmarkable on a
phone from anywhere. No JavaScript, inline CSS only, phone-first layout.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone

from .config import Config
from .models import Observation, SourceResult
from .state import State

_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
       sans-serif; margin: 0 auto; max-width: 640px; padding: 16px;
       line-height: 1.45; }
h1 { font-size: 1.3rem; margin: 0 0 2px; }
h2 { font-size: 1rem; margin: 22px 0 8px; text-transform: uppercase;
     letter-spacing: .04em; opacity: .65; }
.ts { font-size: .85rem; opacity: .6; }
.card { border: 1px solid rgba(128,128,128,.35); border-radius: 10px;
        padding: 10px 12px; margin-bottom: 8px; }
.card.new { border-left: 5px solid #2e7d32; }
.card .item { font-weight: 600; }
.card .what { margin-top: 2px; }
.card a { font-size: .85rem; }
.muted { opacity: .6; }
.src { display: flex; justify-content: space-between; font-size: .9rem;
       padding: 4px 2px; border-bottom: 1px dashed rgba(128,128,128,.25); }
.ok { color: #2e7d32; } .err { color: #c62828; }
ul.watch { padding-left: 20px; margin: 4px 0; }
.warn { background: rgba(255,193,7,.15); border-radius: 8px;
        padding: 8px 12px; font-size: .9rem; }
"""


def build_dashboard(config: Config, results: list[SourceResult],
                    new: list[Observation], state: State) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    current = [o for r in results for o in r.observations]
    e = html.escape
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<title>media tracker</title>",
        f"<style>{_CSS}</style></head><body>",
        "<h1>📚🎬 media tracker</h1>",
        f"<div class='ts'>last checked {e(now)}</div>",
    ]

    parts.append(f"<h2>New this run ({len(new)})</h2>")
    if new:
        for o in new:
            parts.append(_card(o, is_new=True))
    else:
        parts.append("<div class='muted'>nothing new</div>")

    parts.append(f"<h2>Everything currently sighted ({len(current)})</h2>")
    if current:
        for o in current:
            parts.append(_card(o))
    else:
        parts.append("<div class='muted'>no watchlist titles are available "
                     "or playing anywhere right now</div>")

    parts.append("<h2>Source health</h2>")
    for r in results:
        if r.error:
            first = e(r.error.strip().splitlines()[0][:120])
            parts.append(f"<div class='src'><span>{e(r.source)}</span>"
                         f"<span class='err'>✗ {first}</span></div>")
        else:
            parts.append(f"<div class='src'><span>{e(r.source)}</span>"
                         f"<span class='ok'>✓ {len(r.observations)} sighting(s)"
                         "</span></div>")

    never = _never_seen(config, current, state)
    if never:
        parts.append("<h2>Never matched anywhere</h2>")
        parts.append("<div class='warn'>These have never matched at any source "
                     "— double-check the spelling: "
                     + ", ".join(e(t) for t in never) + "</div>")

    parts.append(f"<h2>Watching</h2><ul class='watch'>")
    for b in config.books:
        parts.append(f"<li>📖 {e(str(b))}</li>")
    for m in config.movies:
        parts.append(f"<li>🎬 {e(str(m))}</li>")
    parts.append("</ul></body></html>")
    return "".join(parts)


def _card(o: Observation, is_new: bool = False) -> str:
    e = html.escape
    cls = "card new" if is_new else "card"
    link = f"<div><a href='{e(o.url)}'>open ↗</a></div>" if o.url else ""
    note = "" if o.positive else " <span class='muted'>(informational)</span>"
    return (f"<div class='{cls}'><div class='item'>{e(o.item_label)}</div>"
            f"<div class='what'>{e(o.summary)}{note}</div>{link}</div>")


def _never_seen(config: Config, current: list[Observation],
                state: State) -> list[str]:
    current_keys = {o.item_key for o in current}
    out = []
    for item in [*config.books, *config.movies]:
        if item.key in current_keys:
            continue
        if any(f"|{item.key}|" in fp for fp in state.seen):
            continue
        out.append(str(item))
    return out
