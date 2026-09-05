"""Tests for the lists renderer: YAML parse, cover cache, overrides."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker.lists_gen import (  # noqa: E402
    COVER_URL, ListItem, load_cache, lists_bundle, parse_list, resolve_cover,
    save_cache,
)

YAML = """\
title: Best Books of 2025
kind: books
ranked: true
items:
  - title: The Antidote
    author: Karen Russell
    cover: https://example.com/antidote.jpg
  - title: We Do Not Part
    author: Han Kang
  - title: Mystery Book
"""


def _write_list(tmp_path):
    p = tmp_path / "best-books-2025.yaml"
    p.write_text(YAML, encoding="utf-8")
    return p


def test_parse_list(tmp_path):
    bl = parse_list(_write_list(tmp_path))
    assert bl.title == "Best Books of 2025"
    assert bl.stem == "best-books-2025"
    assert bl.ranked is True
    assert len(bl.items) == 3
    assert bl.items[0].cover == "https://example.com/antidote.jpg"
    assert bl.items[1].author == "Han Kang"
    assert bl.items[2].author == ""


class _NoNetwork:
    def get(self, *a, **k):
        raise AssertionError("network hit despite cache")


def test_resolve_cover_manual_override_wins():
    item = ListItem("The Antidote", "Karen Russell",
                    cover="https://example.com/x.jpg")
    # cache has a different answer; the manual override must win
    cache = {item.cache_key: {"cover_id": 999}}
    assert resolve_cover(item, cache, _NoNetwork()) == "https://example.com/x.jpg"


def test_resolve_cover_cache_hit_no_network():
    item = ListItem("We Do Not Part", "Han Kang")
    cache = {item.cache_key: {"cover_url": "https://example.com/wdnp.jpg"}}
    assert resolve_cover(item, cache, _NoNetwork()) == "https://example.com/wdnp.jpg"


def test_resolve_cover_old_style_cache_entry_still_works():
    item = ListItem("We Do Not Part", "Han Kang")
    cache = {item.cache_key: {"cover_id": 14835467}}
    assert resolve_cover(item, cache, _NoNetwork()) == COVER_URL.format(14835467)


def test_resolve_cover_cached_miss_and_no_session():
    item = ListItem("Mystery Book")
    assert resolve_cover(item, {item.cache_key: {"cover_id": None}},
                         _NoNetwork()) is None
    assert resolve_cover(item, {}, session=None) is None  # uncached, no fetch


def test_cache_roundtrip(tmp_path):
    path = tmp_path / "covers-cache.json"
    cache = {"a|b": {"cover_id": 1, "matched": "A — B (2025)"}}
    save_cache(cache, path)
    assert load_cache(path) == cache


def test_lists_bundle_carries_tile_data(tmp_path):
    """The grid is drawn by docs/assets/diary.js now, so the contract this
    guards is the DATA the bundle hands it — covers resolved server-side
    (the browser can't reach iTunes/OL), and a hue for every item so a
    coverless one still gets a typographic tile rather than a gap."""
    bl = parse_list(_write_list(tmp_path))
    covers = ["https://example.com/antidote.jpg",
              COVER_URL.format(14835467), None]
    bundle = lists_bundle([bl], {bl.stem: covers})
    (entry,) = bundle["lists"]
    assert entry["stem"] == bl.stem and entry["ranked"] is True
    items = entry["items"]
    assert len(items) == 3
    assert items[0]["cover"] == "https://example.com/antidote.jpg"
    assert items[2]["cover"] is None          # -> typographic tile
    assert all(0 <= i["hue"] < 360 for i in items)
    assert "Mystery Book" in [i["title"] for i in items]


def test_lists_bundle_links_read_books(tmp_path):
    """Tiles for books in the reading log link to their diary page and
    carry the rating badge; unread ones carry neither."""
    bl = parse_list(_write_list(tmp_path))
    links = {bl.items[0].cache_key:
             {"href": "../reading/antidote.html", "rating": 4.5}}
    items = lists_bundle([bl], {bl.stem: [None, None, None]},
                         links)["lists"][0]["items"]
    assert items[0]["href"] == "../reading/antidote.html"
    assert items[0]["rating"] == 4.5
    assert items[1]["href"] is None and items[1]["rating"] is None


def test_author_guard_folds_diacritics():
    """The guard rejects a cover credited to a *different* author. Unfolded,
    it also rejected the same one spelled differently: a log typed
    "perez-carbonell" never matched Apple's "Pérez-Carbonell", and the book
    rendered as a blank tile with nothing logged anywhere."""
    from tracker.lists_gen import _author_ok

    assert _author_ok("marta perez-carbonell", "Marta Pérez-Carbonell")
    assert _author_ok("Marta Pérez-Carbonell", "marta perez-carbonell")
    assert _author_ok("Téa Obreht", "Tea Obreht")
    # Still a guard: an accent was never what separated two people.
    assert not _author_ok("marta perez-carbonell", "Rosa Montero")
    assert _author_ok("", "anyone")           # no author to check against


def test_author_guard_folds_punctuation():
    """Phone keyboards type a curly apostrophe. "Maggie O’Farrell" in the
    log never matched Apple's "Maggie O'Farrell", so the iTunes hit was
    dropped, no ISBN reached the Apple Books page-count hop, and Land came
    back with no count and no cover — silently, from both the browser and
    the CI rebuild."""
    from tracker.lists_gen import _author_ok

    assert _author_ok("Maggie O’Farrell", "Maggie O'Farrell")
    assert _author_ok("Maggie O'Farrell", "Maggie O’Farrell")
    assert _author_ok("Roisin O’Donnell", "Roisín O'Donnell")
    # Catalogs drop the apostrophe entirely, or space it out.
    assert _author_ok("Joseph O'Neill", "Joseph O Neill")
    assert _author_ok("Joseph O'Neill", "Joseph ONeill")
    # Still a guard: punctuation was never what separated two people.
    assert not _author_ok("Maggie O’Farrell", "Joseph O'Neill")


def test_title_guard_folds_diacritics():
    """The title half of the match had the same flaw as the author half: a
    log typed "dey" never matched Apple's "Dèy", so the correct hit — first
    in the result list — was rejected along with everything else, and the
    book got no page count and no cover."""
    from tracker.lists_gen import _squash

    def title_ok(wanted, track):
        w = _squash(wanted)
        return bool(w) and w in _squash(track)

    assert title_ok("dey", "Dèy")
    assert title_ok("Dèy", "dey")
    assert title_ok("swamplandia", "Swamplandia!")
    assert title_ok("Land", "Land")
    # Still a guard: a different book is still a different book.
    assert not title_ok("dey", "Daughter")
    assert not title_ok("Land", "Hamnet")
