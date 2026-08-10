"""Markdown run report — the browsable record next to the phone pushes."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone

from .availability import overlap
from .config import Config
from .models import TRACKS, Observation, SourceResult
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


def tracks_for_item(observations: list[Observation]) -> dict[str, list[Observation]]:
    """Split one item's sightings into reading/listening, each best-first."""
    out: dict[str, list[Observation]] = {}
    for obs in observations:
        if obs.track:
            out.setdefault(obs.track, []).append(obs)
    return {t: sorted(obs, key=lambda o: o.sort_key)
            for t, obs in sorted(out.items(), key=lambda kv: TRACKS.index(kv[0]))}


def sync_note(by_track: dict[str, list[Observation]]) -> str | None:
    """Whether the two formats can be in your hands at the same time.

    Only interesting when both tracks have a known wait — and only worth
    printing when they *don't* line up, since that's the case where you'd
    do something about it (suspend the hold that lands first).
    """
    reading = next(iter(by_track.get("reading") or []), None)
    listening = next(iter(by_track.get("listening") or []), None)
    if not reading or not listening:
        return None
    if reading.provisional or listening.provisional:
        # One side is on order, so its clock starts on a date nobody
        # publishes. Any gap we computed would be arithmetic on a guess.
        return None
    loan = min(reading.loan_days or 21, listening.loan_days or 21)
    result = overlap(reading.wait, listening.wait, loan)
    if result is None:
        return None
    gap, fits = result
    if fits:
        return f"sync: gap {gap}d, fits in a {loan}d loan"
    later = "listening" if (listening.wait or 0) > (reading.wait or 0) else "reading"
    earlier = "reading" if later == "listening" else "listening"
    return (f"sync: gap {gap}d > {loan}d loan — "
            f"suspend the {earlier} hold ~{gap}d for {later}")


def _sightings(current: list[Observation]) -> list[str]:
    """Per-item blocks: each track's best option first, then the rest.

    The flat list this replaced printed one line per record, which at a
    Libby-sized library meant six near-identical lines per book and no
    indication which one you'd actually act on.
    """
    if not current:
        return ["- none"]

    by_item: OrderedDict[str, list[Observation]] = OrderedDict()
    for obs in current:
        by_item.setdefault(obs.item_label, []).append(obs)

    lines: list[str] = []
    for label, obs_list in by_item.items():
        lines.append(f"- **{label}**")
        by_track = tracks_for_item(obs_list)
        for track, options in by_track.items():
            for i, obs in enumerate(options):
                marker = "" if obs.positive else " _(informational)_"
                # The winning record's author, so a fuzzy title match on the
                # wrong book is visible rather than quietly authoritative.
                who = obs.detail.get("author") if isinstance(obs.detail, dict) else None
                who = f" · {who}" if (i == 0 and who) else ""
                prefix = f"  - {track}: " if i == 0 else "    - also: "
                lines.append(f"{prefix}{obs.summary}{who}{marker}")
        note = sync_note(by_track)
        if note:
            lines.append(f"  - {note}")
        for obs in obs_list:
            if not obs.track:
                marker = "" if obs.positive else " _(informational)_"
                lines.append(f"  - {obs.summary}{marker}")
    return lines


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
    lines.extend(_sightings(current))
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
