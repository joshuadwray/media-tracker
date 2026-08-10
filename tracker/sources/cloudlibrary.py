"""cloudLibrary (Bibliotheca) ebook/audiobook watcher.

cloudLibrary's web patron app (ebook.yourcloudlibrary.com/library/<id>)
talks to an unauthenticated-search backend. The endpoint has moved over
the years, so we try a small chain of known shapes and use the first
that answers with parseable results. `probe` shows exactly what each
endpoint returned so a future move is a one-line fix.

Find your library id in the URL you use to log in:
https://ebook.yourcloudlibrary.com/library/<THIS PART>/...

The records are richer than they look, and richer than Libby's: alongside
`totalCopies` and `currentlyAvailable` the search returns
`currentlyReserved` (the hold queue), `currentlyLoaned`, `isPreSale` and
an explicit `format`. We read all of it, so cloudLibrary titles rank and
ratchet on wait exactly like Libby ones.
"""
from __future__ import annotations

import json
from typing import Any

from .. import http
from ..availability import describe, wait_days
from ..config import Config
from ..matching import author_matches, search_query, titles_match
from ..models import Observation, medium_for
from .base import Source, register


@register
class CloudLibrarySource(Source):
    kind = "cloudlibrary"
    default_loan_days = 21  # cloudLibrary's standard loan

    @property
    def library_id(self) -> str:
        lib = self.cfg.get("library")
        if not lib:
            raise ValueError(
                f"source '{self.source_id}': set 'library' to your cloudLibrary id "
                "(from https://ebook.yourcloudlibrary.com/library/<id>)"
            )
        return lib

    @property
    def label(self) -> str:
        """Human name for notifications; ids like 'lewisvillepubliclibrary'
        read badly in a push. Defaults to the id."""
        return self.cfg.get("label") or self.library_id

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
                # owned=yes limits results to this library's pool.
                # Consortium copies vanish when on loan, but the
                # absence-gap feature re-notifies when they return.
                # The first request 302s to itself to set a session cookie;
                # requests.Session follows it and keeps the cookie.
                "method": "GET",
                "url": f"https://ebook.yourcloudlibrary.com/library/{lib}"
                       f"/search?title={q}&format=&available=any&language="
                       f"&sort=relevance&segment=posts&orderBy=relevence"
                       f"&owned=yes&_data=routes%2Flibrary.%24name.search",
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
            query = book.isbn or search_query(book.title)
            items, _ = self._search(sess, query)
            for item in items:
                if item.get("borrowable") is False:
                    continue  # marketplace-only record, not in this
                              # library's owned or pay-per-use pool
                title = item.get("title") or ""
                if not book.isbn and not titles_match(book.title, title):
                    continue
                if not book.isbn and book.author and \
                        not author_matches(book.author, item.get("authors")):
                    continue  # fuzzy title hit on the wrong author
                fmt = item.get("format") or "ebook/audio"
                wait = wait_days(item.get("available_copies"), item.get("owned"),
                                 item.get("holds"), self.loan_days)
                observations.append(Observation(
                    source=self.source_id,
                    item_key=book.key,
                    item_label=str(book),
                    summary=(f"{fmt} in cloudLibrary catalog ({self.label})"
                             f" — {_describe_copies(item, wait)}"),
                    url=f"https://ebook.yourcloudlibrary.com/library/{self.library_id}"
                        f"/search?query={query.replace(' ', '%20')}",
                    positive=True,  # in catalog = hit; hold queues are fine
                    event=f"{fmt} in catalog",  # availability flips don't re-notify
                    medium=medium_for(fmt),
                    wait=wait,
                    distance_mi=self.distance_mi,
                    loan_days=self.loan_days,
                    source_label=self.label,
                    detail={"found_title": title,
                            "author": item.get("authors"),
                            "owned_copies": item.get("owned"),
                            "available_copies": item.get("available_copies"),
                            "loaned": item.get("loaned"),
                            "holds": item.get("holds"),
                            "pre_release": item.get("pre_release"),
                            "pages": item.get("pages")},
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
            if item.get("borrowable") is False:
                continue
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


def _raw_authors(node: dict) -> str | None:
    """Author names off a raw catalog record, for the wrong-book guard."""
    authors = node.get("Authors") or node.get("authors")
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
                items.append({
                    "title": title,
                    "format": _format(node),
                    "authors": _raw_authors(node),
                    "available": _availability(node),
                    "borrowable": _borrowable(node),
                    # The queue, which we used to throw away. `currentlyLoaned`
                    # has no Libby equivalent: it separates "no copies free
                    # because they're all out" from "no copies at all".
                    "owned": node.get("totalCopies"),
                    "available_copies": node.get("currentlyAvailable"),
                    "loaned": node.get("currentlyLoaned"),
                    "holds": node.get("currentlyReserved"),
                    "pre_release": bool(node.get("isPreSale")),
                    "pages": node.get("totalExtents"),
                })
                return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return items


def _describe_copies(item: dict, wait: int | None) -> str:
    """Copies and queue, in the same shape the Libby source uses."""
    owned = item.get("owned") or 0
    available = item.get("available_copies") or 0
    holds = item.get("holds") or 0
    if item.get("pre_release"):
        return f"pre-release{f', {holds} holds' if holds else ''}"
    text = f"{available}/{owned} available"
    if holds:
        text += f", {holds} hold{'s' if holds != 1 else ''}"
    if wait is not None and wait > 0:
        text += f" ({describe(wait)})"
    return text


def _format(node: dict) -> str | None:
    """Which format a record is.

    The search payload names it outright — `format` is "audio" or "digital"
    on every live record. The older inference (audiobooks carry a duration,
    ebooks a nonzero epubFormat) stays as a fallback for payload shapes we
    haven't seen.
    """
    fmt = node.get("format")
    if isinstance(fmt, str):
        if fmt.lower() in ("audio", "audiobook"):
            return "audiobook"
        if fmt.lower() in ("digital", "ebook", "epub"):
            return "ebook"
    named = (node.get("productFormDescription")
             or node.get("MediaType") or node.get("mediaType"))
    if named:
        return named
    if node.get("duration"):
        return "audiobook"
    if node.get("epubFormat"):
        return "ebook"
    return None


def _borrowable(node: dict) -> bool | None:
    """Can a patron of THIS library borrow the record?

    owned=any search results span the whole cloudLibrary marketplace.
    Borrowable = the library owns copies (totalCopies > 0) OR the title
    is in its pay-per-use/consortium pool (isPayPerUse — how shared-in
    titles appear; they carry no copy counts). Marketplace-only records
    are isPayPerUse false with null copies. None = legacy payload shape
    without these fields (callers treat unknown as borrowable).
    """
    if "isPayPerUse" not in node and "totalCopies" not in node:
        return None
    return bool(node.get("isPayPerUse")) or bool(node.get("totalCopies") or 0)


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
