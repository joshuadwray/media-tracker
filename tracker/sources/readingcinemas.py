"""Reading Cinemas platform watcher — Angelika Dallas and siblings.

The same backend serves Angelika, Reading Cinemas and Consolidated
Theatres, so the cinema ids live in config rather than in this file.

This was parked for two months on a wrong diagnosis: the site is a
client-rendered React app whose API wants a bearer token, and the note
said that token sat behind a reCAPTCHA. It doesn't. reCAPTCHA is in the
bundle, but only on login, signup and payment — the catalog path never
touches it. The token is simply handed out, unauthenticated, by the
settings endpoint the app calls first:

    GET /settings/{country_id}   -> data.settings.token   (no auth)
    GET /films?countryId=..&cinemaId=..&status=nowShowing
        Authorization: Bearer <that token>

It's a Cognito access token good for an hour, which is why it's fetched
per run and never cached.

Config:
  angelika:
    kind: readingcinemas
    cinema_id: "0000000009"    # quote it — the leading zeros are real
    country_id: 6              # US
    label: Angelika Dallas

Dated films only. ``status=nowShowing`` returns every film that has a
schedule, future runs included (its name is a misnomer: Dune: Part Three
sits in it dated December), and each entry carries real ``showdates``.
``status=comingSoon`` is a separate, *undated* announcement list — 78
titles with no showdates at all — and is deliberately not used, on the
same rule as Landmark's undated roster: a sighting should mean you can
actually go. Wire it up only if announcement-level warning is ever
wanted, and check it for stale entries first.

Two traps, both hit live while working this out:
  * A bad parameter combination returns **HTTP 200** with a raw Lambda
    error body (``errorType``/``errorMessage``/``trace``) instead of a
    film list. Unchecked, that reads as "nothing playing" forever.
  * ``status`` and ``flag`` swap roles between call sites — now-showing
    passes the cinema in ``cinemaId``, coming-soon passes it in ``flag``.

Politeness: the *website* disallows /<city>/sessions/ in robots.txt.
This source never touches those pages — it calls the API host, twice a
run, behind the shared throttle in tracker/http.py. Keep it that way.
"""
from __future__ import annotations

from typing import Any

from .. import http
from ..config import Config
from ..matching import titles_match
from ..models import Observation
from .base import Source, register

API_BASE = "https://production-api.readingcinemas.com"


@register
class ReadingCinemasSource(Source):
    kind = "readingcinemas"

    @property
    def api_base(self) -> str:
        return (self.cfg.get("api_base") or API_BASE).rstrip("/")

    @property
    def cinema_id(self) -> str:
        cid = self.cfg.get("cinema_id")
        if not cid:
            raise ValueError(f"source '{self.source_id}': cinema_id is required")
        return str(cid)

    @property
    def country_id(self) -> str:
        return str(self.cfg.get("country_id") or 6)

    def check(self, config: Config) -> list[Observation]:
        if not config.movies:
            return []
        sess = http.session()
        films = self._films(sess)
        venue = self.label

        observations: list[Observation] = []
        for movie in config.movies:
            for title, dates in films.items():
                if not titles_match(movie.title, title):
                    continue
                when = f" ({', '.join(dates[:4])})" if dates else ""
                observations.append(Observation(
                    source=self.source_id,
                    item_key=movie.key,
                    item_label=str(movie),
                    summary=f'"{title}" playing at {venue}{when}',
                    url="https://angelikafilmcenter.com/dallas/now-playing",
                    positive=True,
                    # Dates stay out of the dedup key: `venue` already
                    # collapses a run into one push per theatre, so an
                    # extended schedule doesn't re-notify.
                    event=f'"{title}" playing at {venue}',
                    venue=venue,
                    source_label=self.label,
                    venue_tier=self.tier,
                    distance_mi=self.distance_mi,
                    detail={"theatre": venue, "cinema_id": self.cinema_id,
                            "dates": dates},
                ))
        return observations

    # ---- data plumbing -------------------------------------------------

    def _token(self, sess: Any) -> str:
        """The bearer token, handed out unauthenticated by /settings."""
        resp = http.get(sess, f"{self.api_base}/settings/{self.country_id}",
                        headers={"Accept": "application/json"})
        if resp.status_code != 200:
            raise RuntimeError(f"settings returned HTTP {resp.status_code}")
        token = ((_body(resp).get("settings") or {}).get("token"))
        if not token:
            raise RuntimeError("settings carried no token — the bootstrap "
                               "endpoint changed shape")
        return str(token)

    def _films(self, sess: Any) -> dict[str, list[str]]:
        token = self._token(sess)
        resp = http.get(
            sess, f"{self.api_base}/films",
            params={"countryId": self.country_id, "cinemaId": self.cinema_id,
                    "status": "nowShowing"},
            headers={"Accept": "application/json",
                     "Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"films returned HTTP {resp.status_code}")
        return _parse_films(resp.json(), self.cinema_id)

    # ---- probe ---------------------------------------------------------

    def probe(self, config: Config, query: str | None = None) -> str:
        sess = http.session()
        lines = [f"api {self.api_base}, cinema {self.cinema_id}, "
                 f"country {self.country_id}"]
        try:
            token = self._token(sess)
        except RuntimeError as exc:
            return "\n".join(lines + [f"FAILED getting token: {exc}"])
        lines.append(f"token acquired unauthenticated ({len(token)} chars)")
        try:
            films = self._films(sess)
        except RuntimeError as exc:
            return "\n".join(lines + [f"FAILED: {exc}"])
        lines.append(f"{len(films)} dated films")
        if not films:
            lines.append("NO FILMS — check cinema_id; a wrong one returns an "
                         "empty list rather than an error.")
        for title, dates in sorted(films.items(), key=lambda kv: kv[1][:1]):
            span = f"{dates[0]}..{dates[-1]}" if len(dates) > 1 else (
                dates[0] if dates else "?")
            lines.append(f"  - {span:24s} {title}")
        return "\n".join(lines)


def _body(resp: Any) -> dict:
    """Decoded payload, with the API's 200-plus-error-body case raised.

    A bad parameter combination answers 200 carrying a raw Lambda error
    instead of data. Left unchecked that parses to nothing and the source
    reports a confident, permanent zero.
    """
    try:
        payload = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"response was not JSON: {exc}") from exc
    if isinstance(payload, dict) and payload.get("errorType"):
        raise RuntimeError(
            f"API error {payload.get('errorType')}: "
            f"{str(payload.get('errorMessage'))[:200]}")
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload if isinstance(payload, dict) else {"data": payload}


def _parse_films(payload: Any, cinema_id: str) -> dict[str, list[str]]:
    """title -> sorted showdates, for films scheduled at this cinema."""
    if isinstance(payload, dict) and payload.get("errorType"):
        raise RuntimeError(
            f"API error {payload.get('errorType')}: "
            f"{str(payload.get('errorMessage'))[:200]}")

    nodes = payload if isinstance(payload, list) else []
    if isinstance(payload, dict):
        for candidate in (payload.get("data"),
                          *(v for v in payload.values() if isinstance(v, list))):
            if isinstance(candidate, list):
                nodes = candidate
                break

    films: dict[str, list[str]] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        title = (node.get("name") or node.get("movieName") or "").strip()
        if not title:
            continue
        # The response is already scoped to one cinema, but it echoes the
        # id back per film; a mismatch means the scoping silently failed.
        theater = str(node.get("theater") or "").strip()
        if theater and theater != str(cinema_id):
            continue
        dates = sorted({
            str(sd.get("date"))[:10]
            for sd in (node.get("showdates") or [])
            if isinstance(sd, dict) and sd.get("date")
        })
        if dates:
            films[title] = dates
    return films
