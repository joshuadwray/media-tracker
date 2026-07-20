"""The check run, callable from both the CLI and the web app."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field

from . import notify
from .config import Config
from .dashboard import build_dashboard
from .models import Observation, SourceResult
from .report import build_report
from .sources import build_sources
from .state import State


@dataclass
class CheckRun:
    results: list[SourceResult] = field(default_factory=list)
    new: list[Observation] = field(default_factory=list)
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
            if state.is_new(obs):
                run.new.append(obs)
                state.record(obs)
            elif r.source in ok_sources:
                state.touch(obs)
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

    if run.new and not no_notify:
        if notify.push_configured():
            try:
                notify.send_push(run.new)
                run.pushed = True
            except Exception as exc:  # noqa: BLE001 — a failed push shouldn't fail the run
                run.push_error = str(exc)
                print(f"WARNING: ntfy push failed: {exc}", file=sys.stderr)
        else:
            run.push_error = "NTFY_TOPIC not set"
    return run
