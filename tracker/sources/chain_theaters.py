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
import re
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

    def check(self, config: Config) -> list[Observation]:
        if not config.movies:
            return []
        sess = http.session()
        observations: list[Observation] = []
        errors: list[str] = []
        for theatre in self.theatres():
            try:
                resp = http.get(sess, theatre["url"])
                if resp.status_code != 200:
                    errors.append(f"{theatre['name']}: HTTP {resp.status_code}")
                    continue
                html = resp.text
            except http.requests.RequestException as exc:
                errors.append(f"{theatre['name']}: {type(exc).__name__}: {exc}")
                continue

            listings = list(_extract_movies(html))
            page_text = None if listings else _html_to_text(html)
            for movie in config.movies:
                if listings:
                    for found in listings:
                        if not titles_match(movie.title, found["title"]):
                            continue
                        dates = found.get("dates")
                        when = f" ({', '.join(sorted(dates)[:4])})" if dates else ""
                        observations.append(Observation(
                            source=self.source_id,
                            item_key=movie.key,
                            item_label=str(movie),
                            summary=f'"{found["title"]}" playing at '
                                    f'{theatre["name"]}{when}',
                            url=theatre["url"],
                            positive=True,
                            detail={"theatre": theatre["name"],
                                    "dates": sorted(dates or [])},
                        ))
                elif text_contains_title(page_text, movie.title):
                    observations.append(Observation(
                        source=self.source_id,
                        item_key=movie.key,
                        item_label=str(movie),
                        summary=f'"{movie.title}" mentioned on '
                                f'{theatre["name"]} page',
                        url=theatre["url"],
                        positive=True,
                        detail={"theatre": theatre["name"], "matched": "page-text"},
                    ))
        if errors and len(errors) == len(self.theatres()):
            raise RuntimeError("; ".join(errors))
        return observations

    def probe(self, config: Config, query: str | None = None) -> str:
        sess = http.session()
        lines = []
        for theatre in self.theatres():
            try:
                resp = http.get(sess, theatre["url"])
            except http.requests.RequestException as exc:
                lines.append(f"{theatre['name']}: FAILED {type(exc).__name__}: {exc}")
                continue
            listings = list(_extract_movies(resp.text))
            lines.append(
                f"{theatre['name']}: HTTP {resp.status_code}, "
                f"{len(resp.text)} bytes, {len(listings)} ld+json movie entries"
            )
            for entry in listings[:10]:
                lines.append(f"  - {json.dumps(entry, default=list)[:200]}")
            if not listings and resp.status_code == 200:
                text = _html_to_text(resp.text)
                lines.append(f"  no structured data; page text {len(text)} chars, "
                             f"excerpt: {text[:200]!r}")
        return "\n".join(lines)


@register
class CinemarkSource(ChainTheaterSource):
    kind = "cinemark"
    chain_label = "Cinemark"


@register
class AMCSource(ChainTheaterSource):
    kind = "amc"
    chain_label = "AMC"


def _extract_movies(html: str) -> Iterator[dict[str, Any]]:
    """Yield {title, dates} from schema.org Movie / ScreeningEvent ld+json."""
    found: dict[str, set[str]] = {}
    for m in _LDJSON_RE.finditer(html):
        try:
            data = json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue
        _walk_schema(data, found)
    for title, dates in found.items():
        yield {"title": title, "dates": dates}


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
