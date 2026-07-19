# TODO / pins / ideas

## Pinned
- **Bookmory history import** — `tracker reading --import-bookmory FILE`.
  Blocked on exporting the file from the app. Design: inspect export
  first; map ratings → 0.5-step floats, statuses reading/finished/quit →
  reading/finished/abandoned; total pages → `page_count`; if no per-day
  history, synthesize one session at finish/export date. Merge by
  title|author, never overwrite, idempotent, print a report.
- **Pages vs local-app asymmetry** — the GH Pages surfaces (dashboard,
  add, lists + editor, reading log/calendar) are the real app now, while
  `tracker web` is machine-bound but has the richest add flow
  (interactive catalog candidate picking; add.html is `--auto` only).
  Either port the picker to Pages (workflow round-trip for catalog
  search) or retire `tracker web` once nothing it does is unique.

## Diary follow-ups (2026-07-18)
- ~~Non-calendar diary view — a Soderbergh-style flat chronological list
  of everything watched/read, alongside the calendar.~~
  Done 2026-07-18: docs/reading/list.html (one row per session/viewing,
  newest first) with a calendar · list toggle on both pages.
- Edit diary entries individually (watching/log.json entries are
  sync-only today; reading edits go through log.html's whole-file save).
- Page counts on the calendar fix — the pg/goal display refinements
  deferred from the unified-diary pass.
- UI pass: calendar page polish, cleaner tabs/links/organization
  across the site nav surfaces.

## Reading-log follow-ups
- Re-reads: second pass through a book (`slug-2` convention).
- ~~Surface ratings on list tiles (star overlay for finished books).~~
  Done 2026-07-18: ★ badge on tiles + finish-date chip on the calendar.
- Streak / chart polish on the calendar.
- ISBN → bib_id bridge: pagecount-cache already stores ISBN-13s; use
  them to auto-pin library catalog records for watchlist adds.
- Cached page-count misses never self-retry; if that bites, add a
  retry-after-N-days rule (manual fix today: delete the cache entry or
  set the count on the card).

## Older / ambient
- Angelika Dallas showtimes — parked: CSR React app, backend needs a
  reCAPTCHA-gated bearer token.
- Extract more media-diary features into this flat-file/Actions
  architecture: diary logging beyond books, TMDB/MusicBrainz search,
  Fable imports. (Letterboxd: done 2026-07-18 — daily RSS sync into
  watching/log.json, and films now surface on the unified diary
  calendar + docs/watching/ pages.)
- iTunes metadata beyond covers (genres, descriptions, release dates)
  as future enrichment for lists/reading pages.
