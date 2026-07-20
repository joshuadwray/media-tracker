"""Cinemark and AMC theater watchers.

Neither chain has a public API we can key into (AMC's official API needs
vendor approval), but both render their theater showtime pages with
schema.org ld+json blocks (Movie / ScreeningEvent) that we can parse for
exact titles and dates. When that structured data is missing or the
shape drifts, we fall back to scanning the page text for watched titles
— strictly worse (no dates) but never silently broken.

Both chains sit behind aggressive bot protection; if `probe` shows 403s
from GitHub Actions, run these sources from a home machine instead.

Config per source:
  theatres:
    - name: Cinemark Denton 14
      url: https://www.cinemark.com/theatres/tx-denton/cinemark-14-denton
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta
from html import unescape
from typing import Any, Iterator

from .. import http
from ..config import Config
from ..matching import text_contains_title, titles_match
from ..models import Observation
from .base import Source, register
from .generic_page import _html_to_text

_LDJSON_RE = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.DOTALL
)

# Cinemark stopped shipping Movie/ScreeningEvent ld+json (observed 2026-07)
# but embeds per-movie JSON in data-json-model attributes: movieTitle plus a
# showTimes list of {showTime: "2026-07-17T22:30:00", ...} for the selected
# date. Entries without showtimes are site-wide carousels, not this theatre.
_JSON_MODEL_RE = re.compile(r'data-json-model="([^"]+)"')

# Cinemark "Advance Tickets" cards: on-sale-now special events with no
# showtimes on the selected date. Verified theatre-specific (different
# theatres list different events). Title rides in an sr-only span.
_ADVANCE_RE = re.compile(
    r'card__movie--advanced-tickets.{0,2000}?sr-only">([^<]+)<', re.DOTALL
)

# AMC's showtimes page is Next.js App Router: the data rides in RSC "flight"
# chunks (self.__next_f.push). Inside the decoded blob, a movie-filter
# dropdown maps slug -> title, and each showtime object carries
# showDateTimeUtc followed by an aria-describedby whose first token is the
# movie slug. Only titles with actual showtimes count — the dropdown lists
# every AMC movie nationwide.
_NEXT_FLIGHT_RE = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
_FLIGHT_OPTION_RE = re.compile(r'\{"value":"([a-z0-9-]+)","children":"([^"]+)"\}')
_FLIGHT_SHOWTIME_RE = re.compile(
    r'"showDateTimeUtc":"([^"]+)".{0,600}?"aria-describedby":"([a-z0-9-]+)\s',
    re.DOTALL,
)


def _lookahead_dates() -> list[str]:
    """Return YYYY-MM-DD strings from today through today+N (inclusive).

    N is read from the THEATER_LOOKAHEAD env var (default 3).
    """
    n = int(os.environ.get("THEATER_LOOKAHEAD", "3"))
    today = date.today()
    return [(today + timedelta(days=d)).isoformat() for d in range(n + 1)]


class ChainTheaterSource(Source):
    chain_label = "theater"

    def theatres(self) -> list[dict]:
        theatres = self.cfg.get("theatres") or []
        if not theatres:
            raise ValueError(
                f"source '{self.source_id}': configure 'theatres' with name + url"
            )
        for t in theatres:
            if not t.get("name") or not t.get("url"):
                raise ValueError(f"each theatre needs name + url, got {t!r}")
        return theatres

    @staticmethod
    def _url_for_date(base: str, dt: str) -> str:
        """Override in subclasses to append date parameter."""
        return base

    def check(self, config: Config) -> list[Observation]:
        if not config.movies:
            return []
        sess = http.session()
        observations: list[Observation] = []
        errors: list[str] = []
        dates_to_scan = _lookahead_dates()
        for theatre in self.theatres():
            # Merge movies across all dates for this theatre.
            merged: dict[str, dict] = {}  # title -> {dates, advance}
            page_text = None
            theatre_ok = False
            for i, dt in enumerate(dates_to_scan):
                url = self._url_for_date(theatre["url"], dt)
                try:
                    resp = http.get(sess, url)
                    if resp.status_code != 200:
                        if i == 0:
                            errors.append(f"{theatre['name']}: HTTP {resp.status_code}")
                        continue
                    theatre_ok = True
                except http.requests.RequestException as exc:
                    if i == 0:
                        errors.append(
                            f"{theatre['name']}: {type(exc).__name__}: {exc}"
                        )
                    continue
                for entry in _extract_movies(resp.text):
                    title = entry["title"]
                    if title not in merged:
                        merged[title] = {"dates": set(), "advance": entry.get("advance", False)}
                    merged[title]["dates"].update(entry.get("dates") or set())
                # Page-text fallback only on the first (today) date.
                if i == 0 and not merged:
                    page_text = _html_to_text(resp.text)
            if not theatre_ok:
                continue
            for movie in config.movies:
                matched = False
                for title, info in merged.items():
                    if not titles_match(movie.title, title):
                        continue
                    matched = True
                    dates = info["dates"]
                    when = f" ({', '.join(sorted(dates)[:4])})" if dates else ""
                    verb = ("advance tickets on sale at"
                            if info.get("advance") else "playing at")
                    observations.append(Observation(
                        source=self.source_id,
                        item_key=movie.key,
                        item_label=str(movie),
                        summary=f'"{title}" {verb} '
                                f'{theatre["name"]}{when}',
                        url=theatre["url"],
                        positive=True,
                        event=f'"{title}" {verb} {theatre["name"]}',
                        detail={"theatre": theatre["name"],
                                "dates": sorted(dates or [])},
                    ))
                if not matched and page_text and text_contains_title(page_text, movie.title):
                    observations.append(Observation(
                        source=self.source_id,
                        item_key=movie.key,
                        item_label=str(movie),
                        summary=f'"{movie.title}" mentioned on '
                                f'{theatre["name"]} page',
                        url=theatre["url"],
                        positive=True,
                        event=f'"{movie.title}" mentioned on {theatre["name"]}',
                        detail={"theatre": theatre["name"], "matched": "page-text"},
                    ))
        if errors and len(errors) == len(self.theatres()):
            raise RuntimeError("; ".join(errors))
        return observations

    def probe(self, config: Config, query: str | None = None) -> str:
        sess = http.session()
        lines = []
        dates_to_scan = _lookahead_dates()
        lines.append(f"Scanning {len(dates_to_scan)} dates: "
                      f"{dates_to_scan[0]} .. {dates_to_scan[-1]}")
        for theatre in self.theatres():
            merged: dict[str, dict] = {}
            status_line = None
            for i, dt in enumerate(dates_to_scan):
                url = self._url_for_date(theatre["url"], dt)
                try:
                    resp = http.get(sess, url)
                except http.requests.RequestException as exc:
                    if i == 0:
                        lines.append(
                            f"{theatre['name']}: FAILED {type(exc).__name__}: {exc}"
                        )
                    continue
                if i == 0:
                    status_line = f"HTTP {resp.status_code}, {len(resp.text)} bytes"
                for entry in _extract_movies(resp.text):
                    title = entry["title"]
                    if title not in merged:
                        merged[title] = {"dates": set(), "advance": entry.get("advance", False)}
                    merged[title]["dates"].update(entry.get("dates") or set())
                if i == 0 and not merged and resp.status_code == 200:
                    text = _html_to_text(resp.text)
                    lines.append(f"{theatre['name']}: {status_line}, "
                                 f"no structured data; page text {len(text)} chars, "
                                 f"excerpt: {text[:200]!r}")
            if merged:
                lines.append(
                    f"{theatre['name']}: {status_line or 'OK'}, "
                    f"{len(merged)} movies across {len(dates_to_scan)} dates"
                )
                for title, info in sorted(merged.items()):
                    dates = sorted(info["dates"])
                    adv = " [advance]" if info.get("advance") else ""
                    lines.append(f"  - {title}{adv}: {', '.join(dates[:6])}"
                                 f"{'...' if len(dates) > 6 else ''}")
        return "\n".join(lines)


@register
class CinemarkSource(ChainTheaterSource):
    kind = "cinemark"
    chain_label = "Cinemark"

    @staticmethod
    def _url_for_date(base: str, dt: str) -> str:
        return f"{base}?showDate={dt}"


@register
class AMCSource(ChainTheaterSource):
    kind = "amc"
    chain_label = "AMC"

    @staticmethod
    def _url_for_date(base: str, dt: str) -> str:
        return f"{base}?date={dt}"


def _extract_movies(html: str) -> Iterator[dict[str, Any]]:
    """Yield {title, dates} from ld+json or Cinemark data-json-model blocks."""
    found: dict[str, set[str]] = {}
    for m in _LDJSON_RE.finditer(html):
        try:
            data = json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue
        _walk_schema(data, found)
    for m in _JSON_MODEL_RE.finditer(html):
        try:
            data = json.loads(unescape(m.group(1)))
        except json.JSONDecodeError:
            continue
        title = data.get("movieTitle")
        shows = data.get("showTimes") or []
        if not title or not isinstance(shows, list) or not shows:
            continue
        dates = found.setdefault(str(title), set())
        for show in shows:
            start = show.get("showTime") if isinstance(show, dict) else None
            if isinstance(start, str) and start:
                dates.add(start[:10])
    _flight_movies(html, found)
    advance: set[str] = set()
    for m in _ADVANCE_RE.finditer(html):
        title = unescape(m.group(1)).strip()
        if title and title not in found:
            advance.add(title)
            found.setdefault(title, set())
    for title, dates in found.items():
        yield {"title": title, "dates": dates, "advance": title in advance}


def _flight_movies(html: str, found: dict[str, set[str]]) -> None:
    """Extract {title: dates} from Next.js RSC flight data (AMC)."""
    chunks = _NEXT_FLIGHT_RE.findall(html)
    if not chunks:
        return
    try:
        blob = "".join(json.loads(c) for c in chunks)
    except json.JSONDecodeError:
        return
    titles = dict(_FLIGHT_OPTION_RE.findall(blob))
    if not titles:
        return
    for m in _FLIGHT_SHOWTIME_RE.finditer(blob):
        ts, slug = m.groups()
        title = titles.get(slug)
        if not title:
            continue
        found.setdefault(title, set()).add(_local_show_date(ts))


def _local_show_date(ts_utc: str) -> str:
    """UTC timestamp -> local calendar date; theatres here are all Central."""
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(ts_utc.replace("Z", "+00:00"))
        return dt.astimezone(ZoneInfo("America/Chicago")).date().isoformat()
    except Exception:
        return ts_utc[:10]


def _walk_schema(node: Any, found: dict[str, set[str]]) -> None:
    if isinstance(node, dict):
        typ = node.get("@type") or ""
        types = {typ} if isinstance(typ, str) else set(typ)
        if types & {"Movie", "ScreeningEvent"}:
            name = node.get("name")
            work = node.get("workPresented")
            if isinstance(work, dict) and work.get("name"):
                name = work["name"]
            if name:
                dates = found.setdefault(str(name), set())
                start = node.get("startDate")
                if isinstance(start, str) and start:
                    dates.add(start[:10])
        for v in node.values():
            _walk_schema(v, found)
    elif isinstance(node, list):
        for v in node:
            _walk_schema(v, found)
