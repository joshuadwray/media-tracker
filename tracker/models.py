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

    @property
    def fingerprint(self) -> str:
        return f"{self.source}|{self.item_key}|{self.event or self.summary}"


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
