"""Static, self-contained HTML dashboard.

Written to docs/index.html on every check run. Once the project has its
own repo with GitHub Pages enabled ("deploy from branch", /docs folder),
this page auto-updates after each scheduled run and is bookmarkable on a
phone from anywhere. Inline CSS only, phone-first layout. The only script
is the remove button, which dispatches remove-item.yml.
"""
from __future__ import annotations

import html
from collections import OrderedDict
from datetime import datetime, timezone

from . import site
from .config import Config
from .models import Observation, SourceResult
from .report import (STILL_LOOKING_BLURB, still_looking, sync_note,
                     tracks_for_item)
from .state import State

_CSS = """
.card { border: 1px solid var(--line); border-radius: var(--r-md);
        background: var(--surface); box-shadow: var(--shadow-sm);
        padding: 10px 12px; margin-bottom: 8px; }
.card.new { border-left: 5px solid var(--ok); }
.card > summary { font-weight: 600; cursor: pointer;
        list-style: none; display: flex; align-items: center; }
.card > summary::-webkit-details-marker { display: none; }
/* The caret hangs off the title, not the summary, so the remove button
   can sit at the far right without the arrow trailing after it. */
.card > summary .ttl { flex: 1; }
.card > summary .ttl::after { content: '\\25B8'; margin-left: 6px;
        font-size: .7rem; vertical-align: middle; color: var(--ink-mute); }
.card[open] > summary .ttl::after { content: '\\25BE'; }
.row { display: flex; justify-content: space-between; align-items: center;
       padding: 3px 0; font-size: .9rem; }
.row + .row { border-top: 1px dashed var(--line); }
.row .lbl { flex: 1; }
.row .st { margin-left: 8px; white-space: nowrap; }
.row a { font-size: .85rem; margin-left: 6px; }
.muted { opacity: .6; }
/* Track sections: the headline option is the one you'd act on, so it reads
   at full strength and the rest of the libraries recede under it. */
.trk { font-size: .7rem; text-transform: uppercase; letter-spacing: .06em;
       color: var(--ink-mute); margin: 8px 0 2px; }
.row.alt { padding-left: 12px; }
.row.alt .lbl { opacity: .6; font-size: .85rem; }
.row + .trk { border-top: 1px dashed var(--line); padding-top: 6px; }
.wait { font-variant-numeric: tabular-nums; font-weight: 600; }
.wait.now { color: var(--ok); }
.sync { font-size: .8rem; color: var(--ink-mute); margin-top: 6px;
        padding-top: 5px; border-top: 1px dashed var(--line); }
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
/* "Still looking" is a status, not a problem — plain rows, muted ages, and
   the gold warning colour held back for the ones old enough to be typos. */
.note { color: var(--ink-mute); font-size: .85rem; margin: 2px 0 8px; }
.wrow { display: flex; justify-content: space-between; font-size: .9rem;
        padding: 4px 2px; border-bottom: 1px dashed var(--line); }
.wrow .age { color: var(--ink-mute); margin-left: 10px; white-space: nowrap; }
.wrow.stale .age { color: #8A6A16; }
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
// Removal is a workflow_dispatch, not a Contents-API write: the YAML edit
// and the state cleanup both happen server-side in `tracker remove`. It
// also means this button works with the Actions-only PAT that add.html
// tells you to create (the old Contents-API version needed Contents:write
// and silently 403'd).
const REPO = 'joshuadwray/media-tracker';
const WORKFLOW = 'remove-item.yml';
async function rmItem(btn, title, kind) {
  if (!confirm(`Remove \\u201c${title}\\u201d from ${kind}s?`)) return;
  const token = localStorage.getItem('mt_pat');
  if (!token) { alert('Set your PAT on the + add page first.'); return; }
  btn.disabled = true; btn.textContent = '\\u22ef';
  try {
    const r = await fetch(
      `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
      { method: 'POST',
        headers: { Authorization: 'token ' + token,
                   Accept: 'application/vnd.github+json',
                   'Content-Type': 'application/json' },
        body: JSON.stringify({ ref: 'main', inputs: { kind, title } }) });
    if (r.status === 401) throw new Error('token rejected (expired?)');
    if (r.status !== 204) {
      const e = await r.json().catch(() => ({}));
      throw new Error(e.message || ('HTTP ' + r.status));
    }
    // A card in the main list, or a row in the Watching section.
    const host = btn.closest('details.card') || btn.closest('li');
    if (host) host.style.opacity = .45;
    btn.textContent = '\\u2713';
    btn.title = 'removing\\u2026 this page updates after the next check run';
  } catch(e) {
    alert('Remove failed: ' + e.message);
    btn.disabled = false; btn.textContent = '\\u{1F5D1}';
  }
}
</script>"""


def build_dashboard(config: Config, results: list[SourceResult],
                    new: list[Observation], state: State) -> str:
    site.write_sheets((("dash", _CSS),))
    now_dt = datetime.now(timezone.utc)
    now = now_dt.strftime("%Y-%m-%d %H:%M UTC")
    current = [o for r in results for o in r.observations]
    new_fps = {o.fingerprint for o in new}

    # Build item_key → label lookup from config, plus the (kind, raw title)
    # the remove button needs — the label carries author/year, which
    # watchlist.yaml doesn't match on.
    item_labels: dict[str, str] = {}
    item_remove: dict[str, tuple[str, str]] = {}
    for item in config.books:
        item_labels[item.key] = str(item)
        item_remove[item.key] = ("book", item.title)
    for item in config.movies:
        item_labels[item.key] = str(item)
        item_remove[item.key] = ("movie", item.title)

    e = html.escape
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<title>media tracker</title>",
        site.head_extra(0, (("dash", _CSS),)),
        "</head><body>",
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
                                       carried=carry,
                                       remove=item_remove.get(item_key)))
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
                                       now_dt, carried=carry,
                                       remove=item_remove.get(key)))
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

    waiting = still_looking(config, current, state, now_dt)
    if waiting:
        parts.append(f"<details><summary><h2>Still looking ({len(waiting)})"
                     "</h2></summary>")
        parts.append(f"<div class='note'>{e(STILL_LOOKING_BLURB)}</div>")
        for w in waiting:
            cls = " stale" if w.suspect else ""
            note = " · check the spelling?" if w.suspect else ""
            age = f"{w.age}{note}" if w.days is not None else "&mdash;"
            parts.append(f"<div class='wrow{cls}'><span>{e(w.label)}</span>"
                         f"<span class='age'>{age}</span></div>")
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
                  carried: list[dict] | None = None,
                  remove: tuple[str, str] | None = None) -> str:
    e = html.escape
    cls = "card new" if is_new else "card"

    rows: list[str] = []

    def obs_row(o: Observation, alt: bool) -> str:
        badge = "🟢" if o.fingerprint in new_fps else "✅"
        link = f" <a href='{e(o.url)}'>open&nbsp;↗</a>" if o.url else ""
        info = "" if o.positive else " <span class='muted'>(info)</span>"
        if o.track:
            wait_cls = "wait now" if o.bucket == "now" else "wait"
            lbl = (f"<span class='{wait_cls}'>{e(o.wait_text)}</span> · "
                   f"{e(o.where)} <span class='muted'>({e(o.medium or '')})</span>")
        else:
            lbl = e(_short_label(o.source, o.event or o.summary))
        return (f"<div class='row{' alt' if alt else ''}'>"
                f"<span class='lbl'>{lbl}{info}</span>"
                f"<span class='st'>{badge}{link}</span></div>")

    # Books split into reading/listening with the best option leading each;
    # everything else (showtimes, streaming) keeps the flat row.
    by_track = tracks_for_item(current_obs)
    for track, options in by_track.items():
        rows.append(f"<div class='trk'>{e(track)}</div>")
        rows.extend(obs_row(o, alt=i > 0) for i, o in enumerate(options))
    rows.extend(obs_row(o, alt=False) for o in current_obs if not o.track)
    note = sync_note(by_track)
    if note:
        rows.append(f"<div class='sync'>{e(note)}</div>")

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
    btn = ""
    if remove:
        kind, title = remove
        # preventDefault stops the click from toggling the <details> it
        # lives in; the confirm() in rmItem is the actual guard.
        btn = (f"<button class='rm' title='Remove from watchlist' "
               f"onclick='event.preventDefault();event.stopPropagation();"
               f"rmItem(this,{_jsq(title)},{_jsq(kind)})'>&#x1F5D1;</button>")
    return (f"<details class='{cls}'{open_attr}>"
            f"<summary class='item'><span class='ttl'>{e(label)}</span>"
            f"{btn}</summary>"
            + "".join(rows) + "</details>")


def _short_label(source: str, event: str) -> str:
    """Compact row label: format · library for catalog items, source · event
    otherwise. Several libraries are watched per format now, so the source has
    to stay visible — otherwise two libraries both render as a bare "ebook"."""
    if event.endswith(" in catalog"):
        return f"{event[: -len(' in catalog')]} · {source}"
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
