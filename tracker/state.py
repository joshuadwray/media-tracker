"""Seen-event state: what we've already notified about.

The state file maps observation fingerprints to {first, last} timestamp
dicts.  A fingerprint is "new" if unseen or if its last-seen timestamp
is older than GAP_DAYS — this re-notifies when an item disappears and
reappears (e.g. a library book's consortium copy returns from loan).
Entries are pruned after PRUNE_DAYS (based on last-seen) to keep the
file small.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Observation

PRUNE_DAYS = 180
GAP_DAYS = 2


class State:
    def __init__(self, path: Path):
        self.path = path
        self.seen: dict[str, dict[str, str]] = {}
        self.meta: dict = {}
        if path.exists():
            try:
                data = json.loads(path.read_text())
                self.seen = data.get("seen", {})
                self.meta = data.get("meta", {})
            except (json.JSONDecodeError, OSError):
                self.seen = {}
                self.meta = {}
        # Migrate old string values to {first, last} dicts.
        for fp, val in self.seen.items():
            if isinstance(val, str):
                self.seen[fp] = {"first": val, "last": val}

    def is_new(self, obs: Observation, now: datetime | None = None) -> bool:
        entry = self.seen.get(obs.fingerprint)
        if entry is None:
            return True
        last = _parse(entry["last"])
        gap = (now or datetime.now(timezone.utc)) - last
        return gap > timedelta(days=GAP_DAYS)

    def record(self, obs: Observation, now: datetime | None = None,
               dates: list[str] | None = None) -> None:
        ts = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        entry: dict[str, object] = {"first": ts, "last": ts}
        if dates:
            entry["dates"] = sorted(set(dates))
        self.seen[obs.fingerprint] = entry

    def touch(self, obs: Observation, now: datetime | None = None,
              dates: list[str] | None = None) -> None:
        ts = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        entry = self.seen.get(obs.fingerprint)
        if entry is not None:
            entry["last"] = ts
            if dates:
                old = set(entry.get("dates") or [])
                entry["dates"] = sorted(old | set(dates))

    def forget_item(self, item_key: str) -> int:
        """Drop every fingerprint belonging to one watchlist item.

        Called when an item leaves the watchlist: without this its
        fingerprints linger for PRUNE_DAYS, and re-adding the same title
        within that window reuses the same item_key — so `is_new` would
        suppress the very "it's in the catalog!" push you re-added it for.
        """
        doomed = [
            fp for fp in self.seen
            if len(fp.split("|", 2)) == 3 and fp.split("|", 2)[1] == item_key
        ]
        for fp in doomed:
            del self.seen[fp]
        return len(doomed)

    def prune(self, now: datetime | None = None) -> int:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=PRUNE_DAYS)
        stale = [
            fp for fp, entry in self.seen.items()
            if _parse(entry["last"]) < cutoff
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
