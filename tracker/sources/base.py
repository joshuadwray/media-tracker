"""Source adapter interface.

A source knows how to check the watchlist against one upstream system
(a library catalog, a theater's schedule, ...) and return Observations.
Sources must never raise out of check(): network flakiness at one
source shouldn't kill the whole run.
"""
from __future__ import annotations

import traceback
from abc import ABC, abstractmethod
from typing import Any

from ..config import Config
from ..models import Observation, SourceResult


class Source(ABC):
    #: registry key used in watchlist.yaml, e.g. "bibliocommons"
    kind: str = ""

    def __init__(self, source_id: str, cfg: dict[str, Any]):
        self.source_id = source_id
        self.cfg = cfg

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
    return sources
