"""Load watchlist.yaml into models + source configs."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .models import WatchBook, WatchMovie

DEFAULT_WATCHLIST = Path(__file__).resolve().parent.parent / "watchlist.yaml"
DEFAULT_STATE = Path(__file__).resolve().parent.parent / "state" / "state.json"


@dataclass
class Config:
    books: list[WatchBook] = field(default_factory=list)
    movies: list[WatchMovie] = field(default_factory=list)
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    state_path: Path = DEFAULT_STATE

    def enabled_sources(self) -> dict[str, dict[str, Any]]:
        return {
            sid: cfg for sid, cfg in self.sources.items()
            if cfg.get("enabled", True)
        }


def load_config(path: str | Path | None = None) -> Config:
    p = Path(path) if path else DEFAULT_WATCHLIST
    if not p.exists():
        raise FileNotFoundError(f"watchlist file not found: {p}")
    raw = yaml.safe_load(p.read_text()) or {}

    books = [
        WatchBook(
            title=_req(b, "title", "books"),
            author=b.get("author"),
            isbn=str(b["isbn"]) if b.get("isbn") else None,
            bib_id=str(b["bib_id"]) if b.get("bib_id") else None,
            notes=b.get("notes"),
        )
        for b in raw.get("books") or []
    ]
    movies = [
        WatchMovie(
            title=_req(m, "title", "movies"),
            year=m.get("year"),
            notes=m.get("notes"),
        )
        for m in raw.get("movies") or []
    ]

    sources = raw.get("sources") or {}
    if not isinstance(sources, dict):
        raise ValueError("'sources' must be a mapping of source-id -> config")

    state_path = Path(raw.get("state_file", DEFAULT_STATE))
    if not state_path.is_absolute():
        state_path = p.parent / state_path

    return Config(books=books, movies=movies, sources=sources, state_path=state_path)


def _req(entry: Any, key: str, section: str) -> str:
    if not isinstance(entry, dict) or not entry.get(key):
        raise ValueError(f"every entry in '{section}' needs a '{key}': got {entry!r}")
    return str(entry[key])


def env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default
