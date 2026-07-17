"""Alamo Drafthouse showtime watcher.

Drafthouse publishes a per-market JSON schedule feed used by their own
site (no auth): https://drafthouse.com/s/mother/v2/schedule/market/<market>
For DFW the market slug is "dfw". One request covers every Drafthouse
location in the metro, which makes this the cheapest showtime source we
have — always check it before falling back to page-watching.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from .. import http
from ..config import Config
from ..matching import titles_match
from ..models import Observation
from .base import Source, register


@register
class DrafthouseSource(Source):
    kind = "drafthouse"

    @property
    def market(self) -> str:
        return self.cfg.get("market", "dfw")

    @property
    def feed_url(self) -> str:
        return f"https://drafthouse.com/s/mother/v2/schedule/market/{self.market}"

    def check(self, config: Config) -> list[Observation]:
        if not config.movies:
            return []
        sess = http.session()
        resp = http.get(sess, self.feed_url, headers={"Accept": "application/json"})
        if resp.status_code != 200:
            raise RuntimeError(f"schedule feed returned HTTP {resp.status_code}")
        data = resp.json()

        films, sessions_by_film, cinemas = _parse_feed(data)
        observations: list[Observation] = []
        for movie in config.movies:
            for slug, title in films.items():
                if not titles_match(movie.title, title):
                    continue
                # One observation per (film, cinema, date) so each new
                # day of showtimes notifies exactly once.
                by_cinema_date: dict[tuple[str, str], int] = defaultdict(int)
                for s in sessions_by_film.get(slug, []):
                    by_cinema_date[(s["cinema"], s["date"])] += 1
                if not by_cinema_date:
                    observations.append(Observation(
                        source=self.source_id,
                        item_key=movie.key,
                        item_label=str(movie),
                        summary=f'"{title}" listed at Alamo {self.market.upper()} '
                                "(no sessions on sale yet)",
                        url=f"https://drafthouse.com/{self.market}",
                        positive=True,
                    ))
                for (cinema_id, date), count in sorted(by_cinema_date.items()):
                    cinema = cinemas.get(cinema_id, cinema_id)
                    observations.append(Observation(
                        source=self.source_id,
                        item_key=movie.key,
                        item_label=str(movie),
                        summary=f'"{title}" at Alamo {cinema} on {date} '
                                f"({count} showtime{'s' if count != 1 else ''})",
                        url=f"https://drafthouse.com/{self.market}/show/{slug}",
                        positive=True,
                        detail={"cinema": cinema, "date": date, "sessions": count},
                    ))
        return observations

    def probe(self, config: Config, query: str | None = None) -> str:
        sess = http.session()
        resp = http.get(sess, self.feed_url, headers={"Accept": "application/json"})
        out = f"GET {self.feed_url}\nHTTP {resp.status_code}, {len(resp.content)} bytes\n"
        if resp.status_code != 200:
            return out
        try:
            data = resp.json()
        except ValueError:
            return out + "not JSON:\n" + resp.text[:1000]
        films, sessions_by_film, cinemas = _parse_feed(data)
        sample = {slug: films[slug] for slug in list(films)[:15]}
        return out + (
            f"parsed {len(films)} films, "
            f"{sum(len(v) for v in sessions_by_film.values())} sessions, "
            f"{len(cinemas)} cinemas\n"
            f"cinemas: {json.dumps(cinemas, indent=2)[:800]}\n"
            f"sample films: {json.dumps(sample, indent=2)[:2000]}"
        )


def _parse_feed(data: Any) -> tuple[dict[str, str], dict[str, list[dict]], dict[str, str]]:
    """Return (film slug -> title, slug -> sessions, cinema id -> name).

    The feed nests under a top-level "data" key; presentations carry the
    film metadata and sessions reference them by slug. Parsed liberally
    so minor schema drift doesn't break the run.
    """
    root = data.get("data", data) if isinstance(data, dict) else {}

    films: dict[str, str] = {}
    for p in _as_list(root.get("presentations")):
        slug = p.get("slug") or p.get("presentationSlug")
        show = p.get("show") or {}
        title = show.get("title") or p.get("title")
        if slug and title:
            films[slug] = title

    cinemas: dict[str, str] = {}
    for c in _as_list(root.get("cinemas")):
        cid = str(c.get("id") or c.get("cinemaId") or "")
        name = c.get("name") or c.get("title")
        if cid and name:
            cinemas[cid] = name

    sessions_by_film: dict[str, list[dict]] = defaultdict(list)
    for s in _as_list(root.get("sessions")):
        slug = s.get("presentationSlug") or s.get("slug")
        when = (
            s.get("showTimeClt") or s.get("showTime") or s.get("showTimeUtc") or ""
        )
        if slug:
            sessions_by_film[slug].append({
                "cinema": str(s.get("cinemaId") or s.get("theaterId") or "?"),
                "date": str(when)[:10] or "?",
                "time": str(when),
            })
    return films, dict(sessions_by_film), cinemas


def _as_list(v: Any) -> list[dict]:
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []
