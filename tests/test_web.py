"""Tests for watchlist editing, the dashboard renderer, and the web API.

No network: source checks aren't exercised here (sources are covered by
`probe` against live sites); these tests cover the machinery around them.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from tracker.watchlist_io import append_entry, remove_entry  # noqa: E402

SAMPLE = """\
# my watchlist
books:
  - title: "Nickel Boys: A Novel"
    author: Colson Whitehead
    isbn: 123
  - title: Plain Book

movies:
  - title: The Substance
    year: 2024

sources:
  texas-theatre:
    kind: pages
    pages: [{name: Texas Theatre, url: "https://example.com/"}]
state_file: state/state.json
"""


@pytest.fixture
def wl(tmp_path):
    p = tmp_path / "watchlist.yaml"
    p.write_text(SAMPLE)
    return p


def test_remove_entry_with_attributes(wl):
    assert remove_entry(wl, "books", "Nickel Boys: A Novel")
    text = wl.read_text()
    assert "Nickel Boys" not in text
    assert "Colson Whitehead" not in text
    assert "Plain Book" in text          # sibling survives
    assert "# my watchlist" in text      # comments survive
    assert "The Substance" in text       # other section untouched


def test_remove_entry_not_found(wl):
    assert not remove_entry(wl, "books", "Nonexistent")
    assert not remove_entry(wl, "movies", "Plain Book")  # wrong section
    assert wl.read_text() == SAMPLE


def test_remove_entry_normalized_fallback(wl):
    """A caller holding a stale/differently-cased spelling still resolves —
    the dashboard caches titles in a published page."""
    assert remove_entry(wl, "books", "nickel boys a novel")
    assert "Nickel Boys" not in wl.read_text()
    assert "Plain Book" in wl.read_text()


def test_cmd_remove_cleans_state(wl, tmp_path, monkeypatch):
    """`tracker remove` drops the entry AND the state keyed to it, so a
    re-add inside the 180-day prune window still notifies."""
    import json

    from tracker.cli import main

    # cli.main loads the repo's .env, so without this the test would send a
    # real push to the phone.
    monkeypatch.setenv("NTFY_TOPIC", "")

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "state.json").write_text(json.dumps({"meta": {}, "seen": {
        "denton-library|book:plain-book|print book in catalog":
            {"first": "2026-01-01T00:00:00+00:00", "last": "2026-01-02T00:00:00+00:00"},
        "cloudlibrary|book:plain-book|ebook in catalog":
            {"first": "2026-01-01T00:00:00+00:00", "last": "2026-01-02T00:00:00+00:00"},
        "denton-library|book:nickel-boys-a-novel|print book in catalog":
            {"first": "2026-01-01T00:00:00+00:00", "last": "2026-01-02T00:00:00+00:00"},
    }}))
    (state_dir / "pending-pins.json").write_text(json.dumps({"pending": [
        {"id": "plain-book-20260101T000000Z", "kind": "book",
         "typed_title": "Plain Book", "candidates": []},
    ]}))

    assert main(["--watchlist", str(wl), "remove", "book", "Plain Book"]) == 0

    assert "Plain Book" not in wl.read_text()
    seen = json.loads((state_dir / "state.json").read_text())["seen"]
    assert list(seen) == ["denton-library|book:nickel-boys-a-novel|print book in catalog"]
    assert json.loads((state_dir / "pending-pins.json").read_text())["pending"] == []

    # Idempotent: a double-tap from the phone is not an error.
    assert main(["--watchlist", str(wl), "remove", "book", "Plain Book"]) == 0


def test_append_then_remove_round_trip(wl):
    append_entry(wl, "movies", {"title": "Eephus", "year": 2024})
    assert remove_entry(wl, "movies", "Eephus")
    from tracker.config import load_config
    cfg = load_config(wl)
    assert [m.title for m in cfg.movies] == ["The Substance"]


def test_dashboard_renders(wl, tmp_path):
    from tracker.config import load_config
    from tracker.dashboard import build_dashboard
    from tracker.models import Observation, SourceResult
    from tracker.state import State

    cfg = load_config(wl)
    obs = Observation(source="texas-theatre", item_key="movie:the-substance",
                      item_label="The Substance (2024)",
                      summary='"The Substance" mentioned on Texas Theatre',
                      url="https://example.com/")
    results = [SourceResult(source="texas-theatre", observations=[obs]),
               SourceResult(source="broken", error="RuntimeError: boom\n  tb")]
    html = build_dashboard(cfg, results, [obs], State(tmp_path / "s.json"))
    assert "The Substance" in html
    assert "boom" in html
    assert "Plain Book" in html  # never-matched warning includes it
    # The remove button is the only script on the dashboard; everything else
    # is server-rendered. (This assertion used to be "no <script> at all",
    # which went stale when the 🗑 button landed.)
    assert html.count("<script") == 1


@pytest.fixture
def client(wl, monkeypatch):
    flask = pytest.importorskip("flask")  # noqa: F841
    from tracker.web import create_app
    app = create_app(str(wl))
    app.config["TESTING"] = True
    return app.test_client()


def test_api_watchlist_roundtrip(client):
    wl_data = client.get("/api/watchlist").get_json()
    assert [b["title"] for b in wl_data["books"]] == \
        ["Nickel Boys: A Novel", "Plain Book"]
    assert wl_data["sources"][0]["id"] == "texas-theatre"

    r = client.post("/api/watchlist/movies", json={"title": "Eephus", "year": 2024})
    assert r.status_code == 200
    assert {"title": "Eephus", "year": 2024} == r.get_json()["added"]

    r = client.post("/api/watchlist/movies", json={"title": "Eephus"})
    assert r.status_code == 409  # duplicate rejected

    r = client.delete("/api/watchlist/movies", json={"title": "Eephus"})
    assert r.status_code == 200
    r = client.delete("/api/watchlist/movies", json={"title": "Eephus"})
    assert r.status_code == 404


def test_api_add_book_from_candidate(client):
    candidate = {"source": "denton-library", "title": "The Actual Title",
                 "author": "A. Author", "bib_id": "S99"}
    r = client.post("/api/watchlist/books",
                    json={"title": "actual title", "candidate": candidate})
    assert r.status_code == 200
    added = r.get_json()["added"]
    assert added == {"title": "The Actual Title", "author": "A. Author",
                     "bib_id": "S99"}
    books = client.get("/api/watchlist").get_json()["books"]
    assert any(b["bib_id"] == "S99" for b in books)


def test_api_validation(client):
    assert client.post("/api/watchlist/movies", json={}).status_code == 400
    assert client.post("/api/watchlist/junk",
                       json={"title": "x"}).status_code == 400
    assert client.get("/api/search/books").status_code == 400
    assert client.get("/api/probe/nope").status_code == 404


def test_api_report_empty(client):
    assert client.get("/api/report").get_json()["report"] is None


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"media tracker" in r.data
