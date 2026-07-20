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
  pages).

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

## Investigate
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
