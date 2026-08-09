"""Seen-event state: what we've already notified about.

Two maps, both mapping a key to {first, last} timestamps:

  seen   — one entry per observation fingerprint (source|item|event).
           Drives the dashboard and report: per-library detail.
  media  — one entry per (item_key, medium). Drives *notifications*:
           a book carried by three libraries in one medium is one push,
           not three, and only when that medium goes unseen -> seen.

A key is "new" if unseen or if its last-seen timestamp is older than
GAP_DAYS — this re-notifies when an item disappears and reappears (e.g.
a library book's consortium copy returns from loan). For `media` that
means "absent from every library for GAP_DAYS", which is the right
reading of "it's back". Entries are pruned after PRUNE_DAYS (based on
last-seen) to keep the file small.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Observation

PRUNE_DAYS = 180
GAP_DAYS = 2

# Legacy `seen` events (f"{format} in catalog") -> medium, used once to seed
# the media map on upgrade. Without this the first run after the upgrade
# would push every book/medium already sitting in a catalog.
_LEGACY_EVENT_SUFFIX = " in catalog"


class State:
    def __init__(self, path: Path):
        self.path = path
        self.seen: dict[str, dict[str, str]] = {}
        self.media: dict[str, dict[str, str]] = {}
        self.meta: dict = {}
        if path.exists():
            try:
                data = json.loads(path.read_text())
                self.seen = data.get("seen", {})
                self.media = data.get("media", {})
                self.meta = data.get("meta", {})
            except (json.JSONDecodeError, OSError):
                self.seen = {}
                self.media = {}
                self.meta = {}
        # Migrate old string values to {first, last} dicts.
        for fp, val in self.seen.items():
            if isinstance(val, str):
                self.seen[fp] = {"first": val, "last": val}
        if not self.media and self.seen:
            self._seed_media_from_seen()

    def _seed_media_from_seen(self) -> None:
        """One-time backfill of the media map from per-source history."""
        from .models import medium_for

        for fp, entry in self.seen.items():
            parts = fp.split("|", 2)
            if len(parts) < 3 or not parts[1].startswith("book:"):
                continue
            _, item_key, event = parts
            if not event.endswith(_LEGACY_EVENT_SUFFIX):
                continue
            medium = medium_for(event[: -len(_LEGACY_EVENT_SUFFIX)])
            if not medium:
                continue
            key = f"{item_key}|{medium}"
            old = self.media.get(key)
            # Widest window wins: earliest first, latest last.
            self.media[key] = {
                "first": min(entry["first"], old["first"]) if old else entry["first"],
                "last": max(entry["last"], old["last"]) if old else entry["last"],
            }

    # --- per-observation (dashboard/report) ---------------------------

    def is_new(self, obs: Observation, now: datetime | None = None) -> bool:
        return _is_new(self.seen, obs.fingerprint, now)

    def record(self, obs: Observation, now: datetime | None = None,
               dates: list[str] | None = None) -> None:
        _record(self.seen, obs.fingerprint, now, dates)

    def touch(self, obs: Observation, now: datetime | None = None,
              dates: list[str] | None = None) -> None:
        _touch(self.seen, obs.fingerprint, now, dates)

    # --- per (item, medium) (notifications) ---------------------------

    def media_is_new(self, key: str, now: datetime | None = None) -> bool:
        return _is_new(self.media, key, now)

    def media_record(self, key: str, now: datetime | None = None) -> None:
        _record(self.media, key, now)

    def media_touch(self, key: str, now: datetime | None = None) -> None:
        _touch(self.media, key, now)

    # --- maintenance --------------------------------------------------

    def forget_item(self, item_key: str) -> int:
        """Drop every entry belonging to one watchlist item.

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
        for key in [k for k in self.media if k.rsplit("|", 1)[0] == item_key]:
            del self.media[key]
            doomed.append(key)
        return len(doomed)

    def prune(self, now: datetime | None = None) -> int:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=PRUNE_DAYS)
        pruned = 0
        for table in (self.seen, self.media):
            stale = [k for k, entry in table.items() if _parse(entry["last"]) < cutoff]
            for k in stale:
                del table[k]
            pruned += len(stale)
        return pruned

    def save(self, now: datetime | None = None) -> None:
        self.meta["last_run"] = (now or datetime.now(timezone.utc)).isoformat(
            timespec="seconds"
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"meta": self.meta, "seen": self.seen, "media": self.media},
                       indent=2, sort_keys=True)
            + "\n"
        )


def _is_new(table: dict, key: str, now: datetime | None = None) -> bool:
    entry = table.get(key)
    if entry is None:
        return True
    gap = (now or datetime.now(timezone.utc)) - _parse(entry["last"])
    return gap > timedelta(days=GAP_DAYS)


def _record(table: dict, key: str, now: datetime | None = None,
            dates: list[str] | None = None) -> None:
    ts = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    entry: dict[str, object] = {"first": ts, "last": ts}
    if dates:
        entry["dates"] = sorted(set(dates))
    table[key] = entry


def _touch(table: dict, key: str, now: datetime | None = None,
           dates: list[str] | None = None) -> None:
    ts = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    entry = table.get(key)
    if entry is not None:
        entry["last"] = ts
        if dates:
            old = set(entry.get("dates") or [])
            entry["dates"] = sorted(old | set(dates))


def _parse(ts: str) -> datetime:
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)
