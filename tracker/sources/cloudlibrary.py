"""cloudLibrary (Bibliotheca) ebook/audiobook watcher.

cloudLibrary's web patron app (ebook.yourcloudlibrary.com/library/<id>)
talks to an unauthenticated-search backend. The endpoint has moved over
the years, so we try a small chain of known shapes and use the first
that answers with parseable results. `probe` shows exactly what each
endpoint returned so a future move is a one-line fix.

Find your library id in the URL you use to log in:
https://ebook.yourcloudlibrary.com/library/<THIS PART>/...
"""
from __future__ import annotations

import json
from typing import Any

from .. import http
from ..config import Config
from ..matching import author_matches, titles_match
from ..models import Observation
from .base import Source, register


@register
class CloudLibrarySource(Source):
    kind = "cloudlibrary"

    @property
    def library_id(self) -> str:
        lib = self.cfg.get("library")
        if not lib:
            raise ValueError(
                f"source '{self.source_id}': set 'library' to your cloudLibrary id "
                "(from https://ebook.yourcloudlibrary.com/library/<id>)"
            )
        return lib

    def _endpoints(self, query: str) -> list[dict[str, Any]]:
        from urllib.parse import quote
        lib = self.library_id
        q = quote(query)
        return [
            {   # current web patron (Remix app): route loaders return JSON
                # when asked with _data=<route id>. NOTE: format= must be
                # present but EMPTY (a value like "all" returns 0 results),
                # and the library id is case-sensitive ("Denton", not
                # "denton" — wrong case redirects to the marketing site).
                # The first request 302s to itself to set a session cookie;
                # requests.Session follows it and keeps the cookie.
                "method": "GET",
                "url": f"https://ebook.yourcloudlibrary.com/library/{lib}"
                       f"/search?title={q}&format=&available=any&language="
                       f"&sort=relevance&segment=posts&orderBy=relevence"
                       f"&owned=any&_data=routes%2Flibrary.%24name.search",
            },
            {   # legacy web-patron search API (404 as of 2026-07)
                "method": "GET",
                "url": f"https://ebook.yourcloudlibrary.com/uisvc/{lib}"
                       f"/Search/CatalogSearch?media=all&src=lib&segment=posts"
                       f"&and=SearchString%3D{q}",
            },
            {   # older UI-service POST shape
                "method": "POST",
                "url": f"https://ebook.yourcloudlibrary.com/uisvc/{lib}/Search/CatalogSearch",
                "payload": {"SearchString": query, "Take": 20, "Skip": 0,
                            "SortBy": "Relevance", "Format": "all"},
            },
        ]

    def check(self, config: Config) -> list[Observation]:
        sess = http.session()
        observations: list[Observation] = []
        for book in config.books:
            query = book.isbn or book.title
            items, _ = self._search(sess, query)
            for item in items:
                title = item.get("title") or ""
                if not book.isbn and not titles_match(book.title, title):
                    continue
                if not book.isbn and book.author and \
                        not author_matches(book.author, _raw_authors(item)):
                    continue  # fuzzy title hit on the wrong author
                fmt = item.get("format") or "ebook/audio"
                observations.append(Observation(
                    source=self.source_id,
                    item_key=book.key,
                    item_label=str(book),
                    summary=f"{fmt} in cloudLibrary catalog ({self.library_id})",
                    url=f"https://ebook.yourcloudlibrary.com/library/{self.library_id}"
                        f"/search?query={query.replace(' ', '%20')}",
                    positive=True,  # in catalog = hit; hold queues are fine
                    event=f"{fmt} in catalog",  # availability flips don't re-notify
                    detail={"found_title": title, "raw": item.get("raw", {})},
                ))
        return observations

    def _search(self, sess, query: str) -> tuple[list[dict[str, Any]], str]:
        """Try each known endpoint shape; return (parsed items, transcript)."""
        transcript: list[str] = []
        for ep in self._endpoints(query):
            try:
                if ep["method"] == "GET":
                    resp = http.get(sess, ep["url"],
                                    headers={"Accept": "application/json"})
                    if resp.status_code == 204:
                        # Remix answers a data request with 204 + Set-Cookie
                        # when it wants to redirect; retry with the cookie.
                        resp = http.get(sess, ep["url"],
                                        headers={"Accept": "application/json"})
                else:
                    resp = http.post_json(sess, ep["url"], ep["payload"])
                transcript.append(
                    f"{ep['method']} {ep['url']} -> HTTP {resp.status_code}, "
                    f"{len(resp.content)} bytes"
                )
                if resp.status_code != 200:
                    continue
                data = resp.json()
            except (ValueError, http.requests.RequestException) as exc:
                transcript.append(f"  failed: {type(exc).__name__}: {exc}")
                continue
            items = _parse_items(data)
            transcript.append(f"  parsed {len(items)} items")
            if items:
                return items, "\n".join(transcript)
        return [], "\n".join(transcript)

    def search_books(self, query: str) -> list[dict[str, Any]]:
        """Candidate records for `tracker add book` — exact catalog spellings."""
        sess = http.session()
        items, _ = self._search(sess, query)
        out = []
        for item in items:
            raw = item.get("raw", {})
            isbn = raw.get("ISBN") or raw.get("isbn")
            authors = raw.get("Authors") or raw.get("authors")
            if isinstance(authors, list):
                authors = ", ".join(str(a) for a in authors)
            out.append({
                "source": self.source_id,
                "title": item.get("title"),
                "author": authors,
                "format": item.get("format"),
                "isbn": str(isbn) if isbn else None,
            })
        return out

    def probe(self, config: Config, query: str | None = None) -> str:
        sess = http.session()
        q = query or (config.books[0].title if config.books else "the hobbit")
        items, transcript = self._search(sess, q)
        return (
            f"query: {q!r}\n{transcript}\n\nfirst items:\n"
            + json.dumps(items[:5], indent=2, default=str)[:3000]
        )


def _raw_authors(item: dict) -> str | None:
    raw = item.get("raw", {})
    authors = raw.get("Authors") or raw.get("authors")
    if isinstance(authors, list):
        authors = ", ".join(str(a) for a in authors)
    return str(authors) if authors else None


def _parse_items(data: Any) -> list[dict[str, Any]]:
    """Liberal parse: accept a list of item dicts wherever the payload nests it."""
    items: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            title = node.get("Title") or node.get("title")
            if title and any(k in node for k in (
                "Authors", "authors", "ISBN", "isbn", "MediaType", "mediaType", "Id", "id"
            )):
                # Current API doesn't name the format, but audiobooks carry a
                # duration and ebooks a nonzero epubFormat.
                fmt = (node.get("productFormDescription")
                       or node.get("MediaType") or node.get("mediaType"))
                if not fmt:
                    if node.get("duration"):
                        fmt = "audiobook"
                    elif node.get("epubFormat"):
                        fmt = "ebook"
                items.append({
                    "title": title,
                    "format": fmt,
                    "available": _availability(node),
                    "raw": {k: node[k] for k in list(node)[:12]},
                })
                return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return items


def _availability(node: dict) -> bool | None:
    for key in ("IsAvailable", "isAvailable", "Available", "available"):
        if key in node:
            return bool(node[key])
    for key in ("CurrentAvailable", "currentAvailable", "AvailableCopies",
                "currentlyAvailable"):
        if key in node:
            try:
                return int(node[key]) > 0
            except (TypeError, ValueError):
                return None
    return None
