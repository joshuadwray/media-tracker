"""Tests for the reading-log generator: deltas, slugs, ISBN bridge."""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker.reading_gen import (  # noqa: E402
    Book, ReadingLog, daily_pages, dump_log, isbn_from_cover_url, load_log,
    pages_by_date, slugify,
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
