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

## Reading-log follow-ups
- Re-reads: second pass through a book (`slug-2` convention).
- Surface ratings on list tiles (star overlay for finished books).
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
  Letterboxd/Fable imports.
- iTunes metadata beyond covers (genres, descriptions, release dates)
  as future enrichment for lists/reading pages.
