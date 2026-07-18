"""Command-line interface.

  python -m tracker check [--dry-run] [--no-notify] [--source ID]
  python -m tracker add book "title" [--yes]
  python -m tracker add movie "title" [--year 2026] [--yes]
  python -m tracker probe [--source ID] [--query "..."]
  python -m tracker list
  python -m tracker lists [--no-fetch]
  python -m tracker reading [--no-fetch]
  python -m tracker web [--port 8765] [--no-browser]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config, load_config
from .engine import run_check
from .sources import build_sources
from .watchlist_io import append_entry

DEFAULT_WEB_PORT = 8765


def main(argv: list[str] | None = None) -> int:
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

    sub.add_parser("list", help="show the parsed watchlist")

    p_lists = sub.add_parser("lists", help="render docs/lists/ pages from "
                                           "lists/*.yaml (covers cached)")
    p_lists.add_argument("--no-fetch", action="store_true",
                         help="never hit Open Library; uncached items get "
                              "typographic tiles")

    p_reading = sub.add_parser("reading", help="render docs/reading/ pages "
                                               "from reading/log.json "
                                               "(page counts cached)")
    p_reading.add_argument("--no-fetch", action="store_true",
                           help="never hit iTunes/Open Library; uncached "
                                "books get no page count")

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
    if args.command == "probe":
        return cmd_probe(config, args)
    if args.command == "list":
        return cmd_list(config)
    if args.command == "lists":
        from .lists_gen import build_all
        build_all(fetch=not args.no_fetch)
        return 0
    if args.command == "reading":
        from .reading_gen import build_all as build_reading
        build_reading(fetch=not args.no_fetch)
        return 0
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
    if args.kind == "book":
        entry = _auto_pick_book(config, args) if args.auto \
            else _pick_book(config, args)
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
    append_entry(watchlist_path, section, entry)
    print(f"added to {section}: {entry}")
    if args.auto:
        if entry.get("bib_id") or entry.get("isbn"):
            how = "pinned to an exact catalog record"
        elif args.kind == "book":
            how = "no unambiguous catalog match — watching by title"
        else:
            how = "watching by title"
        _send_note("watchlist add", f"added {args.kind}: {entry['title']} ({how})")
    return 0


def _already_watched(config: Config, kind: str,
                     *titles: str) -> bool:
    from .models import normalize_key
    existing = {b.key for b in config.books} | {m.key for m in config.movies}
    return any(f"{kind}:{normalize_key(t)}" in existing for t in titles)


def _auto_pick_book(config: Config, args: argparse.Namespace) -> dict:
    """Non-interactive pick: pin a BiblioCommons record when the title
    matches unambiguously; otherwise add as typed (fuzzy title matching
    still catches it everywhere, including cloudLibrary)."""
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
        return as_typed
    entry = candidate_to_entry(pinned, isbn_override=args.isbn)
    if not args.isbn:
        entry.pop("isbn", None)
    return entry


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
