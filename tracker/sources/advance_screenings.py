"""Advance / promo screening watcher (advancescreenings.com).

The studio-driven, lightly publicized word-of-mouth screenings held
before release — not the chains' "advance tickets on sale", which
chain_theaters.py already flags.

advancescreenings.com aggregates ~10 outlets (Gofobo, Alamo Drafthouse,
Mind on Movies, Irish Film Critic, Red Carpet Crash, Dallas Observer,
...) and server-renders everything we need, with no login and a
permissive robots.txt. Gofobo direct was probed and rejected: its
per-film pages are public, but it has no search and no sitemap, so
title -> id would mean crawling ~700 ids, and it lists FEWER screenings
than the aggregator does for the same film. Film Metro now redirects to
Gofobo; SeeItFirst's DNS is dead.

Two page shapes:

  /city/us/<st>/<city>
      Everything screening near that city, grouped by date then film.
      A RADIUS, not a fixed market: a Waxahachie-centred page drops
      Denton while a Denton-centred one keeps it, so the centre matters
      and `markets` takes more than one.

  /screening/<film>/us/<st>/<city>#<code>
      One card per outlet, carrying the theatre, street address, date,
      time and a /go/ pass link. Fetched only for watchlist hits.

The trap: city-page rows are per-OUTLET, not per-screening. Five rows
for Forgotten Island in Dallas were five pass-code sources for the one
AMC NorthPark show. Cards collapse on (theatre, date, time) and the
outlets ride along as alternative ways in.

Config:
  advance-screenings:
    kind: advance-screenings
    markets: [us/tx/denton, us/tx/dallas]
    tier: preferred
    distance_mi: 40
    city_tiers:
      Denton: {tier: home, distance_mi: 3}
    types: [RSVP, Redeem Link or Code, Contest, Studio Screening]
"""
from __future__ import annotations

import re
from datetime import date, datetime

from .. import http
from ..config import Config
from ..matching import normalize, titles_match
from ..models import Observation
from .base import Source, register

BASE = "https://www.advancescreenings.com"

# Free, studio-driven screenings. "Purchase Tickets" is early tickets on
# sale, which the chain sources already report as an advance flag — and
# which is what every Alamo row in this feed is.
DEFAULT_TYPES = ("RSVP", "Redeem Link or Code", "Contest", "Studio Screening")

# The one type you pay for. Config can let it through; the wording then
# has to stop calling it free, so this is read from the row rather than
# assumed from the filter.
PAID_TYPES = frozenset({"purchase tickets"})

# A busy season must not turn one page into forty requests.
MAX_DETAIL_FETCHES = 8

_DATE_HEAD_RE = re.compile(
    r'<h5 class="date">\s*([A-Za-z]+)\s+(\d+)\w*\s*<small>\((\w+)\)</small>'
)
_FILM_TITLE_RE = re.compile(r'<h4 class="movie_title[^"]*">\s*(.*?)\s*</h4>', re.DOTALL)
_ROW_LINK_RE = re.compile(
    r'href="/screening/([a-z0-9_]+)/us/([a-z]{2})/([a-z_]+)#([A-Za-z0-9]+)"\s*>\s*'
    r'(.*?)\s*</a>',
    re.DOTALL,
)
_ROW_CITY_RE = re.compile(r'href="/city/[^"]*">\s*(.*?)\s*</a>', re.DOTALL)
_ROW_OUTLET_RE = re.compile(r'href="/outlet/[^"]*">\s*(.*?)\s*</a>', re.DOTALL)

_CARD_SPLIT_RE = re.compile(r'<div id="([A-Za-z0-9]+)" class="row screening_source">')
_CARD_TYPE_RE = re.compile(r'<h4>\s*(.*?)\s+from\s', re.DOTALL)
_CARD_OUTLET_RE = re.compile(r'<h4>.*?href="/outlet/[^"]*">\s*(.*?)\s*</a>', re.DOTALL)
_CARD_DATE_RE = re.compile(r'fa-calendar-o"></i>\s*([A-Za-z]+)\s+(\d+)\w*\s*\((\w+)\)')
_CARD_TIME_RE = re.compile(r'fa-clock-o"></i>\s*([0-9]{1,2}:[0-9]{2}\s*[apAP]\.?[mM])')
_CARD_PLACE_RE = re.compile(
    r'fa-map-marker"></i>\s*<a[^>]*>\s*(.*?)\s*</a>\s*-\s*(.*?)\s*</li>', re.DOTALL
)
_CARD_GO_RE = re.compile(r'href="(/go/[A-Za-z0-9]+)"')

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday",
             "Friday", "Saturday", "Sunday")


@register
class AdvanceScreeningsSource(Source):
    kind = "advance-screenings"

    @property
    def markets(self) -> list[str]:
        markets = self.cfg.get("markets") or []
        if not markets:
            raise ValueError(
                f"source '{self.source_id}': configure at least one market, "
                "e.g. markets: [us/tx/denton]"
            )
        return [str(m).strip("/") for m in markets]

    @property
    def types(self) -> set[str]:
        return {str(t).lower() for t in (self.cfg.get("types") or DEFAULT_TYPES)}

    @property
    def city_tiers(self) -> dict[str, dict]:
        raw = self.cfg.get("city_tiers") or {}
        return {str(k).lower(): (v or {}) for k, v in raw.items()}

    def city_meta(self, city: str) -> tuple[str, float]:
        """Tier and distance for a row's city, falling back to the source's."""
        return self.venue_meta(self.city_tiers.get(city.lower(), {}))

    # ---- the run ----------------------------------------------------

    def check(self, config: Config) -> list[Observation]:
        if not config.movies:
            return []
        sess = http.session()
        rows, errors = self._market_rows(sess)
        if errors and len(errors) == len(self.markets):
            raise RuntimeError("; ".join(errors))

        wanted = self.types
        hits: dict[tuple[str, str, str], list[dict]] = {}
        for row in rows.values():
            if row["type"].lower() not in wanted:
                continue
            movie = next((m for m in config.movies
                          if titles_match(m.title, row["film"])), None)
            if movie is None:
                continue
            hits.setdefault((row["slug"], row["state"], row["city_slug"]),
                            []).append(dict(row, movie=movie))

        venue_names = _configured_venues(config)
        observations: list[Observation] = []
        # Soonest first, so the cap — if it ever bites — drops the
        # screenings you still have weeks to hear about, not tonight's.
        order = sorted(hits, key=lambda k: (_earliest(hits[k]), k))
        for key in order[:MAX_DETAIL_FETCHES]:
            slug, state, city_slug = key
            matched = hits[key]
            movie = matched[0]["movie"]
            film = matched[0]["film"]
            url = f"{BASE}/screening/{slug}/us/{state}/{city_slug}"
            try:
                resp = http.get(sess, url)
                if resp.status_code != 200:
                    continue
            except http.requests.RequestException:
                continue
            # Every card on this page is in the page's own city — the
            # segment is part of the URL. Reading it back off the street
            # address instead turns "100 S Central Expressway Richardson"
            # into a screening in Expressway, TX.
            city = matched[0]["city"] or city_slug.replace("_", " ").title()
            tier, distance = self.city_meta(city)
            for show in _collapse_cards(_parse_cards(resp.text), wanted):
                theatre = _canonical_venue(show["theatre"], venue_names)
                when = _describe(show["day"], show["time"])
                passes = show["passes"]
                extra = (f" ({len(passes)} pass links)" if len(passes) > 1
                         else "")
                free = "" if show["type"].lower() in PAID_TYPES else "free "
                observations.append(Observation(
                    source=self.source_id,
                    item_key=movie.key,
                    item_label=str(movie),
                    summary=f'"{film}" {free}advance screening at {theatre}, '
                            f"{when}{extra}",
                    # The pass-link count and the feed's "added N hours ago"
                    # churn between runs; the screening itself does not.
                    event=f'"{film}" advance screening at {theatre} on '
                          f'{show["day"].isoformat() if show["day"] else show["date_text"]}',
                    url=f"{url}#{show['code']}",
                    positive=True,
                    venue=theatre,
                    venue_tier=tier,
                    distance_mi=distance,
                    source_label=self.label,
                    detail={
                        "date": show["day"].isoformat() if show["day"] else None,
                        "date_text": show["date_text"],
                        "time": show["time"],
                        "theatre": theatre,
                        "address": show["address"],
                        "city": city,
                        "type": show["type"],
                        "outlets": show["outlets"],
                        "passes": [BASE + p for p in passes],
                    },
                ))
        return observations

    def _market_rows(self, sess) -> tuple[dict[str, dict], list[str]]:
        """Every row across the configured market pages, keyed by its
        #code anchor so two overlapping radii don't double-count."""
        rows: dict[str, dict] = {}
        errors: list[str] = []
        for market in self.markets:
            url = f"{BASE}/city/{market}"
            try:
                resp = http.get(sess, url)
                if resp.status_code != 200:
                    errors.append(f"{market}: HTTP {resp.status_code}")
                    continue
            except http.requests.RequestException as exc:
                errors.append(f"{market}: {type(exc).__name__}: {exc}")
                continue
            for row in parse_city_page(resp.text):
                rows.setdefault(row["code"], row)
        return rows, errors

    # ---- diagnostics ------------------------------------------------

    def probe(self, config: Config, query: str | None = None) -> str:
        sess = http.session()
        rows, errors = self._market_rows(sess)
        lines = [f"markets: {', '.join(self.markets)}"]
        if errors:
            lines.append("errors: " + "; ".join(errors))
        lines.append(f"{len(rows)} rows across all markets "
                     f"(rows are per-outlet, not per-screening)")
        wanted = self.types
        by_film: dict[str, list[dict]] = {}
        for row in rows.values():
            by_film.setdefault(row["film"], []).append(row)
        for film in sorted(by_film):
            group = by_film[film]
            kept = [r for r in group if r["type"].lower() in wanted]
            movie = next((m for m in config.movies
                          if titles_match(m.title, film)), None)
            mark = "WATCHED" if movie else "       "
            types = sorted({r["type"] for r in group})
            cities = sorted({r["city"] for r in group})
            dates = sorted({r["date_text"] for r in group})
            lines.append(
                f"  {mark} {film}\n"
                f"            {len(group)} rows ({len(kept)} kept), "
                f"{', '.join(types)}\n"
                f"            {', '.join(cities)} | {', '.join(dates)}"
            )
        return "\n".join(lines)


# ---- parsing ---------------------------------------------------------


def parse_city_page(html: str, today: date | None = None) -> list[dict]:
    """Rows from a /city/ page: one per outlet listing, not per screening."""
    out: list[dict] = []
    chunks = html.split('<h5 class="date">')
    for chunk in chunks[1:]:
        head = _DATE_HEAD_RE.search('<h5 class="date">' + chunk[:400])
        if not head:
            continue
        day = resolve_date(head.group(1), int(head.group(2)), head.group(3),
                           today=today)
        date_text = f"{head.group(1)} {head.group(2)} ({head.group(3)})"
        for film_chunk in chunk.split('class="row row-screenings"')[1:]:
            title = _FILM_TITLE_RE.search(film_chunk)
            if not title:
                continue
            film = _unescape(title.group(1))
            for tr in film_chunk.split("<tr>")[1:]:
                link = _ROW_LINK_RE.search(tr)
                if not link:
                    continue
                city = _ROW_CITY_RE.search(tr)
                outlet = _ROW_OUTLET_RE.search(tr)
                out.append({
                    "film": film,
                    "slug": link.group(1),
                    "state": link.group(2),
                    "city_slug": link.group(3),
                    "code": link.group(4),
                    "type": _unescape(link.group(5)),
                    "city": _city_name(_unescape(city.group(1))) if city else "",
                    "outlet": _unescape(outlet.group(1)) if outlet else "",
                    "day": day,
                    "date_text": date_text,
                })
    return out


def _parse_cards(html: str, today: date | None = None) -> list[dict]:
    """One entry per outlet from a /screening/ page, deduped by its id —
    the page renders each card twice (desktop and mobile)."""
    parts = _CARD_SPLIT_RE.split(html)
    cards: dict[str, dict] = {}
    for i in range(1, len(parts) - 1, 2):
        code, body = parts[i], parts[i + 1]
        if code in cards:
            continue
        place = _CARD_PLACE_RE.search(body)
        if not place:
            continue
        when = _CARD_DATE_RE.search(body)
        time_m = _CARD_TIME_RE.search(body)
        type_m = _CARD_TYPE_RE.search(body)
        outlet_m = _CARD_OUTLET_RE.search(body)
        go_m = _CARD_GO_RE.search(body)
        cards[code] = {
            "code": code,
            "theatre": _unescape(place.group(1)),
            "address": _unescape(place.group(2)),
            "day": (resolve_date(when.group(1), int(when.group(2)),
                                 when.group(3), today=today) if when else None),
            "date_text": (f"{when.group(1)} {when.group(2)} ({when.group(3)})"
                          if when else ""),
            "time": _unescape(time_m.group(1)) if time_m else "",
            "type": _unescape(type_m.group(1)) if type_m else "",
            "outlet": _unescape(outlet_m.group(1)) if outlet_m else "",
            "pass": go_m.group(1) if go_m else "",
        }
    return list(cards.values())


def _collapse_cards(cards: list[dict], wanted: set[str]) -> list[dict]:
    """Fold the per-outlet cards down to one entry per screening."""
    shows: dict[tuple, dict] = {}
    for card in cards:
        if card["type"].lower() not in wanted:
            continue
        key = (card["theatre"].lower(), card["date_text"], card["time"])
        show = shows.get(key)
        if show is None:
            # The first card's own id anchors the link — every card on the
            # page is a real anchor, and the rest ride along as outlets.
            show = shows[key] = dict(card, outlets=[], passes=[])
        if card["outlet"] and card["outlet"] not in show["outlets"]:
            show["outlets"].append(card["outlet"])
        if card["pass"] and card["pass"] not in show["passes"]:
            show["passes"].append(card["pass"])
    return list(shows.values())


def _earliest(rows: list[dict]) -> date:
    """The soonest dated row, with undated rows sorted last."""
    days = [r["day"] for r in rows if r["day"]]
    return min(days) if days else date.max


def resolve_date(month: str, day: int, weekday: str,
                 today: date | None = None) -> date | None:
    """Pin a year onto "September 22nd (Tuesday)".

    The feed prints no year. The next occurrence of the month/day is the
    obvious guess, but it is wrong for half of December every January, so
    the weekday it also prints is used to confirm it — and a candidate
    that can't be reconciled is dropped rather than invented.
    """
    today = today or date.today()
    try:
        month_num = datetime.strptime(month[:3], "%b").month
    except ValueError:
        return None
    if weekday.capitalize() not in _WEEKDAYS:
        weekday = ""
    for year in (today.year, today.year + 1, today.year - 1):
        try:
            candidate = date(year, month_num, day)
        except ValueError:
            continue
        if candidate < today:
            continue
        if weekday and _WEEKDAYS[candidate.weekday()] != weekday.capitalize():
            continue
        return candidate
    return None


def _describe(day: date | None, time_text: str) -> str:
    if day is None:
        return time_text or "date unknown"
    stamp = f"{_WEEKDAYS[day.weekday()][:3]} {day.strftime('%b')} {day.day}"
    return f"{stamp} {time_text}".strip()


def _city_name(label: str) -> str:
    """"Dallas, TX" -> "Dallas"."""
    return label.split(",")[0].strip()


def _configured_venues(config: Config) -> dict[str, str]:
    """Every theatre name the watchlist already spells, normalized -> as
    written. The feed writes "AMC NorthPark 15" and the config "AMC
    Northpark 15"; left alone that is two dashboard headings for one
    theatre."""
    names: dict[str, str] = {}
    for cfg in config.sources.values():
        for key in ("theatres", "pages"):
            for entry in cfg.get(key) or []:
                name = (entry or {}).get("name")
                if name:
                    names.setdefault(normalize(name), name)
    return names


def _canonical_venue(theatre: str, names: dict[str, str]) -> str:
    return names.get(normalize(theatre), theatre)


def _unescape(text: str) -> str:
    import html as _html
    return _html.unescape(re.sub(r"\s+", " ", text)).strip()
