"""BiblioCommons catalog watcher (Denton Public Library and ~200 others).

BiblioCommons search pages are a React app that server-renders its data
into <script type="application/json"> tags. We fetch the search page per
watched book and dig the bib records (title, author, format, availability)
out of that embedded JSON. If the embed shape shifts, `probe` dumps what
we actually received so the extractor is easy to re-point.

Note: bibliocommons.com sits behind bot protection that 403s some
datacenter IPs. Works from residential connections; from CI you may need
to route through a proxy or run this source only from a home machine.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterator

from .. import http
from ..config import Config
from ..matching import author_matches, titles_match
from ..models import Observation
from .base import Source, register

# BiblioCommons format codes -> reader-friendly names for notifications.
FORMAT_NAMES = {
    "BK": "print book",
    "PAPERBACK": "print book",
    "LPRINT": "large print book",
    "EBOOK": "ebook",
    "AB": "audiobook (CD)",
    "AUDIOBOOK": "audiobook",
    "MUSIC_CD": "music CD",
    "DVD": "DVD",
    "BLURAY": "Blu-ray",
}

@register
class BiblioCommonsSource(Source):
    kind = "bibliocommons"

    @property
    def subdomain(self) -> str:
        sub = self.cfg.get("library")
        if not sub:
            raise ValueError(
                f"source '{self.source_id}': set 'library' to your BiblioCommons "
                "subdomain (the X in https://X.bibliocommons.com)"
            )
        return sub

    def search_url(self, query: str) -> str:
        from urllib.parse import quote
        return (
            f"https://{self.subdomain}.bibliocommons.com/v2/search"
            f"?query={quote(query)}&searchType=smart"
        )

    def check(self, config: Config) -> list[Observation]:
        sess = http.session()
        observations: list[Observation] = []
        wanted_formats = {
            f.upper() for f in self.cfg.get("formats", ["BK", "EBOOK", "AB", "AUDIOBOOK"])
        }

        for book in config.books:
            query = book.isbn or book.title + (f" {book.author}" if book.author else "")
            url = self.search_url(query)
            resp = http.get(sess, url)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"search returned HTTP {resp.status_code} for {book.title!r} "
                    f"(bot protection? try from a residential IP)"
                )
            for bib in _extract_bibs(resp.text):
                title = bib.get("title") or ""
                if book.bib_id:
                    if str(bib.get("bibId")) != book.bib_id:
                        continue
                elif not book.isbn and not titles_match(book.title, title):
                    continue
                elif not book.isbn and book.author and \
                        not author_matches(book.author, _author_str(bib)):
                    continue  # fuzzy title hit on the wrong author
                fmt = (bib.get("format") or "?").upper()
                if wanted_formats and fmt not in wanted_formats and "?" != fmt:
                    continue
                status = (bib.get("status") or "unknown").upper()
                friendly = FORMAT_NAMES.get(fmt, fmt.lower())
                observations.append(Observation(
                    source=self.source_id,
                    item_key=book.key,
                    item_label=str(book),
                    summary=f"{friendly} in {self.subdomain} library catalog",
                    url=url,
                    # Presence in the catalog is the hit — the user is happy
                    # to join hold queues, so availability isn't the bar and
                    # status/copy-count changes don't re-notify.
                    positive=True,
                    event=f"{friendly} in catalog",
                    detail={"format": fmt, "status": status,
                            "available_copies": bib.get("availableCopies"),
                            "found_title": title},
                ))
        return observations

    def search_books(self, query: str) -> list[dict[str, Any]]:
        """Candidate records for `tracker add book` — exact catalog spellings."""
        sess = http.session()
        resp = http.get(sess, self.search_url(query))
        if resp.status_code != 200:
            raise RuntimeError(f"search returned HTTP {resp.status_code}")
        out = []
        for bib in _extract_bibs(resp.text):
            authors = bib.get("authors")
            if isinstance(authors, list):
                authors = ", ".join(
                    a.get("name", str(a)) if isinstance(a, dict) else str(a)
                    for a in authors
                )
            out.append({
                "source": self.source_id,
                "title": bib.get("title"),
                "author": authors,
                "format": bib.get("format"),
                "bib_id": str(bib["bibId"]) if bib.get("bibId") else None,
            })
        return out

    def probe(self, config: Config, query: str | None = None) -> str:
        sess = http.session()
        q = query or (str(config.books[0]) if config.books else "the hobbit")
        url = self.search_url(q)
        resp = http.get(sess, url)
        bibs = list(_extract_bibs(resp.text)) if resp.status_code == 200 else []
        return (
            f"GET {url}\nHTTP {resp.status_code}, {len(resp.text)} bytes\n"
            f"extracted {len(bibs)} bib records:\n"
            + json.dumps(bibs[:5], indent=2)[:3000]
        )


def _author_str(bib: dict) -> str | None:
    authors = bib.get("authors")
    if isinstance(authors, list):
        authors = ", ".join(
            a.get("name", str(a)) if isinstance(a, dict) else str(a)
            for a in authors
        )
    return str(authors) if authors else None


_JSON_SCRIPT_RE = re.compile(
    r'<script[^>]+type="application/json"[^>]*>(.*?)</script>', re.DOTALL
)


def _extract_bibs(html: str) -> Iterator[dict[str, Any]]:
    """Walk every embedded JSON blob for objects that look like bib records."""
    for m in _JSON_SCRIPT_RE.finditer(html):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        yield from _walk_for_bibs(data)


def _walk_for_bibs(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        # BiblioCommons bib entities carry briefInfo{title, format, ...}
        # and an availability object; be liberal about where they sit.
        brief = node.get("briefInfo")
        if isinstance(brief, dict) and brief.get("title"):
            avail = node.get("availability") or {}
            yield {
                "title": brief.get("title"),
                "subtitle": brief.get("subtitle"),
                "authors": brief.get("authors"),
                "format": brief.get("format"),
                "status": avail.get("status") or avail.get("statusType"),
                "availableCopies": avail.get("availableCopies"),
                "heldCopies": avail.get("heldCopies"),
                "bibId": node.get("id") or brief.get("id"),
            }
        else:
            for v in node.values():
                yield from _walk_for_bibs(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_for_bibs(v)
