"""Seen-event state: what we've already notified about.

Three maps, all mapping a key to {first, last} timestamps:

  seen   — one entry per observation fingerprint (source|item|event).
           Drives the dashboard and report: per-library, per-theatre,
           per-showtime-date detail, all of it still visible there.
  media  — one entry per (item_key, track). Drives book *notifications*:
           a book carried by three libraries in one track is one push,
           not three, and only when that track goes unseen -> seen.
           Also carries `best`, the shortest wait-bucket the track has
           ever reached (see availability), so a queue that gets
           materially shorter can speak up a second time without every
           copy-count wobble doing the same.
  venues — one entry per (item_key, venue). The same idea for films:
           a theatre is announced the first time it has the film and
           then stays quiet, however the listing is worded and however
           many days of showtimes go on sale afterwards.
  films  — one entry per item_key, carrying `best`: the best venue tier
           the film has ever been found at (see models.VENUE_TIERS).
           Venues alone said every theatre was equally worth hearing
           about, so a wide release drip-fed a push a day as it worked
           across the metroplex. This is the venue half of media's
           `best`: a theatre only speaks if it beats the tier already on
           record, so Angelika then Northpark is one push and Angelika
           then Cinemark Denton is two.

Plus one flat map, item_key -> ISO timestamp:

  watching — when each item joined the watchlist. Items that have never
           matched anywhere are reported as "still looking", and how long
           they've been looking is the difference between a title the
           libraries haven't bought yet and one that's misspelled. Unlike
           the other two, it survives an item leaving the list (see
           note_watching) and is pruned by age alone.

One more map, source_id -> failure record:

  health — how many consecutive runs a source has errored on, and
           whether we've already said so. A source that dies stays
           silent otherwise: partial failures don't fail the run (sites
           flake constantly) and errors only ever reached state/report.md,
           which nobody reads on a phone. AMC went dark behind Cloudflare
           on 2026-09-02 and was still dark seven days later, found only
           because someone thought to ask.

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

# Consecutive failed runs before a source is called dead. Two runs a day,
# so this is roughly a day and a half — long enough to ride out a flaky
# afternoon, short enough that a season's openings aren't missed.
DEAD_AFTER_RUNS = 3

# Legacy `seen` events (f"{format} in catalog") -> medium, used once to seed
# the media map on upgrade. Without this the first run after the upgrade
# would push every book/medium already sitting in a catalog.
_LEGACY_EVENT_SUFFIX = " in catalog"


class State:
    def __init__(self, path: Path):
        self.path = path
        self.seen: dict[str, dict[str, str]] = {}
        self.media: dict[str, dict[str, str]] = {}
        self.venues: dict[str, dict[str, str]] = {}
        self.films: dict[str, dict[str, str]] = {}
        self.watching: dict[str, str] = {}
        self.health: dict[str, dict] = {}
        self.meta: dict = {}
        if path.exists():
            try:
                data = json.loads(path.read_text())
                self.seen = data.get("seen", {})
                self.media = data.get("media", {})
                self.venues = data.get("venues", {})
                self.films = data.get("films", {})
                self.watching = data.get("watching", {})
                self.health = data.get("health", {})
                self.meta = data.get("meta", {})
            except (json.JSONDecodeError, OSError):
                self.seen = {}
                self.media = {}
                self.venues = {}
                self.films = {}
                self.watching = {}
                self.health = {}
                self.meta = {}
        # Migrate old string values to {first, last} dicts.
        for fp, val in self.seen.items():
            if isinstance(val, str):
                self.seen[fp] = {"first": val, "last": val}
        if not self.media and self.seen:
            self._seed_media_from_seen()
        self._seed_tracks_from_media()

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

    def _seed_tracks_from_media(self) -> None:
        """Fold per-medium keys into per-track keys.

        The media map used to be keyed by medium (print/ebook/audiobook);
        it's now keyed by track (reading/listening). Without this the first
        run after the upgrade would find every track unseen and push the
        whole watchlist. Widest window wins, same as the legacy seed.

        Idempotent: keys already ending in a track name are left alone, so
        this is a no-op on every subsequent run.
        """
        from .models import TRACKS, track_for

        for key in [k for k in self.media if k.rsplit("|", 1)[-1] not in TRACKS]:
            entry = self.media.pop(key)
            item_key, _, medium = key.rpartition("|")
            track = track_for(medium)
            if not track:
                continue  # a medium with no track (dvd, music-cd): drop it
            new_key = f"{item_key}|{track}"
            old = self.media.get(new_key)
            self.media[new_key] = {
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

    # --- per (item, track) (notifications) ----------------------------

    def media_is_new(self, key: str, now: datetime | None = None) -> bool:
        return _is_new(self.media, key, now)

    def media_record(self, key: str, now: datetime | None = None) -> None:
        _record(self.media, key, now)

    def media_touch(self, key: str, now: datetime | None = None) -> None:
        _touch(self.media, key, now)

    def media_best(self, key: str) -> str | None:
        entry = self.media.get(key)
        return entry.get("best") if entry else None

    def media_improves(self, key: str, bucket: str | None) -> bool:
        """Is this wait better than the best we've ever recorded here?

        False the first time we learn a track's wait — the discovery push
        already covered the book, and "it's a 400-day queue" isn't a second
        piece of news. From then on, only genuine improvements speak.
        """
        from .availability import improves
        if bucket is None:
            return False
        return improves(bucket, self.media_best(key))

    def media_set_best(self, key: str, bucket: str | None) -> None:
        """Ratchet the watermark. Only ever moves toward a shorter wait, so
        a title going back on hold doesn't re-arm the notification."""
        from .availability import UNKNOWN, better
        if bucket is None or bucket == UNKNOWN:
            return
        entry = self.media.get(key)
        if entry is not None:
            entry["best"] = better(bucket, entry.get("best"))

    # --- per (item, venue) (notifications) ----------------------------

    # No migration seeds this map, unlike media: the engine only pushes a
    # newly-recorded venue when one of its observations is itself new, so
    # the first run after this shipped recorded every theatre already
    # playing a film silently. See engine._notify_groups.

    def venue_is_new(self, key: str, now: datetime | None = None) -> bool:
        return _is_new(self.venues, key, now)

    def venue_record(self, key: str, now: datetime | None = None) -> None:
        _record(self.venues, key, now)

    def venue_touch(self, key: str, now: datetime | None = None) -> None:
        _touch(self.venues, key, now)

    # --- per item, best venue tier reached (notifications) ------------

    # Seeded by the same trick as venues, and for the same reason: the
    # engine only pushes a film whose candidate theatres are themselves
    # new, so the first run after this shipped recorded every film already
    # playing at its current tier without announcing any of them.

    def film_is_new(self, item_key: str, now: datetime | None = None) -> bool:
        return _is_new(self.films, item_key, now)

    def film_record(self, item_key: str, now: datetime | None = None) -> None:
        _record(self.films, item_key, now)

    def film_touch(self, item_key: str, now: datetime | None = None) -> None:
        _touch(self.films, item_key, now)

    def film_best(self, item_key: str) -> str | None:
        entry = self.films.get(item_key)
        return entry.get("best") if entry else None

    def film_improves(self, item_key: str, tier: str | None) -> bool:
        """Is this theatre better than the best we've ever recorded here?

        False the first time we learn where a film is playing — the debut
        push already covered the film, and naming its tier isn't a second
        piece of news.
        """
        from .models import venue_improves
        return venue_improves(tier, self.film_best(item_key))

    def film_set_best(self, item_key: str, tier: str | None) -> None:
        """Ratchet the watermark. Only ever moves toward a better tier, so a
        film leaving Denton and playing on in Dallas doesn't re-arm it."""
        from .models import better_venue
        if not tier:
            return
        entry = self.films.get(item_key)
        if entry is not None:
            entry["best"] = better_venue(tier, entry.get("best"))

    # --- how long an item has been on the watchlist -------------------

    def note_watching(self, item_keys: list[str], now: datetime | None = None) -> None:
        """Stamp new watchlist items and age out long-departed ones.

        Called once per run, before the report is built. The stamp is the
        first run that saw the item, not the moment it was added — close
        enough at a 4x-daily cadence, and the only thing we can observe.
        The existing entries were backfilled from watchlist.yaml's git
        history; CI checks out shallow, so the code can't do that itself.

        A departed item keeps its stamp until PRUNE_DAYS have passed. Fixing
        an entry from the phone is a remove plus an add, and that shouldn't
        rewrite how long you've been waiting for the book — but a title you
        dropped half a year ago and picked up again is a fresh wait.
        """
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=PRUNE_DAYS)
        ts = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        keys = set(item_keys)
        for key in keys:
            self.watching.setdefault(key, ts)
        for gone in [k for k, v in self.watching.items()
                     if k not in keys and _parse(v) < cutoff]:
            del self.watching[gone]

    def waiting_days(self, item_key: str, now: datetime | None = None) -> int | None:
        """Days since this item joined the watchlist, or None if unstamped."""
        ts = self.watching.get(item_key)
        if not ts:
            return None
        return max(0, ((now or datetime.now(timezone.utc)) - _parse(ts)).days)

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
        for table in (self.media, self.venues):
            for key in [k for k in table if k.rsplit("|", 1)[0] == item_key]:
                del table[key]
                doomed.append(key)
        # films is keyed by the bare item_key, so the rsplit above misses it.
        if self.films.pop(item_key, None) is not None:
            doomed.append(item_key)
        # The `watching` stamp deliberately survives: this is called on
        # removal, and half the removals here are an edit in disguise.
        return len(doomed)

    def note_source_result(self, source_id: str, error: str | None,
                           now: datetime | None = None) -> str | None:
        """Record one run's outcome for a source; return an alarm to push.

        Returns "died" the run a source crosses DEAD_AFTER_RUNS (once per
        outage, never again while it stays down), "recovered" the run it
        comes back after having been reported dead, and None otherwise --
        which is the answer for a single flaky run, the normal case.
        """
        stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        rec = self.health.get(source_id) or {}
        if error is None:
            self.health.pop(source_id, None)
            return "recovered" if rec.get("alerted") else None
        runs = int(rec.get("runs", 0)) + 1
        self.health[source_id] = {
            "runs": runs,
            "since": rec.get("since") or stamp,
            # First line only. Source errors carry a traceback, and this
            # file is committed by CI on every run — the full frame stack
            # bloats the diff and tells a reader nothing the first line
            # doesn't. It's also what the phone push quotes.
            "last_error": first_line(error),
            "alerted": bool(rec.get("alerted")) or runs >= DEAD_AFTER_RUNS,
        }
        if runs >= DEAD_AFTER_RUNS and not rec.get("alerted"):
            return "died"
        return None

    def last_error(self, source_id: str) -> str | None:
        rec = self.health.get(source_id)
        return rec.get("last_error") if rec else None

    def failing_since(self, source_id: str) -> str | None:
        rec = self.health.get(source_id)
        return rec.get("since") if rec else None

    def prune(self, now: datetime | None = None) -> int:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=PRUNE_DAYS)
        pruned = 0
        for table in (self.seen, self.media, self.venues, self.films):
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
            json.dumps({"meta": self.meta, "seen": self.seen, "media": self.media,
                        "venues": self.venues, "films": self.films,
                        "watching": self.watching, "health": self.health},
                       indent=2, sort_keys=True)
            + "\n"
        )


def first_line(text: str) -> str:
    """The headline of a multi-line error, for state and notifications."""
    stripped = (text or "").strip()
    return stripped.splitlines()[0] if stripped else ""


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
