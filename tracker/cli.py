"""Command-line interface.

  python -m tracker check [--dry-run] [--no-notify] [--source ID]
  python -m tracker add book "title" [--yes]
  python -m tracker add movie "title" [--year 2026] [--yes]
  python -m tracker pin ID (--choice N | --keep | --remove) [--expect BIB/ISBN]
  python -m tracker probe [--source ID] [--query "..."]
  python -m tracker list
  python -m tracker lists [--no-fetch]
  python -m tracker reading [--no-fetch]
  python -m tracker letterboxd
  python -m tracker web [--port 8765] [--no-browser]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .config import Config, load_config
from .engine import run_check
from .sources import build_sources
from .watchlist_io import append_entry

DEFAULT_WEB_PORT = 8765


def _load_dotenv() -> None:
    """Load .env from the repo root if it exists (no dependency needed)."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser(prog="tracker",
                                     description="Watchlist watcher for library "
                                                 "books and movie showtimes")
    parser.add_argument("--watchlist", help="path to watchlist.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="run all sources, notify on new sightings")
    p_check.add_argument("--source", help="only run this source id")
    p_check.add_argument("--dry-run", action="store_true",
                         help="print report; don't save state, write files, or push")
    p_check.add_argument("--no-notify", action="store_true",
                         help="update state and report but skip the phone push")

    p_add = sub.add_parser("add", help="add a watchlist entry, verified against "
                                       "live catalog records")
    p_add.add_argument("kind", choices=["book", "movie"])
    p_add.add_argument("title")
    p_add.add_argument("--author")
    p_add.add_argument("--year", type=int)
    p_add.add_argument("--isbn")
    p_add.add_argument("--yes", action="store_true",
                       help="skip the interactive pick; add exactly as typed")
    p_add.add_argument("--auto", action="store_true",
                       help="non-interactive: pin the best matching catalog "
                            "record if unambiguous, else add as typed; "
                            "sends an ntfy confirmation")

    p_probe = sub.add_parser("probe", help="dump raw source responses for "
                                           "endpoint/selector debugging")
    p_probe.add_argument("--source", help="only probe this source id")
    p_probe.add_argument("--query", help="override the probe search query")

    p_pin = sub.add_parser("pin", help="resolve a pending-pin record queued "
                                       "by an ambiguous mobile add")
    p_pin.add_argument("id", help="pending record id from state/pending-pins.json")
    group = p_pin.add_mutually_exclusive_group(required=True)
    group.add_argument("--choice", type=int,
                       help="1-based candidate number to pin the entry to")
    group.add_argument("--keep", action="store_true",
                       help="keep the entry as typed (fuzzy matching)")
    group.add_argument("--remove", action="store_true",
                       help="remove the entry from the watchlist")
    p_pin.add_argument("--expect",
                       help="optional bib_id/isbn guard: fail if the chosen "
                            "candidate doesn't carry it")

    sub.add_parser("list", help="show the parsed watchlist")

    p_lists = sub.add_parser("lists", help="render docs/lists/ pages from "
                                           "lists/*.yaml (covers cached)")
    p_lists.add_argument("--no-fetch", action="store_true",
                         help="never hit Open Library; uncached items get "
                              "typographic tiles")

    p_reading = sub.add_parser("reading", help="render the diary: "
                                               "docs/reading/ calendar + "
                                               "book pages + docs/watching/ "
                                               "film pages (page counts "
                                               "cached)")
    p_reading.add_argument("--no-fetch", action="store_true",
                           help="never hit iTunes/Open Library; uncached "
                                "books get no page count")
    p_reading.add_argument("--import-bookmory", metavar="FILE",
                           help="merge a Bookmory backup.zip (or its "
                                "new_bookmory.db) into reading/log.json "
                                "before building")

    p_lb = sub.add_parser("letterboxd", help="sync Letterboxd diary RSS "
                                             "into watching/log.json")
    p_lb.add_argument("--import", dest="lb_import", metavar="FILE",
                      help="backfill from a Letterboxd data-export zip "
                           "instead of syncing RSS")
    p_lb.add_argument("--import-since", default="2025-01-01",
                      metavar="DATE", help="oldest watched date to "
                                           "backfill (default 2025-01-01)")

    p_web = sub.add_parser("web", help="run the local web app")
    p_web.add_argument("--port", type=int, default=DEFAULT_WEB_PORT)
    p_web.add_argument("--no-browser", action="store_true",
                       help="don't auto-open the browser")

    args = parser.parse_args(argv)
    config = load_config(args.watchlist)

    if args.command == "check":
        return cmd_check(config, args)
    if args.command == "add":
        return cmd_add(config, args)
    if args.command == "pin":
        return cmd_pin(config, args)
    if args.command == "probe":
        return cmd_probe(config, args)
    if args.command == "list":
        return cmd_list(config)
    if args.command == "lists":
        from .lists_gen import build_all
        build_all(fetch=not args.no_fetch)
        return 0
    if args.command == "reading":
        if args.import_bookmory:
            from pathlib import Path
            from .bookmory_import import run as import_bookmory
            import_bookmory(Path(args.import_bookmory))
        from .reading_gen import build_all as build_reading
        build_reading(fetch=not args.no_fetch)
        return 0
    if args.command == "letterboxd":
        if args.lb_import:
            from pathlib import Path
            from .letterboxd_import import run as lb_import
            lb_import(Path(args.lb_import), since=args.import_since)
            return 0
        from .letterboxd_sync import sync
        return sync()
    if args.command == "web":
        from .web import run_web
        return run_web(config_path=args.watchlist, port=args.port,
                       open_browser=not args.no_browser)
    return 2


def cmd_check(config: Config, args: argparse.Namespace) -> int:
    try:
        run = run_check(config, source_id=args.source,
                        dry_run=args.dry_run, no_notify=args.no_notify)
    except ValueError as exc:
        sys.exit(str(exc))

    print(run.report)
    if args.dry_run:
        print("(dry run: state not saved, no files written, no push sent)")
        return 0
    if run.pushed:
        print(f"pushed {len(run.new)} notification(s) via ntfy")
    elif run.new and run.push_error:
        print(f"WARNING: new sightings recorded but not pushed: {run.push_error}",
              file=sys.stderr)
    # Partial source failures are normal (sites flake); only a run where
    # every source errored is a failed run.
    return 1 if run.all_failed else 0


def cmd_add(config: Config, args: argparse.Namespace) -> int:
    matches: list[dict] = []
    if args.kind == "book":
        if args.auto:
            entry, matches = _auto_pick_book(config, args)
        else:
            entry = _pick_book(config, args)
        section = "books"
    else:
        entry = {"title": args.title}
        if args.year:
            entry["year"] = args.year
        section = "movies"

    if args.auto and _already_watched(config, args.kind,
                                      entry["title"], args.title):
        msg = f"already on the watchlist: {entry['title']}"
        print(msg)
        _send_note("watchlist add", msg, tags="information_source")
        return 0

    watchlist_path = Path(args.watchlist) if args.watchlist else \
        Path(__file__).resolve().parent.parent / "watchlist.yaml"
    added = append_entry(watchlist_path, section, entry)
    print(f"added to {section}: {entry}")
    if args.auto:
        if entry.get("bib_id") or entry.get("isbn"):
            how = "pinned to an exact catalog record"
        elif args.kind == "book" and added:
            # Ambiguous (0 or 2+ catalog matches): queue it so the add
            # page can surface a "needs pinning" card. The entry still
            # watches as typed in the meantime.
            from .pending import add_pending
            record = add_pending(args.title, args.author, matches)
            print(f"queued for pinning: {record['id']}")
            if matches:
                how = (f"{len(matches)} candidates need pinning — "
                       "open the add page")
            else:
                how = "not found in catalog — open the add page"
        else:
            how = "watching by title"
        _send_note("watchlist add", f"added {args.kind}: {entry['title']} ({how})")
    return 0


def _already_watched(config: Config, kind: str,
                     *titles: str) -> bool:
    from .models import normalize_key
    existing = {b.key for b in config.books} | {m.key for m in config.movies}
    return any(f"{kind}:{normalize_key(t)}" in existing for t in titles)


def _auto_pick_book(config: Config,
                    args: argparse.Namespace) -> tuple[dict, list[dict]]:
    """Non-interactive pick: pin a BiblioCommons record when the title
    matches unambiguously; otherwise add as typed (fuzzy title matching
    still catches it everywhere, including cloudLibrary). Returns
    (entry, title-matched candidates) — the caller queues the candidate
    list for async pinning when nothing was pinned."""
    from .matching import titles_match

    as_typed = {"title": args.title}
    if args.author:
        as_typed["author"] = args.author
    if args.isbn:
        as_typed["isbn"] = args.isbn

    candidates = search_book_candidates(config, args.title, log=print)
    matches = [c for c in candidates
               if c.get("title") and titles_match(args.title, c["title"])]
    # Prefer the print record's bib_id (the manual convention: bib_id pins
    # the library record, no isbn so cloudLibrary title-matches every format).
    pinned = next((c for c in matches if c.get("bib_id")
                   and (c.get("format") or "").upper() in ("BK", "PAPERBACK")),
                  None) or next((c for c in matches if c.get("bib_id")), None)
    if not pinned:
        return as_typed, matches
    entry = candidate_to_entry(pinned, isbn_override=args.isbn)
    if not args.isbn:
        entry.pop("isbn", None)
    return entry, matches


def cmd_pin(config: Config, args: argparse.Namespace) -> int:
    """Resolve a queued pending-pin record (see tracker/pending.py)."""
    from . import pending
    from .watchlist_io import remove_entry, update_entry

    record = pending.pop(pending.PENDING_PATH, args.id)
    if record is None:
        # Already resolved (double-tap from the add page) — nothing to do.
        print(f"no pending record with id {args.id!r}; already resolved?")
        return 0

    title = record["typed_title"]
    watchlist_path = Path(args.watchlist) if args.watchlist else \
        Path(__file__).resolve().parent.parent / "watchlist.yaml"

    if args.remove:
        if remove_entry(watchlist_path, "books", title):
            msg = f"removed from watchlist: {title}"
        else:
            msg = f"{title} already gone from watchlist"
        print(msg)
        _send_note("watchlist pin", msg, tags="wastebasket")
        return 0

    if args.keep:
        msg = f"keeping as typed: {title}"
        print(msg)
        _send_note("watchlist pin", msg)
        return 0

    candidates = record.get("candidates") or []
    if not 1 <= args.choice <= len(candidates):
        pending.add_pending(title, record.get("typed_author"), candidates,
                            kind=record.get("kind", "book"))
        sys.exit(f"choice {args.choice} out of range (1-{len(candidates)}); "
                 "record re-queued")
    picked = candidates[args.choice - 1]
    if args.expect and args.expect not in (picked.get("bib_id"),
                                           picked.get("isbn")):
        pending.add_pending(title, record.get("typed_author"), candidates,
                            kind=record.get("kind", "book"))
        sys.exit(f"candidate {args.choice} doesn't carry expected id "
                 f"{args.expect!r}; record re-queued")

    # Same convention as _auto_pick_book: bib_id pins the library record;
    # drop the isbn so cloudLibrary keeps title-matching every format.
    entry = candidate_to_entry(picked)
    if picked.get("bib_id"):
        entry.pop("isbn", None)
    if update_entry(watchlist_path, "books", title, entry):
        msg = f"pinned {title} -> {entry.get('bib_id') or entry.get('isbn')}"
    else:
        # Entry hand-deleted meanwhile — treat as resolved, don't fail.
        msg = f"{title} no longer on watchlist; nothing to pin"
    print(msg)
    _send_note("watchlist pin", msg, tags="pushpin")
    return 0


def _send_note(title: str, message: str, tags: str = "heavy_plus_sign") -> None:
    from . import notify
    try:
        notify.send_note(title, message, tags=tags)
    except Exception as exc:  # noqa: BLE001 — the add itself succeeded
        print(f"(ntfy confirmation failed: {exc})", file=sys.stderr)


def _pick_book(config: Config, args: argparse.Namespace) -> dict:
    as_typed = {"title": args.title}
    if args.author:
        as_typed["author"] = args.author
    if args.isbn:
        as_typed["isbn"] = args.isbn

    interactive = sys.stdin.isatty() and not args.yes
    if not interactive:
        return as_typed

    candidates = search_book_candidates(config, args.title, log=print)
    if not candidates:
        print("no live catalog records found; adding as typed "
              "(fuzzy matching will apply)")
        return as_typed

    print("\nPick the record you mean (canonical IDs make matching exact):")
    print("  0. none of these — add exactly as typed")
    for i, c in enumerate(candidates, 1):
        bits = [c.get("title") or "?"]
        if c.get("author"):
            bits.append(str(c["author"]))
        if c.get("format"):
            bits.append(str(c["format"]))
        bits.append(f"[{c['source']}]")
        print(f"  {i}. " + " — ".join(bits))

    while True:
        raw = input("choice: ").strip()
        if raw.isdigit() and 0 <= int(raw) <= len(candidates):
            break
        print(f"enter a number 0-{len(candidates)}")
    choice = int(raw)
    if choice == 0:
        return as_typed
    return candidate_to_entry(candidates[choice - 1], isbn_override=args.isbn)


def search_book_candidates(config: Config, query: str, *, log=None,
                           limit: int = 10) -> list[dict]:
    """Live catalog candidates for a title, deduped. Shared by CLI + web."""
    candidates: list[dict] = []
    for source in build_sources(config):
        search = getattr(source, "search_books", None)
        if not search:
            continue
        if log:
            log(f"searching {source.source_id} ...")
        try:
            candidates.extend(c for c in search(query) if c.get("title"))
        except Exception as exc:  # noqa: BLE001
            if log:
                log(f"  ({source.source_id} search failed: {exc})")

    seen: set[tuple] = set()
    unique = []
    for c in candidates:
        key = (c.get("title"), c.get("author"), c.get("format"))
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique[:limit]


def candidate_to_entry(picked: dict, isbn_override: str | None = None) -> dict:
    entry = {"title": picked["title"]}
    if picked.get("author"):
        entry["author"] = picked["author"]
    if isbn_override or picked.get("isbn"):
        entry["isbn"] = isbn_override or picked["isbn"]
    if picked.get("bib_id"):
        entry["bib_id"] = picked["bib_id"]
    return entry


def cmd_probe(config: Config, args: argparse.Namespace) -> int:
    sources = build_sources(config)
    if args.source:
        sources = [s for s in sources if s.source_id == args.source]
        if not sources:
            sys.exit(f"no enabled source with id '{args.source}'")
    for source in sources:
        print(f"\n===== {source.source_id} ({source.kind}) =====")
        try:
            print(source.probe(config, args.query))
        except Exception as exc:  # noqa: BLE001
            print(f"probe failed: {type(exc).__name__}: {exc}")
    return 0


def cmd_list(config: Config) -> int:
    print(f"books ({len(config.books)}):")
    for b in config.books:
        ids = " ".join(filter(None, [
            f"isbn={b.isbn}" if b.isbn else None,
            f"bib_id={b.bib_id}" if b.bib_id else None,
        ]))
        print(f"  - {b}" + (f"  [{ids}]" if ids else ""))
    print(f"movies ({len(config.movies)}):")
    for m in config.movies:
        print(f"  - {m}")
    print(f"sources ({len(config.enabled_sources())} enabled):")
    for sid, cfg in config.sources.items():
        flag = "on " if cfg.get("enabled", True) else "off"
        print(f"  - [{flag}] {sid} ({cfg.get('kind')})")
    return 0
