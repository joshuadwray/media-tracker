"""Core data models for the media tracker."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from . import availability


@dataclass
class WatchBook:
    title: str
    author: Optional[str] = None
    isbn: Optional[str] = None    # any edition's ISBN enables exact matching
    bib_id: Optional[str] = None  # BiblioCommons bib record id (from `tracker add`)
    notes: Optional[str] = None

    @property
    def key(self) -> str:
        return f"book:{normalize_key(self.title)}"

    def __str__(self) -> str:
        return f"{self.title}" + (f" — {self.author}" if self.author else "")


@dataclass
class WatchMovie:
    title: str
    year: Optional[int] = None
    notes: Optional[str] = None

    @property
    def key(self) -> str:
        return f"movie:{normalize_key(self.title)}"

    def __str__(self) -> str:
        return f"{self.title}" + (f" ({self.year})" if self.year else "")


@dataclass
class Observation:
    """One sighting of a watched item at a source, in its current state.

    fingerprint identifies the *event* we'd notify about: the same
    fingerprint seen on a later run is old news and stays silent.
    """
    source: str            # source id, e.g. "denton-library"
    item_key: str          # WatchBook.key / WatchMovie.key
    item_label: str        # human-readable watched item
    summary: str           # one-line description of what was found
    url: Optional[str] = None
    detail: dict = field(default_factory=dict)  # source-specific extras
    positive: bool = True  # False = informational (e.g. "on order", "all copies out")
    event: Optional[str] = None  # stable identity for dedup; defaults to summary.
                                 # Set this when the summary contains volatile
                                 # detail (copy counts, statuses) that shouldn't
                                 # re-trigger a notification when it changes.
    medium: Optional[str] = None  # canonical format (see MEDIUM_BY_FORMAT).
                                  # Notifications group by (item, track), so a
                                  # book carried by three libraries in one
                                  # track is one push, not three. None keeps
                                  # per-observation pushes (movies/showtimes).
    wait: Optional[int] = None    # days until a copy could reach you, 0 if one
                                  # is free now; None when copies are unknown.
                                  # See availability.wait_days.
    distance_mi: float = 0.0      # how far the copy is. Digital is 0 — it comes
                                  # to you. Orders options within a track.
    loan_days: int = 0            # this library's lending period; 0 = not a
                                  # lending source (theatres, streaming).
    source_label: str = ""        # library as a human would name it. Pushes
                                  # used to print the source id.
    provisional: bool = False     # the wait is a best guess, not a figure we
                                  # stand behind (on-order titles, whose
                                  # arrival date nobody publishes). Good
                                  # enough to rank on, never to ratchet on.
    shelf: Optional[str] = None   # a no-hold shelf these copies sit in, named
                                  # as the library names it ("Lucky Day").
                                  # Nobody can queue ahead of you for one, so
                                  # it says how to read the wait rather than
                                  # changing it: a zero wait on a title with a
                                  # deep queue means a copy is free *now*, and
                                  # only a no-hold copy makes that zero
                                  # something you can still act on tomorrow.
    shelf_copies: int = 0         # how many of them are on that shelf this
                                  # minute. 0 with `shelf` set = the title
                                  # cycles through the collection but every
                                  # such copy is currently out.

    @property
    def where(self) -> str:
        return self.source_label or self.source

    @property
    def track(self) -> Optional[str]:
        return track_for(self.medium)

    @property
    def reachable(self) -> bool:
        """Close enough to count. A far physical copy is still reported, but
        it can't headline a track or satisfy its watermark."""
        return self.distance_mi <= availability.MAX_DISTANCE_MI

    @property
    def bucket(self) -> str:
        b = availability.bucket(self.wait, self.loan_days or 21)
        # An on-order title with an empty queue computes to a zero wait —
        # zero turns *after it arrives*. It still isn't in the building, so
        # it can never be "now", however short the queue.
        if self.provisional and b == "now":
            return "turn"
        return b

    @property
    def wait_text(self) -> str:
        """The wait as a person should read it.

        Provisional waits never render as a duration: we know the queue but
        not the arrival date, and "~3 wk" would be precise about the half
        we're missing. The summary carries the queue position instead.
        """
        return "on order" if self.provisional else availability.describe(self.wait)

    @property
    def sort_key(self) -> tuple:
        """Best option first: reachable, then soonest, then nearest.

        Distance is the last tiebreak, not the first — proximity orders
        equally-good options, but a copy two weeks sooner should still beat
        one down the road.
        """
        return (not self.reachable, availability.rank(self.bucket),
                self.wait if self.wait is not None else 10**6, self.distance_mi)

    @property
    def fingerprint(self) -> str:
        return f"{self.source}|{self.item_key}|{self.event or self.summary}"


# Source format label (or BiblioCommons code) -> canonical medium. Several
# libraries describe the same thing differently, and the medium is what the
# reader actually cares about ("is there an audiobook anywhere?"). These
# strings are notification keys — changing one re-notifies that medium once.
MEDIUM_BY_FORMAT = {
    "BK": "print", "PAPERBACK": "print", "print book": "print",
    "LPRINT": "large-print", "large print book": "large-print",
    "EBOOK": "ebook", "ebook": "ebook",
    "AUDIOBOOK": "audiobook", "audiobook": "audiobook",
    "AB": "audiobook-cd", "audiobook (CD)": "audiobook-cd",
    # cloudLibrary's fallback when a record doesn't say which it is. Vestigial
    # since we started reading its explicit `format` field, but kept so old
    # state keys still resolve to a track during migration.
    "ebook/audio": "ebook-or-audiobook",
    "MUSIC_CD": "music-cd", "music CD": "music-cd",
    "DVD": "dvd", "BLURAY": "blu-ray", "Blu-ray": "blu-ray",
}

# Medium -> the thing you actually decide between. Print and ebook are one
# choice, not two: either gets the book in front of your eyes, so they compete
# for the same slot and only the better option is worth a notification. Audio
# is a genuinely separate way to consume the book, and often a separate queue.
#
# These names are notification keys (state.media is keyed by item|track).
TRACK_BY_MEDIUM = {
    "print": "reading",
    "large-print": "reading",
    "ebook": "reading",
    "ebook-or-audiobook": "reading",  # vestigial; see MEDIUM_BY_FORMAT
    "audiobook": "listening",
    "audiobook-cd": "listening",
}

TRACKS = ("reading", "listening")


def medium_for(fmt: Optional[str]) -> Optional[str]:
    """Canonical medium for a source's format label, or None if unknown
    (unknown means "notify per observation", the old behavior)."""
    if not fmt:
        return None
    return MEDIUM_BY_FORMAT.get(fmt) or MEDIUM_BY_FORMAT.get(fmt.lower())


def track_for(medium: Optional[str]) -> Optional[str]:
    """Which track a medium belongs to, or None for things that aren't a
    book to read (DVDs, music) and for movies/showtimes."""
    if not medium:
        return None
    return TRACK_BY_MEDIUM.get(medium)


@dataclass
class NotifyGroup:
    """What one push is about: an item in one track, plus every place it was
    spotted. Movies and showtimes get a single-observation group so their
    per-date notification behavior is unchanged."""
    item_key: str
    item_label: str
    track: Optional[str]
    observations: list["Observation"] = field(default_factory=list)
    reason: str = "found"  # "found" = the track is newly in a catalog;
                           # "sooner" = it was already there and the wait
                           # dropped a bucket. Shapes the push wording.

    @property
    def state_key(self) -> str:
        return f"{self.item_key}|{self.track}"

    @property
    def options(self) -> list["Observation"]:
        """Every place it was spotted, best first."""
        return sorted(self.observations, key=lambda o: o.sort_key)

    @property
    def headline(self) -> Optional["Observation"]:
        """The one option worth acting on. Only reachable copies qualify —
        a print copy three states away shouldn't declare the track solved."""
        return next((o for o in self.options if o.reachable), None)

    @property
    def best_bucket(self) -> Optional[str]:
        """The bucket the watermark ratchets on.

        Provisional options are skipped entirely: an on-order title's wait
        is missing its arrival date, so promoting it to "the queue got
        shorter" would be announcing a number we made up. It can still
        headline the card — it just can't move the notification state.
        """
        firm = next((o for o in self.options if o.reachable and not o.provisional),
                    None)
        return firm.bucket if firm else None

    @property
    def sources(self) -> list[str]:
        seen: list[str] = []
        for obs in self.observations:
            if obs.source not in seen:
                seen.append(obs.source)
        return seen


@dataclass
class SourceResult:
    source: str
    observations: list[Observation] = field(default_factory=list)
    error: Optional[str] = None
    checked_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


def normalize_key(text: str) -> str:
    """Stable slug used in item keys and state entries."""
    out = []
    for ch in text.lower().strip():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")
