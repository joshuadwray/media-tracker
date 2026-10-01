"""Which books just became finished, and which of those still need a report.

The event source is a phone edit committing reading/log.json; the
book-finished workflow diffs the before/after commits and runs the
book-consensus skill once per newly-finished book, writing
reports/<slug>.md.

Dedupe is the committed file, not a state file: a book has been reported
iff reports/<slug>.md exists in the repo. That makes the recovery path
trivial — a dispatch with no before_sha diffs every finished book against
nothing, and everything already reported drops out — at the cost of needing
a hard cap, since 150+ books in the log are already finished.

The diff helpers live here rather than in hermes_relay because the relay
(ntfy -> launchd -> localhost webhook) existed only to reach a local agent
and is on its way out; hermes_relay re-exports these names so its tests and
its CLI keep working until it goes.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG_PATH = REPO / "reading" / "log.json"
PUBYEAR_CACHE_PATH = REPO / "reading" / "pubyear-cache.json"
REPORTS_DIR = REPO / "reports"

DEFAULT_LIMIT = 2   # see module docstring: an uncapped recovery run would
                    # file a report for every finished book in the log


# ---------------------------------------------------------------- diff

def newly_finished(old_log: dict, new_log: dict) -> list[dict]:
    """Books whose status newly became "finished" between two log snapshots.

    Keyed by slug: the phone/web log editor preserves `slug` on title and
    author edits, so a rename is not a new finish, and a rating change or
    re-serialization isn't either. A re-read is a separate log entry with a
    suffixed slug (hum, hum-2, ...), so finishing one fires — one report
    per read, by design.
    """
    old_slugs = _finished_slugs(old_log)
    out = []
    for book in (new_log or {}).get("books") or []:
        if not isinstance(book, dict) or book.get("status") != "finished":
            continue
        if _slug_of(book) not in old_slugs:
            out.append(book)
    return out


def _finished_slugs(log: dict) -> set:
    slugs = set()
    for book in (log or {}).get("books") or []:
        if isinstance(book, dict) and book.get("status") == "finished":
            slugs.add(_slug_of(book))
    return slugs


def _slug_of(book: dict) -> str:
    slug = book.get("slug")
    if slug:
        return str(slug)
    # Hand-edited entries can lack a stored slug; re-derive it exactly the
    # way the log loader does. If the derivation has drifted from the
    # stored slug of the "same" book, the finish re-fires — rare, accepted.
    from .reading_gen import slugify
    return slugify(str(book.get("title") or ""))


def load_pubyear_cache(path: Path = PUBYEAR_CACHE_PATH) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}   # every year just misses -> null; the skill resolves it


# ---------------------------------------------------------------- reports

def report_path(slug: str, reports_dir: Path | None = None) -> Path:
    return (reports_dir or REPORTS_DIR) / f"{slug}.md"


def has_report(slug: str, reports_dir: Path | None = None) -> bool:
    """Whether this book already has a report. An empty file counts as
    absent: a run that died mid-write shouldn't block the retry."""
    path = report_path(slug, reports_dir)
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


# The skill invocation is interpolated into a workflow input, and titles
# come from a hand-edited JSON file, so anything that could terminate the
# expression or smuggle shell syntax is dropped at the source rather than
# quoted downstream. Letters (any script), digits, spaces and the
# punctuation real book titles use survive.
_SAFE = re.compile(r"[^\w \-'’.,:!?&()/]", re.UNICODE)


def sanitize(text: str, limit: int = 200) -> str:
    return _SAFE.sub("", str(text or "")).strip()[:limit]


def describe(book: dict, pubyear_cache: dict) -> dict:
    """One matrix entry: what the workflow needs to invoke the skill."""
    title = str(book.get("title") or "")
    author = str(book.get("author") or "")
    # Same key the reading log uses for its caches (Book.cache_key).
    key = f"{title.strip().lower()}|{author.strip().lower()}"
    entry = pubyear_cache.get(key) if isinstance(pubyear_cache, dict) else None
    year = entry.get("year") if isinstance(entry, dict) else None
    return {
        "slug": _slug_of(book),
        "title": sanitize(title),
        "author": sanitize(author),
        "year": year or "",
        "finished": str(book.get("finished") or ""),
    }


def pending(old_log: dict, new_log: dict, limit: int = DEFAULT_LIMIT,
            reports_dir: Path | None = None,
            pubyear_cache: dict | None = None) -> tuple[list[dict], list[dict]]:
    """(to_report, deferred) — newly-finished books without a report yet,
    newest finish first, capped at `limit` (0 = uncapped).

    Deferred books are not lost state, just not this run's work: the next
    finish re-diffs, and a dispatch with no before_sha re-offers them.
    """
    cache = load_pubyear_cache() if pubyear_cache is None else pubyear_cache
    entries = [describe(b, cache) for b in newly_finished(old_log, new_log)]
    entries = [e for e in entries if not has_report(e["slug"], reports_dir)]
    entries.sort(key=lambda e: (e["finished"], e["slug"]), reverse=True)
    if limit and len(entries) > limit:
        return entries[:limit], entries[limit:]
    return entries, []


# ---------------------------------------------------------------- CLI

def run(args) -> int:
    """CLI entry for `python -m tracker book-finished` (see tracker/cli.py)."""
    old_log, old_empty = read_log_snapshot(args.diff_before)
    try:
        new_log = json.loads(LOG_PATH.read_text())
    except (OSError, ValueError) as exc:
        print(f"book-finished: could not read {LOG_PATH} ({exc})",
              file=sys.stderr)
        return 1

    finished = newly_finished(old_log, new_log)
    to_report, deferred = pending(old_log, new_log, limit=args.limit)

    print(f"newly finished: {len(finished)}; "
          f"needing a report: {len(to_report) + len(deferred)}; "
          f"this run: {len(to_report)}")
    for entry in to_report:
        print(f"  -> {entry['slug']}  ({entry['title']} — {entry['author']}"
              f"{', ' + str(entry['year']) if entry['year'] else ''})")
    if deferred:
        # The recovery path defers ~150 books, so name only a handful —
        # a workflow annotation with the whole log in it is unreadable.
        shown = ", ".join(e["slug"] for e in deferred[:8])
        more = (f" … and {len(deferred) - 8} more"
                if len(deferred) > 8 else "")
        print(f"::warning::{len(deferred)} newly-finished book(s) deferred by "
              f"--limit {args.limit} and will not be reported by this run: "
              f"{shown}{more}. Re-run the workflow (workflow_dispatch) to "
              f"pick up the next {args.limit}.")

    if not finished and old_empty:
        # An empty old log lists every currently-finished book; zero
        # results there means the wiring is broken, not that nothing
        # changed (a real before/after diff can legitimately be empty).
        print("book-finished: WARNING: old log was empty but no finished "
              "books were found in reading/log.json — this looks like "
              "broken wiring, not a no-op", file=sys.stderr)
        return 1

    if args.github_output:
        out = os.environ.get("GITHUB_OUTPUT")
        if not out:
            print("book-finished: --github-output but GITHUB_OUTPUT is not "
                  "set (not running in Actions?)", file=sys.stderr)
            return 1
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"books={json.dumps(to_report, ensure_ascii=False)}\n")
            fh.write(f"count={len(to_report)}\n")
    else:
        print(json.dumps(to_report, ensure_ascii=False, indent=2))
    return 0


def read_log_snapshot(path_string: str) -> tuple[dict, bool]:
    """Load the old-log snapshot. Returns (log, was_empty). Missing/empty
    means empty-old (the workflow's dispatch-without-before_sha and
    deleted-path cases write exactly that); anything non-dict or invalid
    is refused rather than silently treated as a mass finish."""
    path = Path(path_string)
    try:
        text = path.read_text() if path.exists() else ""
    except OSError as exc:
        print(f"book-finished: could not read old snapshot {path} ({exc})",
              file=sys.stderr)
        raise SystemExit(1)
    if not text.strip():
        print(f"(old snapshot {path} is "
              f"{'missing' if not path.exists() else 'empty'}; treated as "
              "empty — every finished book in reading/log.json counts as "
              "newly finished, so the report filter and --limit are what "
              "keep this bounded)")
        return {}, True
    try:
        data = json.loads(text)
    except ValueError as exc:
        print(f"book-finished: old snapshot {path} is not valid JSON ({exc}); "
              "refusing to diff against a garbage base", file=sys.stderr)
        raise SystemExit(1)
    if not isinstance(data, dict):
        print(f"(old snapshot {path} is not a reading log; treated as empty)")
        return {}, True
    return data, False
