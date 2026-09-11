"""Source adapter interface.

A source knows how to check the watchlist against one upstream system
(a library catalog, a theater's schedule, ...) and return Observations.
Sources must never raise out of check(): network flakiness at one
source shouldn't kill the whole run.
"""
from __future__ import annotations

import os
import traceback
from abc import ABC, abstractmethod
from typing import Any

from ..config import Config
from ..models import Observation, SourceResult


class Source(ABC):
    #: registry key used in watchlist.yaml, e.g. "bibliocommons"
    kind: str = ""
    #: lending period in days, used to turn a hold queue into a wait. Override
    #: per kind; `loan_days` in watchlist.yaml wins over both.
    default_loan_days: int = 21

    def __init__(self, source_id: str, cfg: dict[str, Any]):
        self.source_id = source_id
        self.cfg = cfg

    @property
    def label(self) -> str:
        """The library as a human would name it. Source ids leak into pushes
        otherwise ("ebook at libby-houston")."""
        return self.cfg.get("label") or self.source_id

    @property
    def loan_days(self) -> int:
        return int(self.cfg.get("loan_days") or self.default_loan_days)

    @property
    def distance_mi(self) -> float:
        """How far the physical copies are. Digital stays 0 — it comes to you,
        so it never loses a tiebreak to a branch down the road."""
        return float(self.cfg.get("distance_mi") or 0.0)

    @property
    def tier(self) -> str:
        """How much you'd rather this venue than another (see VENUE_TIERS).

        Libraries leave it at the default and rank on the wait instead. For a
        chain this is the whole chain's standing, which each theatre may
        override — see venue_meta.
        """
        return self.cfg.get("tier") or "other"

    def venue_meta(self, entry: dict) -> tuple[str, float]:
        """Tier and distance for one theatre in a `theatres:`/`pages:` list.

        The entry's own values win over the source's, so a chain declares its
        standing once and the one house in town corrects it.
        """
        distance = entry.get("distance_mi")
        return (entry.get("tier") or self.tier,
                float(self.distance_mi if distance is None else distance))

    @abstractmethod
    def check(self, config: Config) -> list[Observation]:
        """Query the upstream system for everything relevant on the watchlist."""

    def probe(self, config: Config, query: str | None = None) -> str:
        """Return raw diagnostic output for endpoint/selector debugging."""
        return "probe not implemented for this source"

    def run(self, config: Config) -> SourceResult:
        try:
            return SourceResult(source=self.source_id, observations=self.check(config))
        except Exception as exc:  # noqa: BLE001 — isolate per-source failures
            tb = traceback.format_exc(limit=3)
            return SourceResult(
                source=self.source_id,
                error=f"{type(exc).__name__}: {exc}\n{tb}",
            )


_REGISTRY: dict[str, type[Source]] = {}


def register(cls: type[Source]) -> type[Source]:
    if not cls.kind:
        raise ValueError(f"{cls.__name__} must set a 'kind'")
    _REGISTRY[cls.kind] = cls
    return cls


def build_sources(config: Config) -> list[Source]:
    sources: list[Source] = []
    for sid, cfg in config.enabled_sources().items():
        kind = cfg.get("kind")
        if kind not in _REGISTRY:
            raise ValueError(
                f"source '{sid}' has unknown kind '{kind}'. "
                f"Known kinds: {sorted(_REGISTRY)}"
            )
        sources.append(_REGISTRY[kind](sid, cfg))

    # Auto-enable tmdb-streaming when streaming services are configured
    # and TMDB_API_KEY is available (no manual sources: entry needed).
    tmdb_kind = "tmdb-streaming"
    already = any(s.kind == tmdb_kind for s in sources)
    if (not already
            and config.streaming and config.streaming.services
            and os.environ.get("TMDB_API_KEY")):
        sources.append(_REGISTRY[tmdb_kind]("tmdb-streaming", {"kind": tmdb_kind}))

    return sources
