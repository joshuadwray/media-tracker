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

    Observations carrying a `medium` are grouped per (item, medium) and
    deduped against state.media — a book is announced once per medium,
    however many libraries turn out to have it, and staying present at
    one library keeps the medium quiet at the others. Everything else
    (movies, showtimes) keeps its per-observation behavior: those are
    already deduped per fingerprint, so their `new` list is the answer.
    """
    grouped: OrderedDict[str, NotifyGroup] = OrderedDict()
    for r in run.results:
        for obs in r.observations:
            if not obs.medium:
                continue
            key = f"{obs.item_key}|{obs.medium}"
            group = grouped.get(key)
            if group is None:
                group = grouped[key] = NotifyGroup(
                    item_key=obs.item_key, item_label=obs.item_label,
                    medium=obs.medium,
                )
            group.observations.append(obs)

    out: list[NotifyGroup] = []
    for key, group in grouped.items():
        if state.media_is_new(key):
            state.media_record(key)
            out.append(group)
        elif any(o.source in ok_sources for o in group.observations):
            state.media_touch(key)

    out.extend(
        NotifyGroup(item_key=o.item_key, item_label=o.item_label,
                    medium=None, observations=[o])
        for o in run.new if not o.medium
    )
    return out
