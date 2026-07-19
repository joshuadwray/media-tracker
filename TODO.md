# TODO / pins / ideas

## Pinned
- **Bookmory history import** — `tracker reading --import-bookmory FILE`.
  Blocked on exporting the file from the app. Design: inspect export
  first; map ratings → 0.5-step floats, statuses reading/finished/quit →
  reading/finished/abandoned; total pages → `page_count`; if no per-day
  history, synthesize one session at finish/export date. Merge by
  title|author, never overwrite, idempotent, print a report.
  When this lands: (1) add a jump-to-month select next to the calendar's
  older/newer pager — 60 months of history means 60 taps otherwise;
  (2) expect the first CI rebuild to crawl (page-count chain is rate-
  limited ~20 req/min per new book; one-time, caches after).
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
- Edit diary entries individually — reading side done 2026-07-19:
  in-place editors on the generated pages via docs/reading/edit.js
  (fresh-fetch log.json + Contents-API PUT). List diary: per-session
  edit (date/page/delete); book page: full editor (fields, sessions,
  delete book). log.html stays fast-logging only. Films still
  sync-only (watching/log.json comes from Letterboxd RSS).
- ~~Page counts on the calendar fix — the pg/goal display refinements
  deferred from the unified-diary pass.~~
  Done 2026-07-19: dropped the per-day pg number entirely (the flat list
  view shows page numbers; kept the green goal-day border + stats panel).
- Book pages like the movie pages — richer per-book pages à la
  `docs/watching/<slug>.html`. Partly there 2026-07-19: in-place
  editor, sessions table + chart, calendar thumbs now link through.
  Remaining: richer metadata (genres/description via iTunes?).
- Calendar shows one month at a time (2026-07-19) — older/newer
  buttons, newest first; JS-off falls back to the full stack.
- Create new lists from the web — lists/edit.html only edits
  existing lists today.
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
