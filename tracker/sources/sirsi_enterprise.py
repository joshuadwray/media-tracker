"""SirsiDynix Enterprise catalog watcher (Lewisville Public Library and many others).

Enterprise is an Apache Tapestry app, and its two public surfaces disagree
about almost everything, so this source uses one for discovery and one for
availability:

* **Discovery — the Atom feed** at `/client/rss/hitlist/default/qu=<query>`.
  Unauthenticated, no session, and it returns clean structured metadata
  (title, author, publication date, format, record id). Critically it also has
  much better *recall* than the HTML search page: the search page silently
  drops to zero hits when a query mixes a title with an author ("Fruit Fly"
  finds the book, "Fruit Fly Silver" finds nothing), while the feed handles
  both. Six of eight watched books were found via the feed and one via the
  search page, on the same queries.
* **Availability — one AJAX call per record.** Search results and detail pages
  both server-render their availability cells reading "Searching..." and fill
  them in afterwards, so there is no way around a second request.

The feed indexes summary text as well as bibliographic fields, so a bare title
happily matches an unrelated book's plot blurb ("one leg on earth" returns
Mark Greaney's *Armored*). Recall is cheap here and precision is not, which is
why every hit still goes through titles_match + author_matches.

The availability call is the fiddly part, and the fiddliness is one header:

    X-Requested-With: XMLHttpRequest   <- arms Enterprise's CSRF check
    sdcsrf: <token scraped from any page in the same session>

The token must travel as a *header* named `sdcsrf`. Enterprise embeds an
`sdcsrf=` query parameter in its own markup, but replaying that parameter (or
sending no token) alongside X-Requested-With gets "403 Invalid CSRF client
token"; dropping X-Requested-With instead passes CSRF but returns a Tapestry
"System Error" page, because a zone update needs the AJAX marker to render as
a partial. Both halves are required.

The token is per *session*, not per page, so one cheap request to the catalog
home page mints a token good for every record afterwards. Do not try to reuse
the Tapestry event links embedded in a page: those are single-use and 403 on
replay even from a real browser. `probe` dumps parsed records and raw
availability so this is easy to re-point if the feed shape shifts.
"""
from __future__ import annotations

import html
import json
import re
import unicodedata
from typing import Any, Optional
from urllib.parse import quote

from .. import http
from ..availability import wait_after_arrival, wait_days
from ..config import Config
from ..matching import author_matches, search_query, titles_match
from ..models import Observation, medium_for
from .base import Source, register

# Enterprise format labels -> reader-friendly names for notifications.
# Large print is not its own format here: those records report "Books" and
# only announce themselves with a marker in the title (see _friendly).
FORMAT_NAMES = {
    "Books": "print book",
    "Large Print": "large print book",
    "Audio disc": "audiobook (CD)",
    "Music Sound Recording": "music CD",
    "Video disc": "DVD",
}

# A record with more hits than this behind it is a query that went wrong (a
# bare common word matching hundreds of summaries), not a book we can use.
MAX_HITS = 60

_LARGE_PRINT_RE = re.compile(r"\[\s*large\s+(?:type|print)\s*\]", re.I)


@register
class SirsiEnterpriseSource(Source):
    kind = "sirsi-enterprise"
    default_loan_days = 21

    @property
    def host(self) -> str:
        host = self.cfg.get("host")
        if not host:
            raise ValueError(
                f"source '{self.source_id}': set 'host' to your Enterprise "
                "hostname (e.g. lewp.ent.sirsi.net). Enterprise hostnames "
                "follow no pattern — find yours in the catalog URL."
            )
        return str(host).replace("https://", "").rstrip("/")

    @property
    def profile(self) -> str:
        return str(self.cfg.get("profile") or "default")

    @property
    def base_url(self) -> str:
        return f"https://{self.host}/client/en_US/{self.profile}"

    def feed_url(self, query: str) -> str:
        return (f"https://{self.host}/client/rss/hitlist/{self.profile}"
                f"/qu={quote(query)}")

    def detail_url(self, record_id: str) -> str:
        """Permalink for one catalog record, in Enterprise's escaped form."""
        return (f"{self.base_url}/search/detailnonmodal/"
                f"ent:$002f$002fSD_ILS$002f0$002fSD_ILS:{record_id}/one")

    def check(self, config: Config) -> list[Observation]:
        sess = http.session()
        observations: list[Observation] = []
        wanted_formats = set(self.cfg.get("formats") or ["Books"])
        token: Optional[str] = None

        for book in config.books:
            query = _query_for(book)
            url = self.feed_url(query)
            resp = http.get(sess, url)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"catalog feed returned HTTP {resp.status_code} for {book.title!r}"
                )
            records = _parse_feed(resp.text)
            if len(records) > MAX_HITS:
                continue

            for rec in records:
                title = rec.get("title") or ""
                if not titles_match(book.title, title):
                    continue
                if book.author and not author_matches(book.author, rec.get("author")):
                    continue  # matched a summary blurb, not the book
                fmt = rec.get("format") or "?"
                if wanted_formats and fmt not in wanted_formats and fmt != "?":
                    continue

                if token is None:
                    token = _mint_token(sess, self.base_url)
                counts = _availability(sess, self.host, self.profile,
                                       rec["record_id"], token)

                available = _count(counts.get("availableCount"))
                copies = _count(counts.get("copyCount"))
                holds = _count(counts.get("holdCount"))

                # Enterprise reports copyCount 0 for a record the library has
                # ordered but not received — an honest zero, unlike
                # BiblioCommons, which double-counts on-order items. The
                # ordered quantity rides along in a separate zone; fall back
                # to one copy (the longest, least flattering read) only when
                # it's missing. Either way the result is provisional, so it
                # ranks without printing a duration or moving a watermark.
                on_order = copies == 0
                ordered = _count(counts.get("onOrderCopies"))
                if on_order:
                    wait = wait_after_arrival(holds, ordered or 1, self.loan_days)
                else:
                    wait = wait_days(available, copies, holds, self.loan_days)

                friendly = _friendly(fmt, title)
                observations.append(Observation(
                    source=self.source_id,
                    item_key=book.key,
                    item_label=str(book),
                    summary=(f"{friendly} in {self.label} library catalog — "
                             f"{_shelf_state(available, copies, holds, on_order, ordered)}"),
                    url=self.detail_url(rec["record_id"]),
                    # Presence in the catalog is the hit — the user is happy
                    # to join hold queues, so availability isn't the bar and
                    # copy-count changes don't re-notify.
                    positive=True,
                    event=f"{friendly} in catalog",
                    medium=medium_for(friendly),
                    wait=wait,
                    provisional=on_order,
                    distance_mi=self.distance_mi,
                    loan_days=self.loan_days,
                    source_label=self.label,
                    detail={"format": fmt, "record_id": rec["record_id"],
                            "author": rec.get("author"),
                            "available_copies": available,
                            "held_copies": holds,
                            "copies": copies,
                            "on_order": on_order,
                            "copies_on_order": ordered,
                            "call_number": rec.get("call_number"),
                            "year": rec.get("year"),
                            "found_title": title},
                ))
        return observations

    def probe(self, config: Config, query: str | None = None) -> str:
        sess = http.session()
        q = query or (_query_for(config.books[0]) if config.books else "the hobbit")
        url = self.feed_url(q)
        resp = http.get(sess, url)
        records = _parse_feed(resp.text) if resp.status_code == 200 else []
        out = []
        if records:
            token = _mint_token(sess, self.base_url)
            for rec in records[:5]:
                out.append(dict(rec, availability=_availability(
                    sess, self.host, self.profile, rec["record_id"], token)))
        return (
            f"GET {url}\nHTTP {resp.status_code}, {len(resp.text)} bytes\n"
            f"parsed {len(records)} physical (SD_ILS) records; "
            f"availability for the first {len(out)}:\n"
            + json.dumps(out, indent=2)[:3000]
        )


def _query_for(book: Any) -> str:
    """What to type into the catalog search box.

    Author is doing real work: the feed searches summary text too, so a bare
    "one leg on earth" returns Mark Greaney's *Armored* (whose blurb mentions
    a lost leg) while "one leg on earth aguda" correctly returns nothing.

    Diacritics are folded away because Enterprise does not fold them itself:
    "Dèy" returns 300 unrelated Freegal music tracks and none of the book,
    while "dey danticat" finds it. This is a search-box-only transform, the
    same bargain search_query() makes with parentheticals — matching still
    runs against the full original title via titles_match.
    """
    title = search_query(book.title)
    query = title + (f" {book.author}" if book.author else "")
    return _fold(query)


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def _friendly(fmt: str, title: str) -> str:
    """Reader-facing name for a format.

    Large-print editions carry the format "Books" like any other print copy;
    the only tell is a "[large print]" / "[large type]" marker in the title.
    """
    if fmt == "Books" and _LARGE_PRINT_RE.search(title):
        return FORMAT_NAMES["Large Print"]
    return FORMAT_NAMES.get(fmt, fmt.lower())


def _count(raw: Any) -> Optional[int]:
    """Enterprise sends counts as strings, and as the literal "Not Available"
    for federated (non-catalog) records. Anything non-numeric means we don't
    know, which is not the same as zero."""
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _shelf_state(available: Optional[int], copies: Optional[int],
                 holds: Optional[int], on_order: bool,
                 ordered: Optional[int] = None) -> str:
    """Whether you could walk in and pick it up.

    On-order titles name the ordered quantity when Enterprise reports it,
    matching how the BiblioCommons source words the same situation — the
    queue only means something next to the number of copies coming.
    """
    if available:
        return f"{available} on shelf"
    if on_order:
        n = f"{ordered} on order" if ordered else "on order"
        if not holds:
            return f"{n}, no holds yet"
        return f"{n}, {holds} hold{'s' if holds != 1 else ''} ahead"
    if copies:
        return (f"all {copies} out" if copies > 1 else "checked out") + (
            f", {holds} hold{'s' if holds != 1 else ''}" if holds else "")
    return "availability unknown"


_ENTRY_RE = re.compile(r"<entry>(.*?)</entry>", re.S)
# Only ent://SD_ILS ids are the library's own catalog. The ent://ERC_ rows are
# federated e-resources — Lewisville's cloudLibrary and Freegal collections —
# which cloudlibrary-lewisville already watches and which report
# "Not Available" for every availability count anyway.
_ID_RE = re.compile(r"<id>ent://SD_ILS/0/SD_ILS:(\d+)</id>")
_TITLE_RE = re.compile(r'<title type="html">(.*?)</title>', re.S)
_CONTENT_RE = re.compile(r'<content type="html">(.*?)</content>', re.S)


def _parse_feed(xml: str) -> list[dict[str, Any]]:
    """Physical catalog records from an Enterprise Atom feed.

    The <content> is a double-escaped HTML fragment of label/value pairs:
    `by&#160;Silver, Josh<br/>Publication Date&#160;2026<br/>Format:&#160;Books<br/>`
    """
    records = []
    for raw in _ENTRY_RE.findall(xml):
        rid = _ID_RE.search(raw)
        title = _TITLE_RE.search(raw)
        if not rid or not title:
            continue
        content = _CONTENT_RE.search(raw)
        fields = _fields(content.group(1)) if content else {}
        records.append({
            "record_id": rid.group(1),
            "title": _text(title.group(1)),
            "author": fields.get("by"),
            "format": fields.get("Format:"),
            "call_number": fields.get("Call Number"),
            "year": _year(fields.get("Publication Date")),
        })
    return records


def _fields(content: str) -> dict[str, str]:
    """Split a feed entry's <content> into its label/value pairs."""
    # Escaped once by Atom and again by Enterprise itself, so unescape twice.
    text = html.unescape(html.unescape(content)).replace("\xa0", " ")
    out = {}
    for chunk in re.split(r"<br\s*/?>", text):
        chunk = _text(chunk)
        for label in ("by", "Publication Date", "Format:", "Call Number", "Summary"):
            if chunk.startswith(label):
                out.setdefault(label, chunk[len(label):].strip())
                break
    return out


def _text(raw: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()


def _year(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    m = re.search(r"(1[5-9]\d\d|20\d\d)", raw)
    return int(m.group(1)) if m else None


_CSRF_RE = re.compile(r"sdcsrf=([0-9a-f-]{36})")
# Enterprise wraps the payload in a Tapestry zone update:
# {"inits":[{"evalScript":["updateWebServiceFields({...});", ...]}]}
_FIELDS_RE = re.compile(r"updateWebServiceFields\((\{.*?\})\);", re.S)


def _mint_token(sess: Any, base_url: str) -> Optional[str]:
    """Scrape a CSRF token off the catalog home page.

    Tokens are per session rather than per page render, so one ~30KB request
    covers every availability call in a run.
    """
    resp = http.get(sess, base_url)
    m = _CSRF_RE.search(resp.text) if resp.status_code == 200 else None
    return m.group(1) if m else None


def _availability(sess: Any, host: str, profile: str, record_id: str,
                  token: Optional[str]) -> dict[str, Any]:
    """Copy/hold counts for one record.

    On top of Enterprise's own fields this adds `onOrderCopies` — our name,
    not theirs, derived from the zone table described in _on_order_copies.

    Returns {} rather than raising if anything goes wrong — a record with
    unknown availability still belongs on the dashboard, it just can't be
    given a wait.
    """
    if not token:
        return {}
    d = quote(f"ent://SD_ILS/0/SD_ILS:{record_id}~~0", safe="")
    url = (f"https://{host}/client/en_US/{profile}/search/detailnonmodal.detail"
           f".detailavailabilityaccordions.boundwithzone:lookupavailability/"
           f"ent:$002f$002fSD_ILS$002f0$002fSD_ILS:{record_id}"
           f"/ILS/0/false/CALLNUMBER$002cLIBRARY?d={d}")
    resp = http.get(sess, url, headers={"X-Requested-With": "XMLHttpRequest",
                                        "sdcsrf": token})
    if resp.status_code != 200:
        return {}
    try:
        payload = resp.json()
        scripts = payload["inits"][0]["evalScript"]
    except (ValueError, KeyError, IndexError, TypeError):
        return {}
    for script in scripts if isinstance(scripts, list) else [scripts]:
        m = _FIELDS_RE.search(str(script))
        if m:
            try:
                fields = json.loads(m.group(1))
            except ValueError:
                return {}
            ordered = _on_order_copies(payload)
            if ordered is not None:
                fields["onOrderCopies"] = ordered
            return fields
    return {}


# The on-order zone is a small table, one row per library, with the ordered
# quantity under a column keyed by name rather than position:
#   <td class='detailItemsTable_SD_ORDER_COPIES'>1</td>
_ORDER_COPIES_RE = re.compile(
    r"<td[^>]*\bdetailItemsTable_SD_ORDER_COPIES\b[^>]*>\s*(\d+)\s*</td>", re.I
)


def _on_order_copies(payload: dict[str, Any]) -> Optional[int]:
    """How many copies the library has actually ordered.

    Enterprise does publish this, in a `detailOnOrderDiv0` zone alongside the
    counts — which matters because an on-order record reports copyCount 0, so
    without this the queue has to be divided by a guess. Every on-order title
    at Lewisville is a single copy today, but a four-copy order estimated at
    one copy comes out four times too slow.

    Rows are summed: the table is per-library, and for a single-library system
    that total is the number of copies coming. Returns None when the zone is
    missing or unparseable, so the caller can fall back rather than divide by
    a zero it invented.
    """
    zones = payload.get("zones")
    if not isinstance(zones, dict):
        return None
    total = 0
    found = False
    for name, markup in zones.items():
        if "onorder" not in str(name).lower():
            continue
        for n in _ORDER_COPIES_RE.findall(str(markup)):
            total += int(n)
            found = True
    return total if found else None
