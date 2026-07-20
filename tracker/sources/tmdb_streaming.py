"""TMDB-powered streaming/PVOD availability watcher.

Uses TMDB's watch/providers endpoint (powered by JustWatch) to detect
when watchlist movies appear on subscribed streaming services or become
available to rent/buy digitally.  Also checks release_dates for
announced Digital (type 4) releases.

Auto-enabled when ``streaming.services`` is configured in watchlist.yaml
and ``TMDB_API_KEY`` is set in the environment.  No manual ``sources:``
entry required.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .. import http
from ..config import Config, env
from ..matching import normalize
from ..models import Observation, WatchMovie
from .base import Source, register

TMDB_BASE = "https://api.themoviedb.org/3"
CACHE_FILE = Path(__file__).resolve().parent.parent.parent / "state" / "tmdb-cache.json"
CACHE_MAX_AGE = timedelta(days=7)


# ------------------------------------------------------------------
# TMDB ID cache
# ------------------------------------------------------------------

def _load_cache() -> dict[str, Any]:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_cache(cache: dict[str, Any]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n")


def _cache_fresh(entry: dict[str, Any]) -> bool:
    searched = entry.get("searched", "")
    try:
        return date.fromisoformat(searched) >= date.today() - CACHE_MAX_AGE
    except ValueError:
        return False


# ------------------------------------------------------------------
# TMDB API helpers
# ------------------------------------------------------------------

def _search_movie(sess: http.requests.Session, api_key: str,
                  movie: WatchMovie) -> int | None:
    params: dict[str, str] = {"api_key": api_key, "query": movie.title}
    if movie.year:
        params["year"] = str(movie.year)
    resp = http.get(sess, f"{TMDB_BASE}/search/movie", params=params)
    if resp.status_code != 200:
        return None
    results = resp.json().get("results") or []
    if not results:
        return None
    norm_title = normalize(movie.title)
    for r in results:
        if normalize(r.get("title", "")) == norm_title:
            return int(r["id"])
    return int(results[0]["id"])


def _get_providers(sess: http.requests.Session, api_key: str,
                   tmdb_id: int) -> dict[str, Any]:
    resp = http.get(sess, f"{TMDB_BASE}/movie/{tmdb_id}/watch/providers",
                    params={"api_key": api_key})
    if resp.status_code != 200:
        return {}
    return resp.json().get("results", {}).get("US") or {}


def _get_digital_release(sess: http.requests.Session, api_key: str,
                         tmdb_id: int) -> str | None:
    """Return the US Digital (type 4) release date as YYYY-MM-DD, or None."""
    resp = http.get(sess, f"{TMDB_BASE}/movie/{tmdb_id}/release_dates",
                    params={"api_key": api_key})
    if resp.status_code != 200:
        return None
    for country in resp.json().get("results") or []:
        if country.get("iso_3166_1") != "US":
            continue
        for rd in country.get("release_dates") or []:
            if rd.get("type") == 4:
                raw = rd.get("release_date", "")
                m = re.match(r"\d{4}-\d{2}-\d{2}", raw)
                return m.group(0) if m else None
    return None


# ------------------------------------------------------------------
# Observation helpers
# ------------------------------------------------------------------

def _provider_names(providers: list[dict]) -> list[str]:
    return [p.get("provider_name", "?") for p in providers]


def _tmdb_url(tmdb_id: int) -> str:
    return f"https://www.themoviedb.org/movie/{tmdb_id}"


# ------------------------------------------------------------------
# Source
# ------------------------------------------------------------------

@register
class TmdbStreamingSource(Source):
    kind = "tmdb-streaming"

    def check(self, config: Config) -> list[Observation]:
        api_key = env("TMDB_API_KEY")
        if not api_key:
            raise RuntimeError("TMDB_API_KEY not set")
        if not config.movies:
            return []

        services = [s.lower() for s in (config.streaming.services if config.streaming else [])]
        sess = http.session()
        cache = _load_cache()
        observations: list[Observation] = []

        for movie in config.movies:
            tmdb_id = _resolve_id(sess, api_key, movie, cache)
            if not tmdb_id:
                continue
            url = _tmdb_url(tmdb_id)

            # Watch providers
            providers = _get_providers(sess, api_key, tmdb_id)

            # Streaming (flatrate)
            for p in providers.get("flatrate") or []:
                name = p.get("provider_name", "?")
                if name.lower() in services:
                    observations.append(Observation(
                        source=self.source_id,
                        item_key=movie.key,
                        item_label=str(movie),
                        summary=f'"{movie.title}" now streaming on {name}',
                        event=f"{movie.title} streaming on {name}",
                        url=url,
                        positive=True,
                    ))

            # Rent
            rent = providers.get("rent") or []
            if rent:
                names = ", ".join(_provider_names(rent))
                observations.append(Observation(
                    source=self.source_id,
                    item_key=movie.key,
                    item_label=str(movie),
                    summary=f'"{movie.title}" available to rent ({names})',
                    event=f"{movie.title} available to rent",
                    url=url,
                    positive=True,
                ))

            # Buy
            buy = providers.get("buy") or []
            if buy:
                names = ", ".join(_provider_names(buy))
                observations.append(Observation(
                    source=self.source_id,
                    item_key=movie.key,
                    item_label=str(movie),
                    summary=f'"{movie.title}" available to buy ({names})',
                    event=f"{movie.title} available to buy",
                    url=url,
                    positive=True,
                ))

            # Digital release date
            digital_date = _get_digital_release(sess, api_key, tmdb_id)
            if digital_date:
                try:
                    dt = date.fromisoformat(digital_date)
                except ValueError:
                    dt = None
                if dt and dt > date.today():
                    observations.append(Observation(
                        source=self.source_id,
                        item_key=movie.key,
                        item_label=str(movie),
                        summary=f'"{movie.title}" digital release: {digital_date}',
                        event=f"{movie.title} digital {digital_date}",
                        url=url,
                        positive=True,
                    ))

        _save_cache(cache)
        return observations

    def probe(self, config: Config, query: str | None = None) -> str:
        api_key = env("TMDB_API_KEY")
        if not api_key:
            return "TMDB_API_KEY not set"
        sess = http.session()
        cache = _load_cache()
        lines: list[str] = []
        services = [s.lower() for s in (config.streaming.services if config.streaming else [])]
        lines.append(f"subscribed services: {services}")

        for movie in config.movies:
            tmdb_id = _resolve_id(sess, api_key, movie, cache)
            lines.append(f"\n--- {movie} ---")
            if not tmdb_id:
                lines.append("  TMDB search: no results")
                continue
            lines.append(f"  tmdb_id: {tmdb_id}")
            providers = _get_providers(sess, api_key, tmdb_id)
            for cat in ("flatrate", "rent", "buy", "ads", "free"):
                entries = providers.get(cat) or []
                if entries:
                    lines.append(f"  {cat}: {_provider_names(entries)}")
            digital = _get_digital_release(sess, api_key, tmdb_id)
            if digital:
                lines.append(f"  digital release: {digital}")

        _save_cache(cache)
        return "\n".join(lines)


def _resolve_id(sess: http.requests.Session, api_key: str,
                movie: WatchMovie, cache: dict[str, Any]) -> int | None:
    key = movie.key
    entry = cache.get(key)
    if entry and _cache_fresh(entry) and entry.get("tmdb_id"):
        return int(entry["tmdb_id"])
    tmdb_id = _search_movie(sess, api_key, movie)
    cache[key] = {"tmdb_id": tmdb_id, "searched": date.today().isoformat()}
    return tmdb_id
