"""Tests for the book-finished detection: the newly-finished diff, the
"already has a report" filter that replaced the relay's state file, the
cap that keeps the recovery path bounded, and the job-matrix output."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker import book_finished as bf  # noqa: E402


def _book(title="Hum", author="Helen Phillips", slug="hum", status="finished",
          finished="2026-01-01", **kw):
    b = {"title": title, "author": author, "slug": slug, "status": status,
         "finished": finished, "sessions": []}
    b.update(kw)
    return b


def _log(*books):
    return {"settings": {}, "books": list(books)}


def _reports(tmp_path, *slugs, empty=()):
    d = tmp_path / "reports"
    d.mkdir(exist_ok=True)
    for slug in slugs:
        (d / f"{slug}.md").write_text("# report\n")
    for slug in empty:
        (d / f"{slug}.md").write_text("")
    return d


# ------------------------------------------------------- report filter

def test_pending_drops_books_that_already_have_a_report(tmp_path):
    """The committed file is the dedupe key — this is what replaced
    state/hermes-sent.json, and what makes a re-run idempotent."""
    new = _log(_book(slug="hum"), _book(slug="flesh", finished="2026-02-01"))
    reports = _reports(tmp_path, "hum")
    to_report, deferred = bf.pending({}, new, reports_dir=reports,
                                     pubyear_cache={})
    assert [e["slug"] for e in to_report] == ["flesh"]
    assert deferred == []


def test_empty_report_file_counts_as_absent(tmp_path):
    """A run that died mid-Write leaves a 0-byte file; that must not
    permanently block the book."""
    reports = _reports(tmp_path, empty=("hum",))
    assert bf.has_report("hum", reports) is False
    to_report, _ = bf.pending({}, _log(_book(slug="hum")),
                              reports_dir=reports, pubyear_cache={})
    assert [e["slug"] for e in to_report] == ["hum"]


def test_has_report_false_when_reports_dir_is_missing(tmp_path):
    assert bf.has_report("hum", tmp_path / "nope") is False


# ------------------------------------------------------- cap / ordering

def test_cap_takes_the_newest_finishes_and_defers_the_rest(tmp_path):
    new = _log(_book(slug="old", finished="2026-01-01"),
               _book(slug="newest", finished="2026-03-01"),
               _book(slug="middle", finished="2026-02-01"))
    to_report, deferred = bf.pending({}, new, limit=2,
                                     reports_dir=_reports(tmp_path),
                                     pubyear_cache={})
    assert [e["slug"] for e in to_report] == ["newest", "middle"]
    assert [e["slug"] for e in deferred] == ["old"]


def test_limit_zero_is_uncapped(tmp_path):
    new = _log(*[_book(slug=f"b{i}", finished=f"2026-01-0{i}")
                 for i in range(1, 5)])
    to_report, deferred = bf.pending({}, new, limit=0,
                                     reports_dir=_reports(tmp_path),
                                     pubyear_cache={})
    assert len(to_report) == 4
    assert deferred == []


def test_only_newly_finished_books_are_offered(tmp_path):
    """A book already finished in the old snapshot is not a new finish,
    even though it has no report."""
    old = _log(_book(slug="hum"))
    new = _log(_book(slug="hum"), _book(slug="flesh", finished="2026-02-01"))
    to_report, _ = bf.pending(old, new, reports_dir=_reports(tmp_path),
                              pubyear_cache={})
    assert [e["slug"] for e in to_report] == ["flesh"]


# ------------------------------------------------------- matrix entries

def test_describe_pulls_the_year_from_the_pubyear_cache():
    cache = {"hum|helen phillips": {"year": 2024}}
    entry = bf.describe(_book(), cache)
    assert entry == {"slug": "hum", "title": "Hum", "author": "Helen Phillips",
                     "year": 2024, "finished": "2026-01-01"}


def test_describe_year_is_blank_on_a_cache_miss():
    """Blank rather than None: the value is interpolated into a workflow
    input, where None would render as "None"."""
    assert bf.describe(_book(), {})["year"] == ""


@pytest.mark.parametrize("raw, expected", [
    ('Hum"; rm -rf /', "Hum rm -rf /"),   # ; is a shell separator
    ("${{ secrets.NTFY_TOPIC }}", "secrets.NTFY_TOPIC"),
    ("Mrs. Dalloway's Party: A Short-Story Sequence",
     "Mrs. Dalloway's Party: A Short-Story Sequence"),
    ("Mamá, ¿qué pasó?", "Mamá, qué pasó?"),
    ("`whoami`", "whoami"),
])
def test_sanitize_strips_shell_and_expression_syntax(raw, expected):
    assert bf.sanitize(raw) == expected


def test_sanitize_caps_length():
    assert len(bf.sanitize("x" * 500)) == 200


# ------------------------------------------------------- snapshot reading

def test_missing_and_empty_snapshots_mean_empty_old(tmp_path):
    missing = tmp_path / "nope.json"
    assert bf.read_log_snapshot(str(missing)) == ({}, True)
    empty = tmp_path / "empty.json"
    empty.write_text("   \n")
    assert bf.read_log_snapshot(str(empty)) == ({}, True)


def test_a_non_dict_snapshot_is_treated_as_empty(tmp_path):
    path = tmp_path / "list.json"
    path.write_text("[]")
    assert bf.read_log_snapshot(str(path)) == ({}, True)


def test_garbage_snapshot_is_refused_not_treated_as_empty(tmp_path):
    """Refusing beats diffing against nothing: a corrupt base would
    otherwise re-offer the whole log."""
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    with pytest.raises(SystemExit) as exc:
        bf.read_log_snapshot(str(path))
    assert exc.value.code == 1


# ------------------------------------------------------- CLI


class _Args:
    def __init__(self, diff_before, limit=2, github_output=False):
        self.diff_before = diff_before
        self.limit = limit
        self.github_output = github_output


def _patch_repo(monkeypatch, tmp_path, log, reports=()):
    log_path = tmp_path / "log.json"
    log_path.write_text(json.dumps(log))
    monkeypatch.setattr(bf, "LOG_PATH", log_path)
    monkeypatch.setattr(bf, "REPORTS_DIR", _reports(tmp_path, *reports))
    monkeypatch.setattr(bf, "load_pubyear_cache", lambda *a, **k: {})
    return log_path


def test_run_writes_books_and_count_to_github_output(monkeypatch, tmp_path,
                                                     capsys):
    _patch_repo(monkeypatch, tmp_path, _log(_book(slug="hum")))
    out = tmp_path / "gh-out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert bf.run(_Args(str(tmp_path / "absent.json"),
                        github_output=True)) == 0
    written = out.read_text()
    books = json.loads(written.split("books=", 1)[1].split("\n", 1)[0])
    assert [b["slug"] for b in books] == ["hum"]
    assert "count=1\n" in written


def test_run_github_output_without_the_env_var_fails(monkeypatch, tmp_path):
    _patch_repo(monkeypatch, tmp_path, _log(_book(slug="hum")))
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    assert bf.run(_Args(str(tmp_path / "absent.json"),
                        github_output=True)) == 1


def test_run_warns_but_succeeds_when_everything_is_already_reported(
        monkeypatch, tmp_path, capsys):
    """The normal steady state of the recovery path: books finished, all
    reported. Zero work is success, not an error."""
    _patch_repo(monkeypatch, tmp_path, _log(_book(slug="hum")),
                reports=("hum",))
    assert bf.run(_Args(str(tmp_path / "absent.json"))) == 0
    assert "this run: 0" in capsys.readouterr().out


def test_run_fails_when_empty_old_finds_no_finished_books(monkeypatch,
                                                          tmp_path):
    """An empty old log lists every finished book, so zero means the
    wiring is broken rather than nothing having changed."""
    _patch_repo(monkeypatch, tmp_path, _log(_book(status="reading")))
    assert bf.run(_Args(str(tmp_path / "absent.json"))) == 1


def test_run_deferred_warning_is_truncated(monkeypatch, tmp_path, capsys):
    """The recovery path defers ~150 books; the annotation must not be the
    whole log."""
    books = [_book(slug=f"b{i:03d}", finished=f"2026-01-01") for i in range(40)]
    _patch_repo(monkeypatch, tmp_path, _log(*books))
    assert bf.run(_Args(str(tmp_path / "absent.json"), limit=2)) == 0
    warning = [l for l in capsys.readouterr().out.splitlines()
               if l.startswith("::warning::")]
    assert len(warning) == 1
    assert "38 newly-finished book(s) deferred" in warning[0]
    assert "and 30 more" in warning[0]
    assert len(warning[0]) < 400


def test_run_prints_json_when_not_in_actions(monkeypatch, tmp_path, capsys):
    _patch_repo(monkeypatch, tmp_path, _log(_book(slug="hum")))
    assert bf.run(_Args(str(tmp_path / "absent.json"))) == 0
    out = capsys.readouterr().out
    assert json.loads(out[out.index("["):])[0]["slug"] == "hum"
