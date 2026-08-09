#!/usr/bin/env python3
"""Discovery instrument: do the catalogs leak a title's *drop time*?

We want to be ready the instant a competitive new ebook/audiobook enters a
library's borrowable pool. The production sources deliberately throw away
everything that could tell us WHEN that happens:

  - the change-detection `event` is availability-independent ("ebook in
    catalog") on purpose, so copy-count/availability flips don't re-notify;
  - cloudLibrary keeps only `raw = {first 12 keys}` of each record
    (tracker/sources/cloudlibrary.py), so any date/status field past key 12
    never even reaches an Observation;
  - Libby's `probe` prints a fixed 10-key subset, never the full record.

So we've never systematically looked at whether a street/publish/on-sale date
or a pre-order/on-order flag rides along in the payloads we already fetch.
This script dumps the FULL, untruncated records for the cloudLibrary and Libby
sources and highlights any key that smells like a timing/status breadcrumb.

It is strictly read-only: it reuses each source's own endpoint config and the
shared throttled http helper, and it never touches state, notifications, or
any committed file.

    python scripts/discover_droptime.py                 # all watchlist titles
    python scripts/discover_droptime.py "some new title" 9780000000000
    python scripts/discover_droptime.py --source cloudlibrary "title"

NOTE: the catalog hosts (ebook.yourcloudlibrary.com, thunder.api.overdrive.com)
must be reachable. A restricted egress environment (e.g. a sandbox whose proxy
allowlist excludes them) will 403 every request — run this locally or on the
GitHub Actions runner, which already talks to these hosts twice daily.

cloudLibrary is the priority target: historically the less-competitive digital
ecosystem, so a sharp drop-time signal there is the higher-leverage edge.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tracker import http  # noqa: E402
from tracker.config import load_config  # noqa: E402
from tracker.matching import search_query  # noqa: E402
from tracker.sources.cloudlibrary import CloudLibrarySource  # noqa: E402
from tracker.sources.libby import LibbySource  # noqa: E402

# Keys worth a second look: dates, on-sale/street/publish, pre-order/on-order,
# availability/status. Case-insensitive substring match on the key name.
BREADCRUMB_RE = re.compile(
    r"date|street|onsale|on_sale|publish|release|pubdate|"
    r"preorder|pre_order|prerelease|pre_release|onorder|on_order|"
    r"coming|expected|estimat|avail|status|owned|copies|hold",
    re.IGNORECASE,
)

# A node looks like a bib record if it has a title AND at least one id-ish key.
BIB_TITLE = ("Title", "title")
BIB_HINT = ("Authors", "authors", "ISBN", "isbn", "MediaType", "mediaType",
            "Id", "id", "epubFormat", "duration", "creators", "formats")


def all_bibs(data) -> list[dict]:
    """Every dict that looks like a bib record, with ALL keys intact."""
    out: list[dict] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            if any(k in node for k in BIB_TITLE) and any(k in node for k in BIB_HINT):
                out.append(node)
                return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return out


def report_bibs(bibs: list[dict], *, limit: int = 5) -> None:
    if not bibs:
        print("    (no bib records parsed)")
        return
    vocab = sorted({k for b in bibs for k in b})
    print(f"    parsed {len(bibs)} record(s); {len(vocab)} distinct keys")
    print(f"    ALL KEYS: {vocab}")
    hits = [k for k in vocab if BREADCRUMB_RE.search(k)]
    print(f"    >>> BREADCRUMB CANDIDATES: {hits or '(none)'}")
    for i, b in enumerate(bibs[:limit], 1):
        print(f"    --- record {i} full JSON ---")
        print("    " + json.dumps(b, indent=2, default=str)[:4000].replace("\n", "\n    "))


def probe_cloudlibrary(src: CloudLibrarySource, query: str) -> None:
    sess = http.session()
    print(f"\n### cloudLibrary [{src.source_id}] query={query!r}")
    for ep in src._endpoints(query):
        try:
            if ep["method"] == "GET":
                resp = http.get(sess, ep["url"], headers={"Accept": "application/json"})
                if resp.status_code == 204:  # Remix cookie handshake
                    resp = http.get(sess, ep["url"], headers={"Accept": "application/json"})
            else:
                resp = http.post_json(sess, ep["url"], ep["payload"])
        except Exception as exc:  # noqa: BLE001
            print(f"  {ep['method']} -> {type(exc).__name__}: {exc}")
            continue
        print(f"  {ep['method']} -> HTTP {resp.status_code}, {len(resp.content)} bytes")
        if resp.status_code != 200:
            continue
        try:
            data = resp.json()
        except ValueError:
            print("    (response not JSON)")
            continue
        report_bibs(all_bibs(data))
        return
    print("  (no endpoint returned usable records)")


def probe_libby(src: LibbySource, query: str) -> None:
    sess = http.session()
    url = src.search_url(query)
    print(f"\n### Libby [{src.source_id}] query={query!r}")
    try:
        resp = http.get(sess, url, headers={"Accept": "application/json"})
    except Exception as exc:  # noqa: BLE001
        print(f"  GET -> {type(exc).__name__}: {exc}")
        return
    print(f"  GET -> HTTP {resp.status_code}")
    if resp.status_code != 200:
        return
    report_bibs(resp.json().get("items") or [])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("queries", nargs="*",
                    help="titles or ISBNs to probe (default: the watchlist)")
    ap.add_argument("--source", choices=["cloudlibrary", "libby"],
                    help="only probe this source kind")
    ap.add_argument("--watchlist", help="path to watchlist.yaml")
    args = ap.parse_args(argv)

    cfg = load_config(args.watchlist)
    queries = args.queries or [b.isbn or search_query(b.title) for b in cfg.books]
    print(f"queries: {queries}")

    def first(kind: str):
        return next(((sid, c) for sid, c in cfg.sources.items()
                     if c.get("kind") == kind), None)

    if args.source in (None, "cloudlibrary"):
        found = first("cloudlibrary")
        if found:
            src = CloudLibrarySource(*found)
            for q in queries:
                probe_cloudlibrary(src, q)
        else:
            print("\n(no cloudlibrary source configured)")

    if args.source in (None, "libby"):
        found = first("libby")
        if found:
            src = LibbySource(*found)
            for q in queries:
                probe_libby(src, q)
        else:
            print("\n(no libby source configured)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
