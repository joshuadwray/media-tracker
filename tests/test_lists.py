"""Tests for the lists renderer: YAML parse, cover cache, overrides."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker.lists_gen import (  # noqa: E402
    COVER_URL, ListItem, load_cache, parse_list, render_list, resolve_cover,
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


def test_render_list_tiles(tmp_path):
    bl = parse_list(_write_list(tmp_path))
    html = render_list(bl, ["https://example.com/antidote.jpg",
                            COVER_URL.format(14835467), None])
    assert html.count("<li class='tile'>") == 3
    assert "<span class='rank'>1</span>" in html
    assert "https://example.com/antidote.jpg" in html
    assert "noimg" in html  # coverless item gets a typographic tile
    assert "Mystery Book" in html
