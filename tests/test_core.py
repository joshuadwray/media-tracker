"""Unit tests for the pure logic: matching, state dedupe, config parsing."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker.matching import normalize, text_contains_title, titles_match  # noqa: E402
from tracker.models import Observation, normalize_key  # noqa: E402
from tracker.state import State  # noqa: E402


def test_normalize_strips_articles_and_noise():
    assert normalize("The Substance") == "substance"
    assert normalize("SUBSTANCE, THE (35mm)") == "substance"
    assert normalize("Amélie") == "amelie"
    assert normalize("The Nickel Boys: A Novel") == "nickel boys a novel"


def test_titles_match_fuzzy():
    assert titles_match("Nickel Boys", "The Nickel Boys")
    assert titles_match("The Substance", "SUBSTANCE, THE - 35MM (Q&A)")
    assert titles_match("Eephus", "Eephus (2024)")
    assert not titles_match("Heat", "Heat 2")
    assert not titles_match("The Substance", "The Subtle Art of Not")


def test_text_contains_title_word_boundaries():
    page = "NOW PLAYING: The Substance — Fri 7:30pm | Coming soon: Eephus"
    assert text_contains_title(page, "The Substance")
    assert text_contains_title(page, "eephus")
    assert not text_contains_title(page, "Substance Abuse")


def _obs(summary="ebook available"):
    return Observation(source="s1", item_key="book:x", item_label="X",
                       summary=summary)


def test_state_notifies_once(tmp_path):
    state = State(tmp_path / "state.json")
    obs = _obs()
    assert state.is_new(obs)
    state.record(obs)
    assert not state.is_new(obs)
    state.save()

    # Reload from disk: still deduped.
    state2 = State(tmp_path / "state.json")
    assert not state2.is_new(obs)
    # A different sighting of the same item is new again.
    assert state2.is_new(_obs("audiobook available"))


def test_state_survives_corrupt_file(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json")
    state = State(p)
    assert state.is_new(_obs())


def test_normalize_key():
    assert normalize_key("The Substance (2024)!") == "the-substance-2024"


def test_config_parses_sample_watchlist(tmp_path):
    from tracker.config import load_config
    wl = tmp_path / "watchlist.yaml"
    wl.write_text(
        "books:\n"
        "  - title: Nickel Boys\n"
        "    isbn: 9780385537070\n"
        "movies:\n"
        "  - title: The Substance\n"
        "    year: 2024\n"
        "sources:\n"
        "  lib:\n"
        "    kind: bibliocommons\n"
        "    library: denton\n"
        "  off-src:\n"
        "    kind: pages\n"
        "    enabled: false\n"
    )
    cfg = load_config(wl)
    assert cfg.books[0].isbn == "9780385537070"
    assert cfg.movies[0].year == 2024
    assert list(cfg.enabled_sources()) == ["lib"]


def test_cli_add_appends_yaml(tmp_path):
    from tracker.watchlist_io import append_entry
    wl = tmp_path / "watchlist.yaml"
    wl.write_text("# header\nbooks:\n\nmovies:\n  - title: Old\nsources: {}\n")
    append_entry(wl, "books", {"title": "New: Book", "isbn": "123"})
    append_entry(wl, "movies", {"title": "New Movie", "year": 2026})
    text = wl.read_text()
    assert '  - title: "New: Book"\n    isbn: 123\n' in text
    assert "  - title: New Movie\n    year: 2026\n" in text
    assert "  - title: Old" in text

    from tracker.config import load_config
    cfg = load_config(wl)
    assert [b.title for b in cfg.books] == ["New: Book"]
    assert [m.title for m in cfg.movies] == ["New Movie", "Old"]
