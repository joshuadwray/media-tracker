"""Core data models for the media tracker."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


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
                                  # Notifications group by (item, medium), so a
                                  # book carried by three libraries in one
                                  # medium is one push, not three. None keeps
                                  # per-observation pushes (movies/showtimes).

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
    # cloudLibrary's fallback when a record doesn't say which it is
    "ebook/audio": "ebook-or-audiobook",
    "MUSIC_CD": "music-cd", "music CD": "music-cd",
    "DVD": "dvd", "BLURAY": "blu-ray", "Blu-ray": "blu-ray",
}


def medium_for(fmt: Optional[str]) -> Optional[str]:
    """Canonical medium for a source's format label, or None if unknown
    (unknown means "notify per observation", the old behavior)."""
    if not fmt:
        return None
    return MEDIUM_BY_FORMAT.get(fmt) or MEDIUM_BY_FORMAT.get(fmt.lower())


@dataclass
class NotifyGroup:
    """What one push is about: an item in one medium, plus every place it
    was spotted. Movies and showtimes get a single-observation group so
    their per-date notification behavior is unchanged."""
    item_key: str
    item_label: str
    medium: Optional[str]
    observations: list["Observation"] = field(default_factory=list)

    @property
    def state_key(self) -> str:
        return f"{self.item_key}|{self.medium}"

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
