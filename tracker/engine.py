"""The check run, callable from both the CLI and the web app."""
from __future__ import annotations

import sys
from collections import OrderedDict
from dataclasses import dataclass, field

from . import notify
from .config import Config
from .dashboard import build_dashboard
from .models import NotifyGroup, Observation, SourceResult
from .report import build_report
from .sources import build_sources
from .state import State


@dataclass
class CheckRun:
    results: list[SourceResult] = field(default_factory=list)
    new: list[Observation] = field(default_factory=list)
    notify_groups: list[NotifyGroup] = field(default_factory=list)
    report: str = ""
    pushed: bool = False
    push_error: str | None = None

    @property
    def all_failed(self) -> bool:
        return bool(self.results) and all(r.error for r in self.results)


def run_check(config: Config, *, source_id: str | None = None,
              dry_run: bool = False, no_notify: bool = False) -> CheckRun:
    sources = build_sources(config)
    if source_id:
        sources = [s for s in sources if s.source_id == source_id]
        if not sources:
            raise ValueError(f"no enabled source with id '{source_id}'")

    state = State(config.state_path)
    run = CheckRun(results=[s.run(config) for s in sources])
    ok_sources = {r.source for r in run.results if not r.error}
    for r in run.results:
        for obs in r.observations:
            obs_dates = obs.detail.get("dates") if isinstance(obs.detail, dict) else None
            if state.is_new(obs):
                run.new.append(obs)
                state.record(obs, dates=obs_dates)
            elif r.source in ok_sources:
                state.touch(obs, dates=obs_dates)
    run.notify_groups = _notify_groups(run, state, ok_sources)
    state.prune()
    # Before the report: it dates the "still looking" entries from this map,
    # and an item added since the last run should read 0d, not blank.
    state.note_watching([i.key for i in (*config.books, *config.movies)])
    run.report = build_report(config, run.results, run.new, state)

    if dry_run:
        return run

    state_dir = config.state_path.parent
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "report.md").write_text(run.report)

    docs_dir = state_dir.parent / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "index.html").write_text(
        build_dashboard(config, run.results, run.new, state)
    )
    state.save()

    if run.notify_groups and not no_notify:
        if notify.push_configured():
            try:
                notify.send_push(run.notify_groups)
                run.pushed = True
            except Exception as exc:  # noqa: BLE001 — a failed push shouldn't fail the run
                run.push_error = str(exc)
                print(f"WARNING: ntfy push failed: {exc}", file=sys.stderr)
        else:
            run.push_error = "NTFY_TOPIC not set"
    return run


def _notify_groups(run: CheckRun, state: State,
                   ok_sources: set[str]) -> list[NotifyGroup]:
    """Decide what to push about.

    Observations carrying a `track` are grouped per (item, track) and deduped
    against state.media. Two things can make a track worth a push:

      found  — the track went unseen -> seen. A book is announced once per
               track however many libraries turn out to have it, and staying
               present at one library keeps the track quiet at the others.
      sooner — the track was already known, but the shortest wait among its
               options dropped a whole bucket (see availability). This is the
               one that earns its keep at a Libby-sized library, where a title
               can sit in the catalog for months behind a queue you'd never
               join, and then suddenly not be.

    Observations carrying a `venue` are the movie half of the same idea,
    grouped per (item, venue) and deduped against state.venues: a theatre is
    announced the first time it has the film and then stays quiet. Without
    it, one film generated a push per title variant ("... Early Access"),
    another when "advance tickets on sale" became "playing", and another for
    every new day of showtimes at a source that lists them per date.

    Everything else — streaming services, VOD release dates, a book in a
    medium with no track — keeps its per-observation behavior: those are
    already deduped per fingerprint, so their `new` list is the answer.
    """
    grouped: OrderedDict[str, NotifyGroup] = OrderedDict()
    for r in run.results:
        for obs in r.observations:
            if not obs.track:
                continue
            key = f"{obs.item_key}|{obs.track}"
            group = grouped.get(key)
            if group is None:
                group = grouped[key] = NotifyGroup(
                    item_key=obs.item_key, item_label=obs.item_label,
                    track=obs.track,
                )
            group.observations.append(obs)

    # A book we've never seen in any track is a first discovery: say it once,
    # listing everything found, rather than firing reading and listening as
    # two separate pushes for the same news. Later tracks still ping
    # individually, because by then the book itself isn't the news.
    first_seen = {
        g.item_key for g in grouped.values()
        if not any(k.rsplit("|", 1)[0] == g.item_key for k in state.media)
    }

    out: list[NotifyGroup] = []
    debuts: OrderedDict[str, NotifyGroup] = OrderedDict()
    for key, group in grouped.items():
        bucket = group.best_bucket
        if not state.media_is_new(key):
            if any(o.source in ok_sources for o in group.observations):
                state.media_touch(key)
            if state.media_improves(key, bucket):
                group.reason = "sooner"
                out.append(group)
            state.media_set_best(key, bucket)
            continue
        state.media_record(key)
        state.media_set_best(key, bucket)
        if group.item_key in first_seen:
            debut = debuts.get(group.item_key)
            if debut is None:
                debut = debuts[group.item_key] = NotifyGroup(
                    item_key=group.item_key, item_label=group.item_label,
                    track=None,
                )
            debut.observations.extend(group.observations)
        else:
            out.append(group)
    out.extend(debuts.values())

    out.extend(_venue_groups(run, state, ok_sources))

    out.extend(
        NotifyGroup(item_key=o.item_key, item_label=o.item_label,
                    track=None, observations=[o])
        for o in run.new if not o.track and not o.venue
    )
    return out


def _venue_groups(run: CheckRun, state: State,
                  ok_sources: set[str]) -> list[NotifyGroup]:
    """One push per film per run, naming every theatre newly found in it.

    A venue speaks once. The decision needs two things to be true: the
    (item, venue) key is new to state.venues, AND at least one of that
    venue's observations is in run.new.

    The second clause is what let this ship without a migration. On the
    first run after it landed, every theatre already playing a watched film
    had its fingerprint in `seen`, so nothing was in run.new, so those venue
    keys were recorded silently instead of re-announcing the watchlist. It
    stays right afterwards, too: a theatre that genuinely just picked up a
    film always has a new fingerprint, and a venue key ages out on exactly
    the same timestamps as the fingerprints that feed it.
    """
    by_venue: OrderedDict[str, list[Observation]] = OrderedDict()
    for r in run.results:
        for obs in r.observations:
            if not obs.venue or obs.track:
                continue
            by_venue.setdefault(f"{obs.item_key}|{obs.venue}", []).append(obs)

    fresh = {o.fingerprint for o in run.new}
    films: OrderedDict[str, NotifyGroup] = OrderedDict()
    for key, obs_list in by_venue.items():
        if not state.venue_is_new(key):
            if any(o.source in ok_sources for o in obs_list):
                state.venue_touch(key)
            continue
        state.venue_record(key)
        if not any(o.fingerprint in fresh for o in obs_list):
            continue  # already known under some other wording — seed only
        first = obs_list[0]
        group = films.get(first.item_key)
        if group is None:
            group = films[first.item_key] = NotifyGroup(
                item_key=first.item_key, item_label=first.item_label,
                track=None,
            )
        group.observations.append(first)
    return list(films.values())
