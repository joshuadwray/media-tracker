"""Director extraction and caching for the Letterboxd sync."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker import letterboxd_sync as lb  # noqa: E402

# Letterboxd wraps its JSON-LD in /* */ comments — the reason LD_RE exists.
PAGE = '''<html><body data-tmdb-id="12345">
<script type="application/ld+json">
/* <![CDATA[ */
{"@type":"Movie","name":"No Country for Old Men","image":"https://p/600x900.jpg",
 "director":[{"@type":"Person","name":"Joel Coen"},
             {"@type":"Person","name":"Ethan Coen"}]}
/* ]]> */
</script></body></html>'''

NO_DIRECTOR = ('<html><script type="application/ld+json">'
               '{"@type":"Movie","image":"https://p/x.jpg"}</script></html>')


class _Sess:
    def __init__(self, *pages):
        self.pages = list(pages)
        self.urls = []

    def _resp(self, text):
        class R:
            def __init__(self, t):
                self.text = t

            def raise_for_status(self):
                pass
        return R(text)


def _patch_get(monkeypatch, *pages, fail_on=()):
    seen = []

    def fake_get(sess, url, **kw):
        seen.append(url)
        if any(f in url for f in fail_on):
            raise RuntimeError("film page 500")
        page = pages[min(len(seen) - 1, len(pages) - 1)]

        class R:
            text = page

            def raise_for_status(self):
                pass
        return R()

    monkeypatch.setattr(lb.http, "get", fake_get)
    return seen


def test_film_details_pulls_all_three_from_one_request(monkeypatch):
    seen = _patch_get(monkeypatch, PAGE)
    out = lb.film_details(object(), "no-country-for-old-men")
    assert out["tmdb_id"] == 12345
    assert out["poster"] == "https://p/600x900.jpg"
    assert out["director"] == ["Joel Coen", "Ethan Coen"]
    assert len(seen) == 1, "poster, tmdb_id and director share one fetch"


def test_film_details_survives_a_page_without_a_director(monkeypatch):
    _patch_get(monkeypatch, NO_DIRECTOR)
    out = lb.film_details(object(), "x")
    assert out["director"] == [] and out["tmdb_id"] is None


def test_fill_directors_skips_cached_and_dedups_rewatches(monkeypatch):
    seen = _patch_get(monkeypatch, PAGE)
    films = [{"slug": "a"}, {"slug": "a"}, {"slug": "b"}]   # a watched twice
    cache = {"b": {"director": ["Someone"]}}
    fetched = lb.fill_directors(films, cache, sess=object())
    assert fetched == 1 and len(seen) == 1, "only the uncached slug is fetched"
    assert cache["a"]["director"] == ["Joel Coen", "Ethan Coen"]


def test_fill_directors_is_a_noop_when_everything_is_cached(monkeypatch):
    seen = _patch_get(monkeypatch, PAGE)
    cache = {"a": {"director": []}}
    assert lb.fill_directors([{"slug": "a"}], cache, sess=object()) == 0
    assert seen == []


def test_a_failed_fetch_is_left_uncached(monkeypatch):
    """Same contract as resolve_pub_year: a bad network day must never
    harden into "this film has no director"."""
    _patch_get(monkeypatch, PAGE, fail_on=("boom",))
    cache = {}
    assert lb.fill_directors([{"slug": "boom"}], cache, sess=object()) == 0
    assert cache == {}


def test_budget_defers_the_rest_to_the_next_run(monkeypatch):
    seen = _patch_get(monkeypatch, PAGE)
    films = [{"slug": s} for s in ("a", "b", "c")]
    cache = {}
    assert lb.fill_directors(films, cache, sess=object(), budget=2) == 2
    assert len(seen) == 2 and set(cache) == {"a", "b"}


def test_director_cache_serialises_stably():
    cache = {"b": {"director": ["Z"]}, "a": {"director": ["Wong Kar-Wai"]}}
    text = lb.dump_directors(cache)
    assert text.index('"a"') < text.index('"b"'), "sorted: no diff churn"
    assert text.endswith("\n") and "Wong Kar-Wai" in text
