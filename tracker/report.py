"""Markdown run report — the browsable record next to the phone pushes."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone

from .availability import overlap
from .config import Config
from .models import TRACKS, Observation, SourceResult
from .state import State, first_line

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


def venues_for_item(observations: list[Observation]) -> dict[str, list[Observation]]:
    """Split one film's sightings by theatre, best theatre first.

    The films twin of tracks_for_item. Theatres order on sort_key, which for
    a showtime means tier then miles; the listings inside one theatre order
    alphabetically, because a chain's title variants ("Dune: Part Three",
    "... Insider Screenings", "... Insider Screenings in IMAX 70MM") are one
    showing described three ways and shuffling them run to run makes a
    diffable page unreadable.

    Observations with no venue are left out — streaming and VOD dates aren't
    places, and they keep their flat rows.
    """
    out: dict[str, list[Observation]] = {}
    for obs in observations:
        if obs.venue and not obs.track:
            out.setdefault(obs.venue, []).append(obs)
    ordered = sorted(out.items(),
                     key=lambda kv: (min(o.sort_key for o in kv[1]), kv[0]))
    return {venue: sorted(obs, key=lambda o: o.summary)
            for venue, obs in ordered}


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
        for venue, listings in venues_for_item(obs_list).items():
            for i, obs in enumerate(listings):
                marker = "" if obs.positive else " _(informational)_"
                prefix = f"  - {venue}: " if i == 0 else "    - also: "
                lines.append(f"{prefix}{obs.summary}{marker}")
        for obs in obs_list:
            if not obs.track and not obs.venue:
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
            headline = first_line(r.error)
            # How long it's been broken is the part that decides whether to
            # act: one flaky run reads the same as a week of Cloudflare 403s
            # without it.
            runs = (state.health.get(r.source) or {}).get("runs")
            age = _failing_age(state.failing_since(r.source))
            plural = "" if runs == 1 else "s"
            since = f" (failing {age}, {runs} run{plural})" if age and runs else ""
            lines.append(f"- ❌ `{r.source}`{since}: {headline}")
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


def _failing_age(since: str | None) -> str | None:
    """"3d" / "5h" since a source first errored, for the status line."""
    if not since:
        return None
    try:
        started = datetime.fromisoformat(since)
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - started
    hours = int(delta.total_seconds() // 3600)
    return f"{delta.days}d" if delta.days else f"{hours}h"
