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


_LABELS = {"denton-cl": "Denton CL", "lewisville-cl": "Lewisville",
           "fortworth": "Fort Worth"}


def _book_obs(source, medium, item="book:x", label="X", wait=None,
              distance_mi=0.0, loan_days=14):
    fmt = {"ebook": "ebook", "audiobook": "audiobook", "print": "print book",
           "audiobook-cd": "audiobook (CD)"}[medium]
    return Observation(source=source, item_key=item, item_label=label,
                       summary=f"{fmt} in {source} catalog",
                       event=f"{fmt} in catalog", medium=medium, wait=wait,
                       distance_mi=distance_mi, loan_days=loan_days,
                       source_label=_LABELS.get(source, source))


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


def test_notify_groups_one_push_per_track(tmp_path):
    """Once a book is known, each track is announced once — however many
    libraries carry it, in whatever format. Print and ebook are the same
    decision, so the ebook is silent once print has been announced."""
    state = State(tmp_path / "state.json")
    # Past its debut (that path is test_first_discovery_is_one_push).
    _run_groups(state, [_book_obs("denton-cl", "print")])

    groups = _run_groups(state, [
        _book_obs("denton-cl", "ebook"),
        _book_obs("lewisville-cl", "ebook"),
        _book_obs("denton-cl", "audiobook"),
    ])
    # print already covered "reading"; only listening is news.
    assert [g.track for g in groups] == ["listening"]

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
    # Nor is audio on CD — same track as the audiobook we already have.
    assert _run_groups(state, [_book_obs("fortworth", "audiobook-cd")]) == []


def test_first_discovery_is_one_push(tmp_path):
    """A book nobody has seen before is announced once, listing every track
    — not once per track. Later tracks ping on their own."""
    from tracker.notify import body

    state = State(tmp_path / "state.json")
    groups = _run_groups(state, [
        _book_obs("denton-cl", "print", wait=0),
        _book_obs("lewisville-cl", "ebook", wait=30),
        _book_obs("fortworth", "audiobook", wait=30),
    ])
    assert len(groups) == 1
    debut = groups[0]
    assert debut.track is None
    text = body(debut)
    # Best option per track, named the way a person would name the library.
    assert "reading: now at Denton CL (print)" in text
    assert "listening: ~4 wk at Fort Worth (audiobook)" in text
    # The ebook loses to the on-shelf print copy and doesn't clutter the push.
    assert "ebook" not in text

    # Each track was still recorded, so nothing re-announces.
    assert _run_groups(state, [_book_obs("denton-cl", "print", wait=0)]) == []


def test_notify_groups_keep_per_observation_for_movies(tmp_path):
    """Showtimes have no track, so they keep firing per sighting/date."""
    state = State(tmp_path / "state.json")
    showtime = Observation(source="amc", item_key="movie:dune", item_label="Dune",
                           summary='"Dune" playing at AMC Northpark 15')
    groups = _run_groups(state, [showtime])
    assert len(groups) == 1 and groups[0].track is None
    assert _run_groups(state, [showtime]) == []


def test_media_map_seeded_from_legacy_state(tmp_path):
    """Upgrading an existing state file must not re-push everything that is
    already sitting in a catalog."""
    import json
    from datetime import datetime, timedelta, timezone

    # Relative to now: a fixed date would silently age past GAP_DAYS and
    # start asserting the opposite of what it means.
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=30)).isoformat(timespec="seconds")
    recent = (now - timedelta(hours=6)).isoformat(timespec="seconds")

    p = tmp_path / "state.json"
    p.write_text(json.dumps({"meta": {}, "seen": {
        # both spellings the event string has had over time
        "denton-library|book:x|BK in catalog": {"first": old, "last": recent},
        "cloudlibrary|book:x|ebook in catalog": {"first": old, "last": recent},
        "amc|movie:dune|playing at AMC": {"first": old, "last": recent},
    }}))
    state = State(p)
    # Both formats are things you read, so they seed one "reading" track.
    assert sorted(state.media) == ["book:x|reading"]
    assert not state.media_is_new("book:x|reading")
    # Movies aren't seeded — they never grouped by medium.
    assert not any(k.startswith("movie:") for k in state.media)


def test_forget_item_clears_both_maps(tmp_path):
    state = State(tmp_path / "state.json")
    _run_groups(state, [_book_obs("denton-cl", "ebook")])
    assert state.media and state.seen
    assert state.forget_item("book:x") == 2
    assert not state.media and not state.seen


def test_watching_stamps_are_kept_in_step_with_the_watchlist(tmp_path):
    from datetime import datetime, timedelta, timezone

    state = State(tmp_path / "state.json")
    long_ago = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state.note_watching(["book:x", "movie:y"], now=long_ago)
    # A second run doesn't reset the clock on an item already being watched.
    state.note_watching(["book:x", "movie:y"], now=long_ago + timedelta(days=30))
    assert state.waiting_days("book:x", now=long_ago + timedelta(days=30)) == 30
    assert state.waiting_days("book:never-added") is None

    # Fixing an entry from the phone is a remove plus an add. The stamp has
    # to ride that out, or every metadata edit resets the wait to zero.
    state.forget_item("book:x")
    state.note_watching(["movie:y"], now=long_ago + timedelta(days=31))
    state.note_watching(["book:x", "movie:y"], now=long_ago + timedelta(days=31))
    assert state.waiting_days("book:x", now=long_ago + timedelta(days=31)) == 31

    # But a title dropped long enough ago is forgotten, and comes back new.
    state.note_watching(["movie:y"], now=long_ago + timedelta(days=400))
    assert "book:x" not in state.watching


def test_still_looking_ranks_by_wait_and_flags_only_the_old(tmp_path):
    """The section is a status, not an error list: a title no library has
    bought yet is indistinguishable from a typo except by how long it's
    been sitting there."""
    from datetime import datetime, timedelta, timezone

    from tracker.config import load_config
    from tracker.report import SPELLING_HINT_DAYS, still_looking

    wl = tmp_path / "watchlist.yaml"
    wl.write_text(
        "books:\n"
        "  - title: Fresh Release\n"
        "  - title: Teh Typpo\n"
        "  - title: Already Found\n"
        "sources: {}\n"
    )
    cfg = load_config(wl)
    now = datetime(2026, 8, 9, tzinfo=timezone.utc)
    state = State(tmp_path / "state.json")
    state.note_watching([b.key for b in cfg.books],
                        now=now - timedelta(days=10))
    state.watching["book:teh-typpo"] = (
        now - timedelta(days=SPELLING_HINT_DAYS + 1)).isoformat()
    # Seen once, months ago, and absent today: that's a stale card, not a
    # title we're still hunting for.
    state.seen["denton|book:already-found|print book in catalog"] = {
        "first": "2026-01-01T00:00:00+00:00", "last": "2026-01-02T00:00:00+00:00"}

    waiting = still_looking(cfg, [], state, now)
    assert [w.label for w in waiting] == ["Teh Typpo", "Fresh Release"]
    assert waiting[0].suspect and waiting[0].age == "91d"
    assert not waiting[1].suspect and waiting[1].age == "10d"


def test_state_survives_corrupt_file(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json")
    state = State(p)
    assert state.is_new(_obs())


def test_auto_pick_prefers_author_then_newest(monkeypatch, tmp_path):
    """The live failure this came from: bare "Sunrise" pinned Karen
    Kingsbury's 2007 novel over Téa Obreht's 2026 one, because the first
    title match with a bib_id won."""
    import argparse

    from tracker import cli

    catalog = [
        {"title": "Sea Otter Sunrise", "author": "Osborne, Mary Pope",
         "format": "BK", "bib_id": "S1", "year": 2025},
        {"title": "Sunrise", "author": "Kingsbury, Karen",
         "format": "BK", "bib_id": "S2", "year": 2007},
        {"title": "Sunrise", "author": "Obreht, Téa",
         "format": "BK", "bib_id": "S3", "year": 2026},
        {"title": "Sunrise", "author": "Obreht, Téa",
         "format": "EBOOK", "bib_id": "S4", "year": 2026},
    ]
    monkeypatch.setattr(cli, "search_book_candidates",
                        lambda *a, **k: list(catalog))

    def pick(author):
        args = argparse.Namespace(title="Sunrise", author=author, isbn=None)
        entry, _matches, picked = cli._auto_pick_book(None, args)
        return entry.get("bib_id"), picked.get("year")

    assert pick(None) == ("S3", 2026)            # newest wins a bare title
    assert pick("Obreht") == ("S3", 2026)        # print preferred over ebook
    assert pick("Kingsbury") == ("S2", 2007)     # an author still overrides
    # An author nobody matches shouldn't strand the add — fall back to title.
    assert pick("Nonexistent, Someone")[0] == "S3"


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


# --- wait model ------------------------------------------------------------

def test_wait_days_matches_probed_records():
    """Every (available, owned, holds, loan) tuple here came off a live
    catalog. The 14-day rows double as a check that we reproduce OverDrive's
    own arithmetic where it is meaningful, since estimatedWaitDays is exactly
    ceil((holds+1)/copies * 14)."""
    from tracker.availability import wait_days

    probed = [
        # available, owned, holds, loan_days, expected
        (0, 2, 56, 14, 399), (0, 1, 96, 14, 1358), (0, 1, 98, 14, 1386),
        (0, 2, 49, 14, 350), (0, 2, 55, 14, 392), (0, 1, 10, 14, 154),
        (0, 1, 4, 14, 70), (0, 1, 6, 14, 98), (0, 3, 4, 14, 24),
        (0, 3, 25, 14, 122), (0, 5, 65, 14, 185), (0, 1, 8, 14, 126),
        (0, 3, 13, 14, 66), (0, 1, 1, 14, 28), (0, 1, 0, 14, 14),
        # cloudLibrary, 21-day loans
        (0, 1, 3, 21, 84), (0, 1, 0, 21, 21), (0, 2, 2, 21, 32),
    ]
    for available, owned, holds, loan, expected in probed:
        assert wait_days(available, owned, holds, loan) == expected, \
            (available, owned, holds, loan)


def test_wait_days_zero_when_a_copy_is_free():
    """The distinction both vendors' own numbers throw away: OverDrive
    reports 14 days for a title sitting 3-of-3 on the shelf."""
    from tracker.availability import wait_days

    assert wait_days(3, 3, 0, 14) == 0
    assert wait_days(1, 5, 40, 14) == 0
    # No copies owned is not an infinite queue — it's a pre-release or a
    # marketplace record, and we say so rather than inventing a number.
    assert wait_days(0, 0, 5, 14) is None
    assert wait_days(0, None, None, 14) is None


def test_bucket_edges_scale_with_the_loan_period():
    from tracker.availability import UNKNOWN, bucket

    assert [bucket(w, 14) for w in (0, 1, 14, 15, 42, 43, 90, 91)] == [
        "now", "turn", "turn", "plannable", "plannable", "horizon",
        "horizon", "someday"]
    # "front of the queue" is one loan, so 21 days is still `turn` at a
    # 21-day library and already `plannable` at a 14-day one.
    assert bucket(21, 21) == "turn"
    assert bucket(21, 14) == "plannable"
    assert bucket(None, 14) == UNKNOWN


def test_ratchet_only_speaks_on_a_genuine_improvement(tmp_path):
    state = State(tmp_path / "state.json")
    key = "book:x|reading"
    state.media_record(key)

    # First known bucket is silent: the debut push already covered the book.
    assert not state.media_improves(key, "someday")
    state.media_set_best(key, "someday")

    # Wobbling inside a bucket says nothing; dropping one speaks.
    assert not state.media_improves(key, "someday")
    assert state.media_improves(key, "plannable")
    state.media_set_best(key, "plannable")

    # Going back on hold is not news, and must not re-arm the watermark.
    assert not state.media_improves(key, "someday")
    state.media_set_best(key, "someday")
    assert state.media_best(key) == "plannable"
    assert not state.media_improves(key, "plannable")
    assert state.media_improves(key, "now")

    # An unknown wait can neither claim an improvement nor erase one.
    assert not state.media_improves(key, "unknown")
    state.media_set_best(key, "unknown")
    assert state.media_best(key) == "plannable"


def test_shorter_wait_pushes_once(tmp_path):
    """The queue at one library collapsing is worth a push, and exactly one."""
    state = State(tmp_path / "state.json")
    _run_groups(state, [_book_obs("houston", "ebook", wait=400)])  # debut
    assert _run_groups(state, [_book_obs("houston", "ebook", wait=380)]) == []

    groups = _run_groups(state, [_book_obs("houston", "ebook", wait=30)])
    assert [(g.track, g.reason) for g in groups] == [("reading", "sooner")]
    # Same improved wait on the next run is old news.
    assert _run_groups(state, [_book_obs("houston", "ebook", wait=30)]) == []
    # And a *worse* wait never speaks.
    assert _run_groups(state, [_book_obs("houston", "ebook", wait=400)]) == []


def test_far_physical_copy_never_headlines(tmp_path):
    """Order-only proximity: a shelf copy 240 miles away is still reported,
    but it can't declare the track solved over a digital option."""
    from tracker.availability import MAX_DISTANCE_MI

    state = State(tmp_path / "state.json")
    near_ebook = _book_obs("houston-cl", "ebook", wait=400)
    far_print = _book_obs("houston-print", "print", wait=0,
                          distance_mi=MAX_DISTANCE_MI + 180)
    groups = _run_groups(state, [near_ebook, far_print])
    group = groups[0]
    assert group.headline is near_ebook
    assert group.best_bucket == "someday"
    # Still listed, just last.
    assert group.options[-1] is far_print


def test_sync_note_reflects_the_loan_period():
    from tracker.report import sync_note, tracks_for_item

    def note(reading_wait, listening_wait, loan):
        return sync_note(tracks_for_item([
            _book_obs("a", "ebook", wait=reading_wait, loan_days=loan),
            _book_obs("a", "audiobook", wait=listening_wait, loan_days=loan),
        ]))

    # You read a book in a day or three, so the holds only have to overlap.
    assert "fits in a 14d loan" in note(30, 40, 14)
    assert "fits in a 21d loan" in note(30, 44, 21)
    # A gap wider than the loan is the case worth acting on.
    assert "suspend the reading hold ~60d" in note(30, 90, 14)
    assert sync_note(tracks_for_item([_book_obs("a", "ebook", wait=30)])) is None


def test_track_seed_folds_retired_medium_keys(tmp_path):
    """cloudLibrary's old ebook-or-audiobook sentinel still has to land
    somewhere, or the next run treats the book as never seen."""
    import json
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=30)).isoformat(timespec="seconds")
    recent = (now - timedelta(hours=6)).isoformat(timespec="seconds")
    older = (now - timedelta(days=60)).isoformat(timespec="seconds")

    p = tmp_path / "state.json"
    p.write_text(json.dumps({"meta": {}, "seen": {}, "media": {
        "book:x|ebook-or-audiobook": {"first": old, "last": recent},
        "book:x|print": {"first": older, "last": old},
        "book:x|audiobook": {"first": old, "last": recent},
    }}))
    state = State(p)
    assert sorted(state.media) == ["book:x|listening", "book:x|reading"]
    # Widest window wins, so nothing looks newly absent.
    assert state.media["book:x|reading"]["first"] == older
    assert state.media["book:x|reading"]["last"] == recent
    assert not state.media_is_new("book:x|reading")
    # Idempotent: a second load changes nothing.
    state.save()
    assert sorted(State(p).media) == ["book:x|listening", "book:x|reading"]


# --- on-order titles -------------------------------------------------------

def test_wait_after_arrival_counts_only_the_queue():
    """The first `copies` holds are filled the day the box lands."""
    from tracker.availability import wait_after_arrival

    assert wait_after_arrival(0, 1, 21) == 0    # you'd be next
    assert wait_after_arrival(1, 1, 21) == 21   # one ahead of you
    assert wait_after_arrival(4, 1, 21) == 84   # Fruit Fly at Denton
    assert wait_after_arrival(4, 2, 21) == 42
    assert wait_after_arrival(3, None, 21) is None


def test_on_order_is_never_now_and_never_ratchets(tmp_path):
    """We know the queue but not the arrival date, so an on-order title can
    rank but must not claim a duration or move a watermark."""
    on_order = _book_obs("denton", "print", wait=0, loan_days=21)
    on_order.provisional = True

    # A zero queue is zero turns *after arrival*, not "on the shelf".
    assert on_order.bucket == "turn"
    assert on_order.wait_text == "on order"

    state = State(tmp_path / "state.json")
    groups = _run_groups(state, [on_order])
    assert len(groups) == 1
    # It can headline the card...
    assert groups[0].headline is on_order
    # ...but contributes no watermark, so nothing can "improve" on it later.
    assert groups[0].best_bucket is None
    assert state.media_best("book:x|reading") is None


def test_firm_option_sets_the_watermark_over_a_provisional_headline(tmp_path):
    """A guess can outrank a known wait for display without polluting state."""
    state = State(tmp_path / "state.json")
    on_order = _book_obs("denton", "print", wait=21, loan_days=21)
    on_order.provisional = True
    firm = _book_obs("houston", "ebook", wait=200, loan_days=14)

    groups = _run_groups(state, [on_order, firm])
    g = groups[0]
    assert g.headline is on_order          # sorts first: 21d beats 200d
    assert g.best_bucket == "someday"      # but the watermark is the firm one


def test_true_copies_halves_only_on_order():
    """Every ordered copy is listed twice on an ON_ORDER bib (placeholder +
    real item), so totalCopies double-counts. Verified against the gateway
    availability API on 2026-08-10."""
    from tracker.sources.bibliocommons import _true_copies

    assert _true_copies({"totalCopies": 2}, "ON_ORDER") == 1
    assert _true_copies({"totalCopies": 12}, "ON_ORDER") == 6
    assert _true_copies({"totalCopies": 3}, "UNAVAILABLE") == 3
    assert _true_copies({"totalCopies": 1}, "ON_ORDER") == 1   # never zero
    assert _true_copies({}, "ON_ORDER") is None


def test_shelf_state_wording():
    from tracker.sources.bibliocommons import _shelf_state

    assert _shelf_state(2, 3, 0, "AVAILABLE") == "2 on shelf"
    assert _shelf_state(0, 1, 0, "ON_ORDER") == "1 on order, no holds yet"
    assert _shelf_state(0, 1, 4, "ON_ORDER") == "1 on order, 4 holds ahead"
    assert _shelf_state(0, 3, 0, "UNAVAILABLE") == "all 3 out"
    assert _shelf_state(0, 1, 2, "UNAVAILABLE") == "checked out, 2 holds"


def test_sooner_beats_nearer_within_a_bucket():
    """Proximity is the last tiebreak: a copy two weeks sooner should win
    over one down the road, even though digital counts as distance 0."""
    print_7d = _book_obs("denton", "print", wait=7, distance_mi=3, loan_days=21)
    ebook_21d = _book_obs("lewisville", "ebook", wait=21, loan_days=21)
    assert print_7d.bucket == ebook_21d.bucket == "turn"
    assert sorted([ebook_21d, print_7d], key=lambda o: o.sort_key)[0] is print_7d


def test_sync_note_stays_quiet_when_a_side_is_on_order():
    """The gap is arithmetic on a guess if one clock hasn't started."""
    from tracker.report import sync_note, tracks_for_item

    reading = _book_obs("denton", "print", wait=84, loan_days=21)
    reading.provisional = True
    listening = _book_obs("denton-cl", "audiobook", wait=84, loan_days=21)
    assert sync_note(tracks_for_item([reading, listening])) is None

    reading.provisional = False
    assert "gap 0d" in sync_note(tracks_for_item([reading, listening]))
