# TODO / pins / ideas

## Pinned
- ~~Bookmory history import.~~ Done 2026-07-19:
  `tracker reading --import-bookmory backup.zip` (tracker/
  bookmory_import.py reads new_bookmory.db, a sembast_sqflite store).
  133 books imported (test log wiped first — Bookmory is the sole
  source now); page_log_list → sessions, 8 synthesized at finish date,
  covers seeded from Bookmory's own URLs, page counts backfilled.
  Data gotchas handled: Goodreads "(Series #N)" title suffixes,
  author only in `authors[]` for manually-added books, double spaces.
- ~~Jump-to-month select next to the calendar's older/newer pager.~~
  Done 2026-07-19: server-rendered <select> between the pager buttons
  (hidden when JS is off, since all months show stacked then).
- ~~Backfill Letterboxd to 2025-01-01.~~ Done 2026-07-19:
  `tracker letterboxd --import <export.zip>` (tracker/
  letterboxd_import.py) — 81 entries from diary.csv + reviews/likes;
  slug via boxd.it redirect, poster/tmdb_id scraped from the film
  page's JSON-LD. Synthetic "letterboxd-import-<md5>" guids; sync's
  merge now upgrades those by title+date if the RSS window overlaps
  (it did — 20 dupes purged once). settings.since now 2025-01-01.
- **Pages vs local-app asymmetry** — largely resolved 2026-07-19: the
  catalog-candidate picker is on Pages now as an *async pin queue*
  (ambiguous `--auto` adds queue to state/pending-pins.json; add.html
  shows "needs pinning" cards → pin-item.yml → `tracker pin`). cloudLibrary
  isn't browser-callable (CORS), hence async rather than live picking.
  Remaining: retire `tracker web` once the pin queue proves out.
  Root cause this fixed: bare "yesteryear" add fired false-positive
  cloudLibrary notifications (fuzzy `titles_match`, no author check) —
  now guarded by `author_matches` (surname, fail-open) in
  cloudlibrary/bibliocommons checks + pinning.

## Diary follow-ups (2026-07-18)
- ~~Non-calendar diary view — a Soderbergh-style flat chronological list
  of everything watched/read, alongside the calendar.~~
  Done 2026-07-18: docs/reading/list.html (one row per session/viewing,
  newest first) with a calendar · list toggle on both pages.
- ~~Edit diary entries individually.~~ Done 2026-07-19 for books:
  in-place editors on the generated pages via docs/reading/edit.js
  (fresh-fetch log.json + Contents-API PUT). Films are sync-only BY
  DESIGN — Letterboxd is the full-featured editor; the RSS sync picks
  up changes (within its ~50-item window).
- ~~Page counts on the calendar fix — the pg/goal display refinements
  deferred from the unified-diary pass.~~
  Done 2026-07-19: dropped the per-day pg number entirely (the flat list
  view shows page numbers; kept the green goal-day border + stats panel).
- Calendar shows one month at a time (2026-07-19) — older/newer
  buttons, newest first; JS-off falls back to the full stack.
- ~~Create new lists from the web — lists/edit.html only edits
  existing lists today.~~ Done 2026-07-19: "+ new list" button
  (title + ranked toggle) creates `lists/<stem>.yaml` via a sha-less
  Contents PUT, then loads the empty list for item entry.
- UI pass: done 2026-07-19 — shared BASE_CSS + pill-tab nav in
  tracker/site.py (generators dieted; nav pasted into the 3 hand-written
  pages). CSS moved out to docs/assets/*.css on 2026-08-09.
- ~~Diary/lists edits take ~2 min to show up.~~ Done 2026-08-09: they
  render in the BROWSER now. Measured first — CI was only 13-24s, the
  Pages deploy ~40s (a floor), and Pages' `cache-control: max-age=600`
  plus no completion signal did the rest. The fix follows the real
  boundary: MACHINE-authored data (scraper, Letterboxd — nobody waits)
  stays server-rendered; HUMAN-authored data (reading log, lists) is
  interactive CRUD and can't sit behind a build. CI now publishes only
  what a browser can't compute — docs/data/{diary,lists}.json with covers
  and page counts resolved — and docs/assets/diary.js draws the calendar,
  flat list, book pages and lists grid. Python renderers deleted (one
  renderer, not two: this would otherwise have become a fourth copy of
  the log.html/edit.js/dump_log parity hazard). Saves go into
  localStorage via mtSavePending and appear with zero network, then the
  overlay retires itself when a build contains it.
  Guard rail if you touch diary.js: a node harness diffs its output
  against the old Python HTML recovered from git (it caught 3 real bugs);
  see the memory note for how it was run.

## Reading-log follow-ups
- ~~Re-reads: second pass through a book (`slug-2` convention).~~
  Done 2026-07-19: log entries stay one-per-read (`<base>-2` slugs);
  generation groups by title|author onto ONE page at the base slug
  ("Read N" sections, per-read editors), diary/list-tile links resolve
  to the base page, list-tile rating = latest finished read. "read
  again" link on book pages clones the entry via edit.js.
- ~~Surface ratings on list tiles (star overlay for finished books).~~
  Done 2026-07-18: ★ badge on tiles + finish-date chip on the calendar.
- Cached page-count misses never self-retry; if that bites, add a
  retry-after-N-days rule (manual fix today: delete the cache entry or
  set the count on the card).
- **cloudLibrary carries page counts.** Its search records have
  `totalExtents` (ebooks only; audiobooks carry `duration` instead) —
  verified 2026-08-10, e.g. Dead but Dreaming = 336. The library sources
  already stash it on `Observation.detail["pages"]`, so it could become a
  step in `reading_gen`'s page-count chain (covers-cache ISBN → OL →
  iTunes → Apple Books). Attractive because it needs no extra request and
  covers exactly the new releases OpenLibrary tends to miss — but it only
  fires for books that are on the watchlist AND in a cloudLibrary we
  watch, so it's a supplement, not a replacement.

## Pending cleanup
- Retire `add-item.yml` once `add-items.yml` (batch) has a few real runs
  behind it. docs/add.html dispatches only the batch workflow now; the
  single one is kept purely as a fallback.

## Investigate
- **Print catalogs for the non-Denton cards (2026-08).** Only Denton runs
  BiblioCommons, so every other card needs its own source:
  - ~~SirsiDynix Enterprise~~ **done 2026-08-10** for Lewisville
    (`lewisville-print`, `tracker/sources/sirsi_enterprise.py`). The
    "session-heavy" warning was half right: the availability call is
    CSRF-gated, but the token is per *session* and travels as a request
    **header** named `sdcsrf` — not as the `sdcsrf=` query param
    Enterprise puts in its own markup, which 403s. See the module
    docstring; the dead ends are written down there so nobody re-walks
    them.
    Houston print (`halan.sdp.sirsi.net`, profile `hou`) is now a
    config-only add — same `kind`, different `host`/`profile` — but it
    stays off: at ~240mi it is past `MAX_DISTANCE_MI`, so it could never
    headline a book, and it would cost a request per book per run.
  - Fort Worth — Polaris (fwmlc.polarislibrary.com), which has a fairly
    scrapable JSON search API. Theoretically nice, realistically of
    limited utility given the drive.
  Digital is covered everywhere: Lewisville = cloudLibrary (added
  2026-08), Fort Worth + Houston = Libby/OverDrive (Houston added
  2026-08).

- **`author_matches` breaks on multi-author watchlist entries (2026-08-10).**
  The guard takes the *last* token of the wanted author string as "the
  surname". For "Surname, First" that is the first name, which still
  appears in the found record, so it works by luck. For an entry naming a
  translator — "Harpman, Jacqueline, Schwartz, Ros" — it tests for "ros"
  and rejects a correct match. Live consequence: Lewisville holds *We Were
  Forbidden* (SD_ILS:428636, "Harpman, Jacqueline, author.") and the source
  drops it. Affects bibliocommons and cloudLibrary identically, so this is a
  shared-matching fix, not a per-source one — and loosening it re-runs every
  unpinned title's guard, so it wants its own change with its own test.
- **OverDrive trap, found 2026-08**: thunder key `denton` still resolves
  AND serves media, but the record says `"status": "Terminated"` and
  denton.overdrive.com 302s to /terminated. Denton dropped Libby. Always
  check `status` before trusting a thunder library key — a dead
  collection answers queries as if it were live.
- ~~cloudLibrary consortium title-sharing vs `owned=yes`.~~ Resolved
  2026-07-19 same-day using the user's live checkouts as ground truth:
  "This Is Where the Serpent Lives" was checked out yet absent from
  owned=yes — shared-in titles are NOT owned. Discriminator found in
  the full record JSON: borrowable = `isPayPerUse` (pay-per-use/
  consortium pool, null copy counts) OR `totalCopies > 0` (owned);
  marketplace-only records are ppu=false + null copies. Source now
  searches owned=any again and filters on that (verified against 3
  known-false + 3 of 4 checked-out titles). Follow-up: TWO sharing
  mechanisms exist. PPU (serpent: ppu=true even while checked out) is
  caught. Idle-copy consortium share (Dog Days/LaBarge, user's live
  ebook checkout): invisible under owned=yes AND ppu=false/null-copies
  under owned=any while the copy is IN USE — indistinguishable from
  marketplace-only. Hypothesis: it surfaces with real totalCopies only
  while idle at its home library (= the librarian's appear/disappear
  story), which is exactly when it's borrowable, so the filter may be
  behaviorally right. EXPERIMENT: when the user returns Dog Days,
  re-probe it (owned=yes + owned=any, check ppu/totalCopies) to
  confirm the flip. Also: what does `ppuTitleExcludes` mean?
  Later probe (same day, vs user's BC-cataloged digital checkouts):
  Lewinsky ebook has NO cloudLibrary record at all — Denton runs
  another digital vendor whose titles get full BiblioCommons EBOOK
  records; Antimemetics is BOTH in the CL pool (ppu, 3 copies) and
  BC-cataloged (AB+EBOOK); Wolf Hour is CL-pool-only (ppu, 12/7
  copies, no BC record). All CL records seen so far are ppu=true —
  totalCopies looks like network-pool copies, not Denton holdings.
  Model: CL API = what patrons can reach; BC cataloging = the only
  (incomplete, laggy) marker of Denton ownership. Tracker coverage is
  the union of both sources, which is what we want.

## Older / ambient
- Angelika Dallas showtimes — parked: CSR React app, backend needs a
  reCAPTCHA-gated bearer token.
- ISBN → bib_id bridge — demoted 2026-07-19: cached ISBNs mostly
  cover already-read books, not watchlist adds; author_matches + the
  pin queue already fixed the false-positive problem. Revisit only if
  pin-queue traffic gets annoying (better version: iTunes ISBN lookup
  at add time, works for any book).
