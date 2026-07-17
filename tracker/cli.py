"""Command-line interface.

  python -m tracker check [--dry-run] [--no-notify] [--source ID]
  python -m tracker add book "title" [--yes]
  python -m tracker add movie "title" [--year 2026] [--yes]
  python -m tracker probe [--source ID] [--query "..."]
  python -m tracker list
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

    p_probe = sub.add_parser("probe", help="dump raw source responses for "
                                           "endpoint/selector debugging")
    p_probe.add_argument("--source", help="only probe this source id")
    p_probe.add_argument("--query", help="override the probe search query")

    sub.add_parser("list", help="show the parsed watchlist")

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
        entry = _pick_book(config, args)
        section = "books"
    else:
        entry = {"title": args.title}
        if args.year:
            entry["year"] = args.year
        section = "movies"

    watchlist_path = Path(args.watchlist) if args.watchlist else \
        Path(__file__).resolve().parent.parent / "watchlist.yaml"
    append_entry(watchlist_path, section, entry)
    print(f"added to {section}: {entry}")
    return 0


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
