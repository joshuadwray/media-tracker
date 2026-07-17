"""Generic theater-page watcher.

Most single-screen indie theaters (Texas Theatre, The Modern, Grand
Berry, ...) have no API worth reverse-engineering — but their "now
playing / coming soon" page is one URL, and all we need to know is
"does a watched title now appear on it?". This source fetches each
configured page, strips it to text, and looks for watched movie titles
as normalized phrases. When a title appears, that's the signal to go
look at the page yourself and buy the ticket.

Config:
  pages:
    - name: Texas Theatre
      url: https://thetexastheatre.com/
    - name: The Modern (Magnolia at the Modern)
      url: https://www.themodern.org/films
"""
from __future__ import annotations

from html.parser import HTMLParser

from .. import http
from ..config import Config
from ..matching import text_contains_title
from ..models import Observation
from .base import Source, register


@register
class GenericPageSource(Source):
    kind = "pages"

    def pages(self) -> list[dict]:
        pages = self.cfg.get("pages") or []
        if not pages:
            raise ValueError(f"source '{self.source_id}': configure at least one page")
        for p in pages:
            if not p.get("name") or not p.get("url"):
                raise ValueError(f"each page needs name + url, got {p!r}")
        return pages

    def check(self, config: Config) -> list[Observation]:
        if not config.movies:
            return []
        sess = http.session()
        observations: list[Observation] = []
        errors: list[str] = []
        for page in self.pages():
            try:
                resp = http.get(sess, page["url"])
                if resp.status_code != 200:
                    errors.append(f"{page['name']}: HTTP {resp.status_code}")
                    continue
                text = _html_to_text(resp.text)
            except http.requests.RequestException as exc:
                errors.append(f"{page['name']}: {type(exc).__name__}: {exc}")
                continue
            for movie in config.movies:
                if text_contains_title(text, movie.title):
                    observations.append(Observation(
                        source=self.source_id,
                        item_key=movie.key,
                        item_label=str(movie),
                        summary=f'"{movie.title}" mentioned on {page["name"]}',
                        url=page["url"],
                        positive=True,
                        detail={"page": page["name"]},
                    ))
        if errors and not observations:
            # Surface total failure; partial failures ride along in detail.
            if len(errors) == len(self.pages()):
                raise RuntimeError("; ".join(errors))
        return observations

    def probe(self, config: Config, query: str | None = None) -> str:
        sess = http.session()
        lines = []
        for page in self.pages():
            try:
                resp = http.get(sess, page["url"])
                text = _html_to_text(resp.text)
                lines.append(
                    f"{page['name']}: HTTP {resp.status_code}, "
                    f"{len(text)} chars of text\n  excerpt: {text[:300]!r}"
                )
            except http.requests.RequestException as exc:
                lines.append(f"{page['name']}: FAILED {type(exc).__name__}: {exc}")
        return "\n".join(lines)


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript"}

    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data.strip():
            self.chunks.append(data.strip())


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return " ".join(parser.chunks)
