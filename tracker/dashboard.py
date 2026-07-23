"""Static, self-contained HTML dashboard.

Written to docs/index.html on every check run. Once the project has its
own repo with GitHub Pages enabled ("deploy from branch", /docs folder),
this page auto-updates after each scheduled run and is bookmarkable on a
phone from anywhere. No JavaScript, inline CSS only, phone-first layout.
"""
from __future__ import annotations

import html
from collections import OrderedDict
from datetime import datetime, timezone

from . import site
from .config import Config
from .models import Observation, SourceResult
from .state import State

_CSS = """
.card { border: 1px solid var(--line); border-radius: var(--r-md);
        background: var(--surface); box-shadow: var(--shadow-sm);
        padding: 10px 12px; margin-bottom: 8px; }
.card.new { border-left: 5px solid var(--ok); }
.card > summary { font-weight: 600; cursor: pointer;
        list-style: none; }
.card > summary::-webkit-details-marker { display: none; }
.card > summary::after { content: '\\25B8'; margin-left: 6px;
        font-size: .7rem; vertical-align: middle; color: var(--ink-mute); }
.card[open] > summary::after { content: '\\25BE'; }
.row { display: flex; justify-content: space-between; align-items: center;
       padding: 3px 0; font-size: .9rem; }
.row + .row { border-top: 1px dashed var(--line); }
.row .lbl { flex: 1; }
.row .st { margin-left: 8px; white-space: nowrap; }
.row a { font-size: .85rem; margin-left: 6px; }
.muted { opacity: .6; }
.src { display: flex; justify-content: space-between; font-size: .9rem;
       padding: 4px 2px; border-bottom: 1px dashed var(--line); }
.ok { color: var(--ok); } .err { color: var(--err); }
ul.watch { padding-left: 20px; margin: 4px 0; }
ul.watch li { display: flex; align-items: center; justify-content: space-between; }
ul.watch li span { flex: 1; }
.rm { background: none; border: none; padding: 2px 6px; cursor: pointer;
      opacity: .35; font-size: .85rem; line-height: 1; }
.rm:hover { opacity: .8; }
.warn { background: var(--gold-tint); color: #8A6A16; border-radius: 8px;
        padding: 8px 12px; font-size: .9rem; }
details { margin-bottom: 4px; }
details > summary { list-style: none; cursor: pointer; }
details > summary::-webkit-details-marker { display: none; }
details > summary h2 { display: inline; }
details > summary::after { content: '\\25B8'; margin-left: 6px;
        font-size: .7rem; vertical-align: middle; color: var(--ink-mute); }
details[open] > summary::after { content: '\\25BE'; }
"""

_REMOVE_JS = """
<script>
const REPO = 'joshuadwray/media-tracker';
const WL_API = `https://api.github.com/repos/${REPO}/contents/watchlist.yaml`;
function b64decode(s) { return decodeURIComponent(escape(atob(s))); }
async function rmItem(btn, title, kind) {
  if (!confirm(`Remove \\u201c${title}\\u201d from ${kind}s?`)) return;
  const token = localStorage.getItem('mt_pat');
  if (!token) { alert('Set your PAT on the + add page first.'); return; }
  btn.disabled = true; btn.textContent = '\\u22ef';
  try {
    const r = await fetch(WL_API, { headers: { Authorization: 'token ' + token } });
    if (!r.ok) throw new Error('fetch: ' + r.status);
    const data = await r.json();
    const sha = data.sha;
    const text = b64decode(data.content);
    const esc = title.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');
    const re = new RegExp(
      '^[ \\t]*- title: (?:' + esc + '|"' + esc + '"|\\x27' + esc + '\\x27)\\n(?:[ \\t]+(?!- )[^\\n]*\\n)*', 'm');
    const after = text.replace(re, '');
    if (after === text) { throw new Error('entry not found in watchlist'); }
    const put = await fetch(WL_API, { method: 'PUT',
      headers: { Authorization: 'token ' + token, 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: 'remove ' + kind + ': ' + title,
        content: btoa(unescape(encodeURIComponent(after))), sha }) });
    if (!put.ok) { const e = await put.json(); throw new Error(e.message || put.status); }
    btn.closest('li').style.display = 'none';
  } catch(e) { alert('Remove failed: ' + e.message); btn.disabled = false; btn.textContent = '\\u{1F5D1}'; }
}
</script>"""


def build_dashboard(config: Config, results: list[SourceResult],
                    new: list[Observation], state: State) -> str:
    now_dt = datetime.now(timezone.utc)
    now = now_dt.strftime("%Y-%m-%d %H:%M UTC")
    current = [o for r in results for o in r.observations]
    new_fps = {o.fingerprint for o in new}

    # Build item_key → label lookup from config.
    item_labels: dict[str, str] = {}
    for item in [*config.books, *config.movies]:
        item_labels[item.key] = str(item)

    e = html.escape
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<title>media tracker</title>",
        site.head_extra(0),
        f"<style>{site.BASE_CSS}{_CSS}</style></head><body>",
        site.nav("tracker", 0),
        "<h1>media tracker</h1>",
        f"<div class='meta'>last checked {e(now)}</div>",
    ]

    # Group current observations by item_key (preserving first-seen order).
    grouped = _group_by_item(current)

    # Also gather historical (stale) fingerprints per item_key from state,
    # for items not seen this run but still within the prune window.
    current_fps = {o.fingerprint for o in current}
    today = now_dt.strftime("%Y-%m-%d")
    historical, carried = _historical_by_item(state, current_fps, today)

    parts.append(f"<details open><summary><h2>New this run ({len(new)})</h2></summary>")
    if new:
        new_grouped = _group_by_item(new)
        for item_key, obs_list in new_grouped.items():
            stale = historical.get(item_key, [])
            carry = carried.get(item_key, [])
            label = item_labels.get(item_key, obs_list[0].item_label)
            parts.append(_grouped_card(label, obs_list, stale, new_fps,
                                       now_dt, is_new=True,
                                       carried=carry))
    else:
        parts.append("<div class='muted'>nothing new</div>")
    parts.append("</details>")

    # All items section: merge current + historical + carried into unified cards.
    # Only include items still on the watchlist (in item_labels).
    all_keys: list[str] = []
    all_obs: dict[str, list[Observation]] = {}
    for key, obs_list in grouped.items():
        if key in item_labels:
            all_keys.append(key)
            all_obs[key] = obs_list
    for key in {*historical, *carried}:
        if key in item_labels and key not in all_obs:
            all_keys.append(key)
            all_obs[key] = []

    parts.append(f"<details open><summary><h2>All tracked items ({len(all_keys)})</h2></summary>")
    if all_keys:
        for key in all_keys:
            obs_list = all_obs.get(key, [])
            stale = historical.get(key, [])
            carry = carried.get(key, [])
            label = item_labels[key]
            parts.append(_grouped_card(label, obs_list, stale, new_fps,
                                       now_dt, carried=carry))
    else:
        parts.append("<div class='muted'>no watchlist titles are available "
                     "or playing anywhere right now</div>")
    parts.append("</details>")

    has_err = any(r.error for r in results)
    parts.append(f"<details{' open' if has_err else ''}><summary><h2>Source health</h2></summary>")
    for r in results:
        if r.error:
            first = e(r.error.strip().splitlines()[0][:120])
            parts.append(f"<div class='src'><span>{e(r.source)}</span>"
                         f"<span class='err'>✗ {first}</span></div>")
        else:
            parts.append(f"<div class='src'><span>{e(r.source)}</span>"
                         f"<span class='ok'>✓ {len(r.observations)} sighting(s)"
                         "</span></div>")
    parts.append("</details>")

    never = _never_seen(config, current, state)
    if never:
        parts.append("<details><summary><h2>Never matched anywhere</h2></summary>")
        parts.append("<div class='warn'>These have never matched at any source "
                     "— double-check the spelling: "
                     + ", ".join(e(t) for t in never) + "</div>")
        parts.append("</details>")

    parts.append("<details><summary><h2>Watching</h2></summary><ul class='watch'>")
    for b in config.books:
        parts.append(f"<li><span>📖 {e(str(b))}</span>"
                     f"<button class='rm' onclick='rmItem(this,{_jsq(b.title)},\"book\")"
                     f"' title='Remove'>&#x1F5D1;</button></li>")
    for m in config.movies:
        parts.append(f"<li><span>🎬 {e(str(m))}</span>"
                     f"<button class='rm' onclick='rmItem(this,{_jsq(m.title)},\"movie\")"
                     f"' title='Remove'>&#x1F5D1;</button></li>")
    parts.append("</ul></details>")
    parts.append(_REMOVE_JS)
    parts.append("</body></html>")
    return "".join(parts)


def _group_by_item(observations: list[Observation]) -> OrderedDict[str, list[Observation]]:
    groups: OrderedDict[str, list[Observation]] = OrderedDict()
    for o in observations:
        groups.setdefault(o.item_key, []).append(o)
    return groups


def _historical_by_item(state: State, current_fps: set[str],
                        today: str | None = None,
                        ) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """Fingerprints in state.seen that weren't observed this run.

    Returns (stale, carried) where *carried* entries have at least one
    showtime date today-or-later (so they shouldn't look stale on the
    dashboard) and *stale* entries have no future dates.
    """
    if today is None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stale_items: dict[str, list[dict]] = {}
    carried_items: dict[str, list[dict]] = {}
    for fp, entry in state.seen.items():
        if fp in current_fps:
            continue
        parts = fp.split("|", 2)
        if len(parts) < 3:
            continue
        source, item_key, event = parts
        rec = {
            "source": source, "event": event, "fp": fp,
            "first": entry.get("first", ""), "last": entry.get("last", ""),
            "dates": entry.get("dates", []),
        }
        future_dates = [d for d in rec["dates"] if d >= today]
        if future_dates:
            carried_items.setdefault(item_key, []).append(rec)
        else:
            stale_items.setdefault(item_key, []).append(rec)
    return stale_items, carried_items


def _grouped_card(label: str, current_obs: list[Observation],
                  stale: list[dict], new_fps: set[str], now: datetime,
                  is_new: bool = False,
                  carried: list[dict] | None = None) -> str:
    e = html.escape
    cls = "card new" if is_new else "card"

    rows: list[str] = []
    for o in current_obs:
        is_fresh = o.fingerprint in new_fps
        badge = "🟢" if is_fresh else "✅"
        link = f" <a href='{e(o.url)}'>open&nbsp;↗</a>" if o.url else ""
        info = "" if o.positive else " <span class='muted'>(info)</span>"
        row_label = _short_label(o.source, o.event or o.summary)
        rows.append(f"<div class='row'><span class='lbl'>{e(row_label)}{info}</span>"
                    f"<span class='st'>{badge}{link}</span></div>")

    for c in (carried or []):
        today = now.strftime("%Y-%m-%d")
        future = sorted(d for d in c.get("dates", []) if d >= today)
        date_hint = f" ({', '.join(future[:3])})" if future else ""
        row_label = _short_label(c["source"], c["event"])
        rows.append(f"<div class='row'><span class='lbl'>{e(row_label)}"
                    f"<span class='muted'>{e(date_hint)}</span></span>"
                    f"<span class='st'>✅</span></div>")

    for s in stale:
        last = s["last"]
        ago = _ago(last, now) if last else ""
        badge = "⏸️"
        row_label = _short_label(s["source"], s["event"])
        rows.append(f"<div class='row'><span class='lbl muted'>"
                    f"{e(row_label)}"
                    f"</span><span class='st'>{badge}"
                    f" <span class='muted'>{e(ago)}</span></span></div>")

    open_attr = " open" if is_new else ""
    return (f"<details class='{cls}'{open_attr}>"
            f"<summary class='item'>{e(label)}</summary>"
            + "".join(rows) + "</details>")


def _short_label(source: str, event: str) -> str:
    """Compact row label: just the format for catalog items, source · event otherwise."""
    if event.endswith(" in catalog"):
        return event[: -len(" in catalog")]
    return f"{source} · {event}"


def _ago(ts: str, now: datetime) -> str:
    try:
        dt = datetime.fromisoformat(ts)
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return ""
    delta = now - dt
    days = delta.days
    if days < 1:
        hours = delta.seconds // 3600
        return f"{hours}h ago" if hours else "just now"
    if days == 1:
        return "1d ago"
    return f"{days}d ago"


def _jsq(s: str) -> str:
    """Quote a string for safe embedding in an HTML onclick attribute."""
    import json
    return html.escape(json.dumps(s), quote=True)


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
