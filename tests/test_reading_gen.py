"""Tests for the reading-log generator: deltas, slugs, ISBN bridge."""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker import reading_gen  # noqa: E402
from tracker.reading_gen import (  # noqa: E402
    Book, ReadingLog, _pages_from_ldjson, daily_pages, dump_log,
    isbn_from_cover_url, load_log, pages_by_date, slugify,
)


def _book(**kw):
    base = dict(title="A Book", author="An Author", slug="a-book",
                status="reading", sessions=[])
    base.update(kw)
    return Book(**base)


def test_delta_math_basic():
    b = _book(sessions=["2026-07-10 38", "2026-07-12 96", "2026-07-13 141"])
    out = daily_pages(b)
    assert out == {date(2026, 7, 10): 38,
                   date(2026, 7, 12): 58,
                   date(2026, 7, 13): 45}


def test_multiple_sessions_same_day_summed():
    b = _book(sessions=["2026-07-10 20", "2026-07-10 55"])
    assert daily_pages(b) == {date(2026, 7, 10): 55}


def test_correction_lower_page_is_delta_zero():
    warns = []
    b = _book(sessions=["2026-07-10 100", "2026-07-11 80", "2026-07-12 120"])
    out = daily_pages(b, warn=warns.append)
    assert out[date(2026, 7, 11)] == 0
    # delta resumes against the highest page seen, not the correction
    assert out[date(2026, 7, 12)] == 20
    assert len(warns) == 1


def test_finish_without_final_page_credits_remainder():
    b = _book(status="finished", finished="2026-07-14",
              sessions=["2026-07-10 100", "2026-07-13 300"])
    out = daily_pages(b, page_count=350)
    assert out[date(2026, 7, 14)] == 50
    # and no remainder when the last session already reached the end
    b2 = _book(status="finished", finished="2026-07-13",
               sessions=["2026-07-13 350"])
    assert daily_pages(b2, page_count=350) == {date(2026, 7, 13): 350}


def test_pages_by_date_totals_and_readers():
    a = _book(title="A", slug="a", sessions=["2026-07-10 30"])
    b = _book(title="B", slug="b", sessions=["2026-07-10 12"])
    totals, readers = pages_by_date([a, b], {"a": None, "b": None})
    assert totals == {date(2026, 7, 10): 42}
    assert readers[date(2026, 7, 10)] == [a, b]


def test_slugify():
    assert slugify("The Antidote") == "the-antidote"
    assert slugify("  Wild: A Memoir!! ") == "wild-a-memoir"


def test_load_log_rejects_duplicate_and_reserved_slugs(tmp_path):
    def write(books):
        p = tmp_path / "log.json"
        p.write_text(json.dumps({"settings": {}, "books": books}),
                     encoding="utf-8")
        return p

    dupes = [{"title": "X", "slug": "same", "sessions": []},
             {"title": "Y", "slug": "same", "sessions": []}]
    with pytest.raises(ValueError, match="duplicate slugs"):
        load_log(write(dupes))
    with pytest.raises(ValueError, match="reserved"):
        load_log(write([{"title": "Log", "slug": "log", "sessions": []}]))
    with pytest.raises(ValueError, match="bad session"):
        load_log(write([{"title": "Z", "sessions": ["yesterday 40"]}]))


def test_isbn_from_real_mzstatic_url():
    url = ("https://is1-ssl.mzstatic.com/image/thumb/Publication211/v4/3d/a9"
           "/a7/3da9a7f2-817e-6f6e-26bd-95edeb19d7fc/9780593723838.d.jpg"
           "/600x600bb.jpg")
    assert isbn_from_cover_url(url) == "9780593723838"
    assert isbn_from_cover_url("https://example.com/no-isbn.jpg") is None


def test_pages_from_ldjson():
    page = ('<html><script type="application/ld+json">'
            '{"@type":"Book","isbn":"9798217176656",'
            '"name":"Go Gentle: Oprah\'s Book Club","numberOfPages":384}'
            '</script></html>')
    assert _pages_from_ldjson(page) == 384
    assert _pages_from_ldjson("<html>no ld json</html>") is None
    # list-shaped blocks and broken JSON are tolerated
    page2 = ('<script type="application/ld+json">not json</script>'
             '<script type="application/ld+json">'
             '[{"@type":"Book","numberOfPages":144}]</script>')
    assert _pages_from_ldjson(page2) == 144


def test_dump_log_shape_and_key_order():
    b = _book(rating=4.0, page_count=None, started="2026-07-10",
              finished=None, sessions=["2026-07-10 38"])
    text = dump_log(ReadingLog(settings={"daily_goal_pages": 30}, books=[b]))
    assert text.endswith("\n")
    assert '"rating": 4,' in text  # integral float -> int, matches JS
    data = json.loads(text)
    assert list(data["books"][0]) == ["title", "author", "slug", "status",
                                      "rating", "page_count", "started",
                                      "finished", "sessions"]


# ---------------------------------------------------- publication year


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeOL:
    """Stands in for an Open Library session; records the queries made."""

    def __init__(self, *pages):
        self.pages = list(pages)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        docs = self.pages.pop(0) if self.pages else []
        return _FakeResp({"docs": docs})


def _doc(title, author, year):
    return {"title": title, "author_name": [author],
            "first_publish_year": year}


def test_pub_year_prefers_original_publication(monkeypatch):
    monkeypatch.setattr(reading_gen.time, "sleep", lambda *_: None)
    sess = _FakeOL([_doc("East of Eden", "John Steinbeck", 1952)])
    cache, covers = {}, {}
    year, source = reading_gen.resolve_pub_year(
        _book(title="east of eden", author="john steinbeck",
              slug="east-of-eden"), cache, covers, sess)
    assert (year, source) == (1952, "openlibrary-search")
    assert cache["east of eden|john steinbeck"]["year"] == 1952


def test_pub_year_rejects_right_author_wrong_book(monkeypatch):
    """The Hamnet trap: a loose q= for "land" returns Maggie O'Farrell's
    other novel, whose 2020 would look perfectly plausible in the filter."""
    monkeypatch.setattr(reading_gen.time, "sleep", lambda *_: None)
    monkeypatch.setattr(reading_gen.lists_gen, "_itunes_lookup",
                        lambda *a, **k: None)
    sess = _FakeOL([_doc("Hamnet", "Maggie O'Farrell", 2020)],
                   [_doc("Hamnet", "Maggie O'Farrell", 2020)])
    year, source = reading_gen.resolve_pub_year(
        _book(title="land", author="maggie ofarrell", slug="land"),
        {}, {}, sess)
    assert year is None and source == "unresolved"


def test_pub_year_falls_back_to_itunes_for_a_new_release(monkeypatch):
    monkeypatch.setattr(reading_gen.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        reading_gen.lists_gen, "_itunes_lookup",
        lambda *a, **k: {"track": "Dèy", "release_date": "2026-08-25T07:00:00Z",
                         "cover_url": "https://example.com/c.jpg",
                         "source": "itunes"})
    sess = _FakeOL([], [])           # OL knows nothing about it yet
    covers = {}
    year, source = reading_gen.resolve_pub_year(
        _book(title="dey", author="edwidge danticat", slug="dey"),
        {}, covers, sess)
    assert (year, source) == (2026, "itunes-edition")
    # the cover rides along for free, exactly as resolve_page_count does
    assert covers["dey|edwidge danticat"]["source"] == "itunes"


def test_pub_year_itunes_fallback_still_checks_the_title(monkeypatch):
    monkeypatch.setattr(reading_gen.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        reading_gen.lists_gen, "_itunes_lookup",
        lambda *a, **k: {"track": "Study Guide: The Bad Guys: Episode 10",
                         "release_date": "2025-05-09T07:00:00Z",
                         "source": "itunes"})
    sess = _FakeOL([], [])
    year, _ = reading_gen.resolve_pub_year(
        _book(title="The Bad Guys Episode 10", author="Aaron Blabey",
              slug="bad-guys-10"), {}, {}, sess)
    assert year is None


def test_pub_year_cache_hit_makes_no_request():
    cache = {"a book|an author": {"year": 1999, "source": "manual"}}
    year, source = reading_gen.resolve_pub_year(_book(), cache, {}, None)
    assert (year, source) == (1999, "manual")


def test_pub_year_outage_is_not_cached_as_a_miss(monkeypatch):
    monkeypatch.setattr(reading_gen.time, "sleep", lambda *_: None)

    def _down(*a, **k):
        raise reading_gen.requests.ConnectionError("openlibrary is down")

    monkeypatch.setattr(reading_gen, "_ol_first_published", _down)
    monkeypatch.setattr(reading_gen, "_itunes_year", _down)
    cache = {}
    year, _ = reading_gen.resolve_pub_year(_book(), cache, {}, object())
    assert year is None
    assert cache == {}, "a source outage must never harden into a fact"


def test_sane_year_bounds():
    assert reading_gen._sane_year(1952) == 1952
    assert reading_gen._sane_year("2026") == 2026
    assert reading_gen._sane_year(0) is None
    assert reading_gen._sane_year(date.today().year + 5) is None
    assert reading_gen._sane_year(None) is None
    assert reading_gen._sane_year("not a year") is None
