"""Libby / OverDrive ebook and audiobook watcher.

Libby's own web app reads from OverDrive's "thunder" API, which answers
unauthenticated GETs — no session cookie, no token, no endpoint chain to
try. One request per book:

    https://thunder.api.overdrive.com/v2/libraries/<key>/media?query=<q>

Find your library's key by loading https://libbyapp.com/library/<key>,
or look it up at https://thunder.api.overdrive.com/v2/libraries/<guess>.
CHECK THE `status` FIELD in that response: a terminated collection stays
queryable and answers with real-looking media, so a dead library reads
exactly like a live one (Denton dropped Libby but its key still
responds — see TODO.md).

Copies owned, copies free and the hold count all come back with the
search, so the notification can say how long the queue is. We compute the
wait ourselves rather than using `estimatedWaitDays`, which is not an
estimate — see availability.py.

Presence in the catalog is still the hit: the user is happy to join a
hold queue, so the event stays availability-independent and the volatile
numbers live in the summary. The queue is not *ignored*, though — the
shortest wait across a book's libraries is ratcheted in state.media, so a
months-long queue collapsing to days speaks up once.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from .. import http
from ..availability import describe, wait_days
from ..config import Config
from ..matching import author_matches, search_query, titles_match
from ..models import Observation, medium_for
from .base import Source, register

API = "https://thunder.api.overdrive.com/v2/libraries"
DEFAULT_FORMATS = ("ebook", "audiobook")
PER_PAGE = 24


@register
class LibbySource(Source):
    kind = "libby"
    # OverDrive's default, and what its own wait arithmetic assumes. Patrons
    # can pick 7/14/21 in Libby where the library offers it, so set
    # `loan_days` per source when you know better.
    default_loan_days = 14

    @property
    def library_key(self) -> str:
        key = self.cfg.get("library")
        if not key:
            raise ValueError(
                f"source '{self.source_id}': set 'library' to your Libby key "
                "(the segment in https://libbyapp.com/library/<key>)"
            )
        return key

    @property
    def label(self) -> str:
        return self.cfg.get("label") or self.library_key

    @property
    def formats(self) -> set[str]:
        return {f.lower() for f in (self.cfg.get("formats") or DEFAULT_FORMATS)}

    def search_url(self, query: str) -> str:
        from urllib.parse import quote
        return (f"{API}/{self.library_key}/media?query={quote(query)}"
                f"&perPage={PER_PAGE}&page=1")

    def check(self, config: Config) -> list[Observation]:
        sess = http.session()
        observations: list[Observation] = []
        for book in config.books:
            query = book.isbn or search_query(book.title)
            for item in self._search(sess, query):
                fmt = (item.get("type") or {}).get("id", "")
                if fmt not in self.formats:
                    continue  # magazines, videos
                if not _owned(item):
                    continue  # "recommend to library" record, not stock
                title = item.get("title") or ""
                if not book.isbn and not titles_match(book.title, title):
                    continue
                if not book.isbn and book.author and \
                        not author_matches(book.author, _authors(item)):
                    continue  # fuzzy title hit on the wrong author
                wait = wait_days(item.get("availableCopies"),
                                 item.get("ownedCopies"),
                                 item.get("holdsCount"), self.loan_days)
                observations.append(Observation(
                    source=self.source_id,
                    item_key=book.key,
                    item_label=str(book),
                    summary=(f"{_friendly(item)} in Libby catalog ({self.label})"
                             f" — {_availability(item, wait)}"),
                    url=f"https://libbyapp.com/library/{self.library_key}"
                        f"/media/{item.get('id')}",
                    positive=True,  # in catalog = hit; hold queues are fine
                    event=f"{_friendly(item)} in catalog",  # copies/holds flip
                                                            # without re-notifying
                    medium=medium_for(fmt),
                    wait=wait,
                    distance_mi=self.distance_mi,
                    loan_days=self.loan_days,
                    source_label=self.label,
                    detail={"found_title": title,
                            "author": item.get("firstCreatorName"),
                            "owned_copies": item.get("ownedCopies"),
                            "available_copies": item.get("availableCopies"),
                            "holds": item.get("holdsCount"),
                            # Kept for comparison only: OverDrive's own number
                            # is ceil((holds+1)/copies * 14) and ignores
                            # available copies entirely. See availability.py.
                            "estimated_wait_days": item.get("estimatedWaitDays"),
                            "pre_release": bool(item.get("isPreReleaseTitle")),
                            "release_date": (item.get("estimatedReleaseDate")
                                             or "")[:10] or None,
                            "isbn": _isbn(item)},
                ))
        return observations

    def _search(self, sess, query: str) -> list[dict[str, Any]]:
        resp = http.get(sess, self.search_url(query),
                        headers={"Accept": "application/json"})
        if resp.status_code != 200:
            raise RuntimeError(
                f"thunder search returned HTTP {resp.status_code} for "
                f"{query!r} (library key {self.library_key!r} wrong or retired?)"
            )
        return resp.json().get("items") or []

    def search_books(self, query: str) -> list[dict[str, Any]]:
        """Candidate records for `tracker add book` — exact catalog spellings."""
        sess = http.session()
        return [
            {"source": self.source_id,
             "title": item.get("title"),
             "author": item.get("firstCreatorName"),
             "format": _friendly(item),
             "isbn": _isbn(item)}
            for item in self._search(sess, query)
            if (item.get("type") or {}).get("id") in self.formats and _owned(item)
        ]

    def probe(self, config: Config, query: str | None = None) -> str:
        sess = http.session()
        q = query or (config.books[0].title if config.books else "the hobbit")
        items = self._search(sess, q)
        trimmed = [
            {k: item.get(k) for k in
             ("id", "title", "subtitle", "firstCreatorName", "type", "isOwned",
              "ownedCopies", "availableCopies", "holdsCount", "estimatedWaitDays",
              "isPreReleaseTitle", "estimatedReleaseDate")}
            for item in items[:5]
        ]
        return (f"query: {q!r}\nGET {self.search_url(q)}\n"
                f"{len(items)} items\n\nfirst items:\n"
                + json.dumps(trimmed, indent=2, default=str)[:3000])


def _owned(item: dict) -> bool:
    """Is this the library's own stock? OverDrive returns marketplace
    records the patron can only *recommend* for purchase; they carry no
    copies and would otherwise look like a hit."""
    return bool(item.get("isOwned")) or bool(item.get("ownedCopies") or 0)


def _friendly(item: dict) -> str:
    """"eBook"/"Audiobook" as OverDrive spells it, lowercased to match the
    other library sources' event strings."""
    name = (item.get("type") or {}).get("name") or "title"
    return name.lower()


def _availability(item: dict, wait: Optional[int]) -> str:
    """Copies, queue, and our own wait estimate.

    A pre-release title is its own sentence: OverDrive lists it with zero
    copies, so "0/0 available" reads like a dead record when it's actually
    the best moment to join the queue.
    """
    owned = item.get("ownedCopies") or 0
    available = item.get("availableCopies") or 0
    holds = item.get("holdsCount") or 0
    if item.get("isPreReleaseTitle"):
        when = (item.get("estimatedReleaseDate") or "")[:10]
        text = f"pre-release{f', out {when}' if when else ''}"
        return text + (f", {holds} hold{'s' if holds != 1 else ''}" if holds else "")
    text = f"{available}/{owned} available"
    if holds:
        text += f", {holds} hold{'s' if holds != 1 else ''}"
    if wait is not None and wait > 0:
        text += f" ({describe(wait)})"
    return text


def _authors(item: dict) -> Optional[str]:
    """Author names for the author guard. `creators` carries sortName in
    "Surname, First" form — the same shape watchlist authors use."""
    names = [c.get("sortName") or c.get("name")
             for c in (item.get("creators") or [])
             if (c.get("role") or "").lower() == "author"]
    names = [n for n in names if n]
    if not names:
        first = item.get("firstCreatorSortName") or item.get("firstCreatorName")
        return first or None
    return ", ".join(names)


def _isbn(item: dict) -> Optional[str]:
    for fmt in item.get("formats") or []:
        if fmt.get("isbn"):
            return str(fmt["isbn"])
        for ident in fmt.get("identifiers") or []:
            if ident.get("type") == "ISBN" and ident.get("value"):
                return str(ident["value"])
    return None
