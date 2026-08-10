"""Markdown run report — the browsable record next to the phone pushes."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .config import Config
from .models import Observation, SourceResult
from .state import State

# A title nobody carries isn't a bug — libraries buy on their own schedule,
# and half the movie list is unreleased. Only after this long is "we've never
# seen it anywhere" better explained by a typo than by the world.
SPELLING_HINT_DAYS = 90

STILL_LOOKING_BLURB = (
    "On the watchlist, not matched at any source yet. This is the normal "
    "resting place for a new or forthcoming title: it waits here until a "
    "library buys a copy or a theater books a date."
)


@dataclass
class Waiting:
    """One watchlist item that has never matched anywhere."""
    label: str
    days: int | None          # days on the watchlist, None if unstamped

    @property
    def suspect(self) -> bool:
        """Old enough that a spelling check is worth suggesting."""
        return self.days is not None and self.days >= SPELLING_HINT_DAYS

    @property
    def age(self) -> str:
        return f"{self.days}d" if self.days is not None else ""


def still_looking(config: Config, current: list[Observation],
                  state: State, now: datetime | None = None) -> list[Waiting]:
    """Watchlist items with no sighting now and none ever, oldest first."""
    current_keys = {o.item_key for o in current}
    out = []
    for item in [*config.books, *config.movies]:
        if item.key in current_keys:
            continue
        if any(f"|{item.key}|" in fp for fp in state.seen):
            continue
        out.append(Waiting(str(item), state.waiting_days(item.key, now)))
    return sorted(out, key=lambda w: (-(w.days or 0), w.label.lower()))


def build_report(config: Config, results: list[SourceResult],
                 new: list[Observation], state: State) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Media tracker report — {now}", ""]

    lines.append(f"## New sightings ({len(new)})")
    if new:
        for obs in sorted(new, key=lambda o: o.item_label.lower()):
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

    waiting = still_looking(config, current, state)
    if waiting:
        lines.append(f"## Still looking ({len(waiting)})")
        lines.append(STILL_LOOKING_BLURB)
        for w in waiting:
            suffix = f" — waiting {w.age}" if w.days is not None else ""
            if w.suspect:
                suffix += " · nothing this whole time, so check the spelling"
            lines.append(f"- {w.label}{suffix}")
        lines.append("")

    return "\n".join(lines) + "\n"
