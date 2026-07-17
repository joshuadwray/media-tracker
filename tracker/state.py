"""Seen-event state: what we've already notified about.

The state file is a small JSON document mapping observation
fingerprints -> first-seen timestamp. A run only notifies about
fingerprints it hasn't recorded before, so re-running is always safe.
Entries are pruned after PRUNE_DAYS to keep the file small; if a
pruned availability re-appears months later it will simply re-notify,
which is the behavior you want anyway.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Observation

PRUNE_DAYS = 180


class State:
    def __init__(self, path: Path):
        self.path = path
        self.seen: dict[str, str] = {}
        self.meta: dict = {}
        if path.exists():
            try:
                data = json.loads(path.read_text())
                self.seen = data.get("seen", {})
                self.meta = data.get("meta", {})
            except (json.JSONDecodeError, OSError):
                # Corrupt state -> start fresh rather than crash the run.
                self.seen = {}
                self.meta = {}

    def is_new(self, obs: Observation) -> bool:
        return obs.fingerprint not in self.seen

    def record(self, obs: Observation, now: datetime | None = None) -> None:
        ts = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        self.seen.setdefault(obs.fingerprint, ts)

    def prune(self, now: datetime | None = None) -> int:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=PRUNE_DAYS)
        stale = [
            fp for fp, ts in self.seen.items()
            if _parse(ts) < cutoff
        ]
        for fp in stale:
            del self.seen[fp]
        return len(stale)

    def save(self, now: datetime | None = None) -> None:
        self.meta["last_run"] = (now or datetime.now(timezone.utc)).isoformat(
            timespec="seconds"
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"meta": self.meta, "seen": self.seen}, indent=2, sort_keys=True)
            + "\n"
        )


def _parse(ts: str) -> datetime:
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)
