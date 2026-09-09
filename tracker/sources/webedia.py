"""Webedia (Gatsby) theater watcher — Landmark Inwood and friends.

Landmark's site never puts a film title in its server-rendered HTML, so
the page watcher sees nothing there. But it's a Gatsby build, and Gatsby
publishes every static query result as plain JSON with no auth at
``/page-data/sq/d/<hash>.json``. One of those blobs is the entire
circuit's movie list, and each film carries the theatre codes it's booked
at plus the date its showtimes start:

    allMovie.nodes[].title
    allMovie.nodes[].theaters[] -> {"th": "X02KC", "firstShowtimeDate": ...}

A second blob maps theatre code -> name (``allTheater``), which is how a
wrong ``theater_id`` gets caught instead of silently matching nothing.

Discovery: ``/page-data/index/page-data.json`` lists the site's
``staticQueryHashes``; we scan them for the blob holding ``allMovie``.
The hash is derived from the GraphQL query *text*, not the build, so it
survives ordinary rebuilds — ``movie_query_hash`` in config
short-circuits the scan to a single request, and the scan is what heals
it when the query does change.

Config:
  inwood:
    kind: webedia
    site: https://www.landmarktheatres.com
    theater_id: X02KC
    label: Landmark Inwood

Only films with a ``firstShowtimeDate`` count as sightings. A theatre's
roster also carries films with that field null — announced-but-unscheduled
and long-past bookings sit together there (Asteroid City and Jurassic
World Rebirth are both on Inwood's list), so treating the roster as
"playing" would fire on films that left months ago.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from .. import http
from ..config import Config
from ..matching import titles_match
from ..models import Observation
from .base import Source, register

# Hashes seen in the wild, tried before scanning. Advisory only — a miss
# just costs the scan it was meant to skip.
_KNOWN_MOVIE_HASH = "3836549025"
_KNOWN_THEATER_HASH = "2506275789"


@register
class WebediaSource(Source):
    kind = "webedia"

    @property
    def site(self) -> str:
        return (self.cfg.get("site") or "https://www.landmarktheatres.com").rstrip("/")

    @property
    def theater_id(self) -> str:
        tid = self.cfg.get("theater_id")
        if not tid:
            raise ValueError(f"source '{self.source_id}': theater_id is required")
        return str(tid).upper()

    def check(self, config: Config) -> list[Observation]:
        if not config.movies:
            return []
        sess = http.session()
        films = self._films_at_theatre(sess)
        venue = self._venue_name(sess)
        today = date.today().isoformat()

        observations: list[Observation] = []
        for movie in config.movies:
            for title, first_date in films.items():
                if not titles_match(movie.title, title):
                    continue
                upcoming = first_date > today
                verb = "booked at" if upcoming else "playing at"
                when = f" from {first_date}" if upcoming else f" (since {first_date})"
                observations.append(Observation(
                    source=self.source_id,
                    item_key=movie.key,
                    item_label=str(movie),
                    summary=f'"{title}" {verb} {venue}{when}',
                    url=f"{self.site}/our-locations/",
                    positive=True,
                    # One observation per (film, venue) — the venue field
                    # already collapses a whole run into a single push, so
                    # the date stays out of the dedup key and a schedule
                    # extension doesn't re-notify.
                    event=f'"{title}" {verb} {venue}',
                    venue=venue,
                    source_label=self.label,
                    detail={"theatre": venue, "theater_id": self.theater_id,
                            "first_showtime": first_date},
                ))
        return observations

    # ---- data plumbing -------------------------------------------------

    def _static_query(self, sess: Any, want_key: str, known_hash: str) -> dict:
        """Return the static-query payload containing `want_key`.

        Tries the known hash first, then scans the site's advertised
        hashes. Raises if nothing carries the key — a silent empty parse
        is the one outcome worth failing loudly over.
        """
        hinted = self.cfg.get(f"{want_key.replace('all', '').lower()}_query_hash")
        for h in [hinted, known_hash]:
            if h:
                payload = self._try_hash(sess, str(h), want_key)
                if payload is not None:
                    return payload

        index = http.get(sess, f"{self.site}/page-data/index/page-data.json",
                         headers={"Accept": "application/json"})
        if index.status_code != 200:
            raise RuntimeError(
                f"page-data index returned HTTP {index.status_code}")
        for h in index.json().get("staticQueryHashes") or []:
            payload = self._try_hash(sess, str(h), want_key)
            if payload is not None:
                return payload
        raise RuntimeError(f"no static query on {self.site} carries '{want_key}'")

    def _try_hash(self, sess: Any, h: str, want_key: str) -> Optional[dict]:
        resp = http.get(sess, f"{self.site}/page-data/sq/d/{h}.json",
                        headers={"Accept": "application/json"}, retries=0)
        if resp.status_code != 200:
            return None
        try:
            data = (resp.json() or {}).get("data") or {}
        except ValueError:
            return None
        node = data.get(want_key)
        return node if isinstance(node, dict) and "nodes" in node else None

    def _films_at_theatre(self, sess: Any) -> dict[str, str]:
        """title -> earliest firstShowtimeDate at this theatre."""
        movies = self._static_query(sess, "allMovie", _KNOWN_MOVIE_HASH)
        return _films_for(movies.get("nodes") or [], self.theater_id)

    def _venue_name(self, sess: Any) -> str:
        try:
            theaters = self._static_query(sess, "allTheater", _KNOWN_THEATER_HASH)
        except RuntimeError:
            return self.label
        for node in theaters.get("nodes") or []:
            if str(node.get("id") or "").upper() == self.theater_id:
                return node.get("name") or self.label
        return self.label

    # ---- probe ---------------------------------------------------------

    def probe(self, config: Config, query: str | None = None) -> str:
        sess = http.session()
        lines = [f"site {self.site}, theatre {self.theater_id}"]
        try:
            movies = self._static_query(sess, "allMovie", _KNOWN_MOVIE_HASH)
        except RuntimeError as exc:
            return "\n".join(lines + [f"FAILED: {exc}"])
        nodes = movies.get("nodes") or []
        films = _films_for(nodes, self.theater_id)
        booked = _booked_count(nodes, self.theater_id)
        lines.append(f"{len(nodes)} films circuit-wide, "
                     f"{booked} on this theatre's roster, "
                     f"{len(films)} with showtimes")
        lines.append(f"venue name: {self._venue_name(sess)}")
        if not films:
            lines.append("NO DATED FILMS — check theater_id against the roster "
                         "count above: 0 there means the code is wrong.")
        for title, first in sorted(films.items(), key=lambda kv: kv[1]):
            lines.append(f"  - {first}  {title}")
        return "\n".join(lines)


def _films_for(nodes: list[dict], theater_id: str) -> dict[str, str]:
    """title -> earliest firstShowtimeDate for films dated at this theatre."""
    films: dict[str, str] = {}
    for node in nodes:
        title = node.get("title")
        if not title:
            continue
        for th in node.get("theaters") or []:
            if str(th.get("th") or "").upper() != theater_id:
                continue
            first = th.get("firstShowtimeDate")
            if not first:
                continue
            first = str(first)[:10]
            if title not in films or first < films[title]:
                films[title] = first
    return films


def _booked_count(nodes: list[dict], theater_id: str) -> int:
    """Films on the theatre's roster, dated or not — the probe's sanity check."""
    return sum(
        any(str(th.get("th") or "").upper() == theater_id
            for th in (node.get("theaters") or []))
        for node in nodes
    )
