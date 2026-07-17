"""Markdown run report — the browsable record next to the phone pushes."""
from __future__ import annotations

from datetime import datetime, timezone

from .config import Config
from .models import Observation, SourceResult
from .state import State


def build_report(config: Config, results: list[SourceResult],
                 new: list[Observation], state: State) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Media tracker report — {now}", ""]

    lines.append(f"## New sightings ({len(new)})")
    if new:
        for obs in new:
            link = f" — [link]({obs.url})" if obs.url else ""
            lines.append(f"- **{obs.item_label}**: {obs.summary}{link}")
    else:
        lines.append("- nothing new this run")
    lines.append("")

    current = [o for r in results for o in r.observations]
    lines.append(f"## All current sightings ({len(current)})")
    for obs in current:
        marker = "" if obs.positive else " _(informational)_"
        lines.append(f"- {obs.item_label}: {obs.summary}{marker}")
    if not current:
        lines.append("- none")
    lines.append("")

    lines.append("## Source status")
    for r in results:
        if r.error:
            first_line = r.error.strip().splitlines()[0]
            lines.append(f"- ❌ `{r.source}`: {first_line}")
        else:
            lines.append(f"- ✅ `{r.source}`: {len(r.observations)} observation(s)")
    lines.append("")

    never_seen = _never_seen_items(config, current, state)
    if never_seen:
        lines.append("## Never matched anywhere (possible typos?)")
        lines.append("These watchlist entries have not matched at any source, "
                     "ever. Double-check the spelling, or use `tracker add` "
                     "to pick the exact catalog record.")
        for label in never_seen:
            lines.append(f"- {label}")
        lines.append("")

    return "\n".join(lines) + "\n"


def _never_seen_items(config: Config, current: list[Observation],
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
