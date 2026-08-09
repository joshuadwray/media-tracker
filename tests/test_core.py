"""Unit tests for the pure logic: matching, state dedupe, config parsing."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker.matching import normalize, text_contains_title, titles_match  # noqa: E402
from tracker.models import Observation, SourceResult, normalize_key  # noqa: E402
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


def test_search_query_drops_parentheticals():
    """cloudLibrary and OverDrive match the query as a phrase, so a series
    suffix returns zero results — while the title still has to match in
    full afterwards."""
    from tracker.matching import search_query

    assert search_query("A Trade of Blood (Ana and Din Mysteries)") == \
        "A Trade of Blood"
    assert search_query("The Substance (2024)") == "The Substance"
    assert search_query("Eradication") == "Eradication"
    # Never search for an empty string.
    assert search_query("(Untitled)") == "(Untitled)"
    assert titles_match("A Trade of Blood (Ana and Din Mysteries)",
                        "A Trade of Blood")


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


def _book_obs(source, medium, item="book:x", label="X"):
    fmt = {"ebook": "ebook", "audiobook": "audiobook", "print": "print book"}[medium]
    return Observation(source=source, item_key=item, item_label=label,
                       summary=f"{fmt} in {source} catalog",
                       event=f"{fmt} in catalog", medium=medium)


def _run_groups(state, observations, ok=None):
    """Drive engine._notify_groups the way run_check does."""
    from tracker.engine import CheckRun, _notify_groups

    run = CheckRun(results=[SourceResult(source=o.source, observations=[o])
                            for o in observations])
    for r in run.results:
        for obs in r.observations:
            if state.is_new(obs):
                run.new.append(obs)
                state.record(obs)
            else:
                state.touch(obs)
    sources = {o.source for o in observations} if ok is None else ok
    return _notify_groups(run, state, sources)


def test_notify_groups_one_push_per_medium(tmp_path):
    """A book carried by several libraries is announced once per medium —
    and a new library joining later doesn't re-announce it."""
    state = State(tmp_path / "state.json")

    groups = _run_groups(state, [
        _book_obs("denton-cl", "ebook"),
        _book_obs("lewisville-cl", "ebook"),
        _book_obs("denton-cl", "audiobook"),
    ])
    assert sorted(g.medium for g in groups) == ["audiobook", "ebook"]
    assert next(g for g in groups if g.medium == "ebook").sources == \
        ["denton-cl", "lewisville-cl"]

    # Same run again (state persisted): nothing new.
    state.save()
    state = State(tmp_path / "state.json")
    assert _run_groups(state, [
        _book_obs("denton-cl", "ebook"),
        _book_obs("lewisville-cl", "ebook"),
        _book_obs("denton-cl", "audiobook"),
    ]) == []

    # A third library turning up the same ebook is not news.
    assert _run_groups(state, [_book_obs("fortworth", "ebook")]) == []
    # A medium nobody had yet is.
    assert [g.medium for g in _run_groups(state, [_book_obs("fortworth", "print")])] \
        == ["print"]


def test_notify_groups_keep_per_observation_for_movies(tmp_path):
    """Showtimes have no medium, so they keep firing per sighting/date."""
    state = State(tmp_path / "state.json")
    showtime = Observation(source="amc", item_key="movie:dune", item_label="Dune",
                           summary='"Dune" playing at AMC Northpark 15')
    groups = _run_groups(state, [showtime])
    assert len(groups) == 1 and groups[0].medium is None
    assert _run_groups(state, [showtime]) == []


def test_media_map_seeded_from_legacy_state(tmp_path):
    """Upgrading an existing state file must not re-push everything that is
    already sitting in a catalog."""
    import json

    p = tmp_path / "state.json"
    p.write_text(json.dumps({"meta": {}, "seen": {
        # both spellings the event string has had over time
        "denton-library|book:x|BK in catalog":
            {"first": "2026-07-01T00:00:00+00:00", "last": "2026-08-08T00:00:00+00:00"},
        "cloudlibrary|book:x|ebook in catalog":
            {"first": "2026-07-05T00:00:00+00:00", "last": "2026-08-08T00:00:00+00:00"},
        "amc|movie:dune|playing at AMC":
            {"first": "2026-08-01T00:00:00+00:00", "last": "2026-08-08T00:00:00+00:00"},
    }}))
    state = State(p)
    assert sorted(state.media) == ["book:x|ebook", "book:x|print"]
    assert not state.media_is_new("book:x|print")
    # Movies aren't seeded — they never grouped by medium.
    assert not any(k.startswith("movie:") for k in state.media)


def test_forget_item_clears_both_maps(tmp_path):
    state = State(tmp_path / "state.json")
    _run_groups(state, [_book_obs("denton-cl", "ebook")])
    assert state.media and state.seen
    assert state.forget_item("book:x") == 2
    assert not state.media and not state.seen


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
