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
  now guarded by `author_matches` (any name token, fail-open) in
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

- **Sync the actual library accounts (2026-08-10).** Everything the tracker
  knows today is the *public* view of a catalog: how long the queue is, not
  where in it you stand. The gap showed up concretely with holds on Fruit Fly
  and Sunrise at Lewisville — both on-order, and Symphony reports queue place
  `0` for them, because it sequences holds against item records and an
  on-order title has none (the "Copy 1 / On Order" row is an order record, not
  a circulating item). Public `holdCount` counts those holds (Sunrise showed
  exactly 1, the user's own) but can never say which one is yours.
  What logging in would buy, roughly in value order:
  - **Real queue position**, so a title you are near the front of stops being
    ranked as if you had just joined. Today `wait_after_arrival` answers "if I
    joined now" — correct while deciding, wrong once you hold a spot, and it
    gets *worse* over time as people queue up behind you.
  - **Suppression.** A book you already have on hold, or already have checked
    out, does not need to keep competing for a headline slot.
  - **Due dates and auto-logging**, which could feed `reading/log.json`
    instead of being typed in.
  Per-stack notes: Enterprise has a patron login form in-page
  (`detailnonmodal.template.patronloginform`) and the BLUEcloud Mobile app
  (`sirsi.mobile.bcmobile_lewisville`) implies a JSON account backend worth
  probing first — cleaner than scraping My Account. Denton is BiblioCommons,
  cloudLibrary and Libby each have their own login. So this is four
  integrations, not one.
  **The real gate is credentials, not scraping.** This needs card numbers and
  PINs in GitHub Secrets, and a compromise reaches an account that can place
  holds and see borrowing history. Decide that before building anything; a
  local-only mode (`tracker` run from the house, secrets in `.env`, never in
  CI) may be the right shape, the same conclusion the README's escalation
  ladder reaches for blocked scrapers.

- **Enterprise publishes on-order copy counts; the source assumes 1
  (2026-08-10).** `sirsi_enterprise._availability` discards the payload's
  `zones.detailOnOrderDiv0`, which carries a per-library table with an
  `SD_ORDER_COPIES` column. All seven on-order titles at Lewisville are
  currently 1 copy, so nothing is miscomputed today, but a 4-copy order would
  come out 4x too pessimistic in `wait_after_arrival(holds, 1, loan_days)`.
  Parse the zone and pass the real count.
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

- ~~**`author_matches` breaks on multi-author watchlist entries**~~ fixed
  2026-08-10. The guard tested the *last* token of the wanted author and
  called it a surname check. It isn't one — watchlist authors are written
  "Surname, First", so the last token is the given name, and it passed
  because the given name is normally in the found string too. An entry
  naming a translator ("Harpman, Jacqueline, Schwartz, Ros") tested for
  "ros" against "Harpman, Jacqueline, author." and rejected a correct
  match, hiding *We Were Forbidden* (SD_ILS:428636) at Lewisville. Now
  matches on any name token, ignoring bare initials and the birth years
  catalogs staple on. Deliberately a superset of the old rule, so nothing
  that matched before can stop matching; a full dry run across all four
  library sources changed exactly one row.
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

## AMC blocked by Cloudflare (2026-09-02, still down)
Every AMC theatre page returns a hard 403 ("Sorry, you have been
blocked" — a WAF block, not a JS challenge, so no cookie or wait
clears it). Not header-fixable: verified 2026-09-09 that a current
Chrome UA, full sec-ch-ua/Sec-Fetch set and Accept-Encoding all get
the identical 5486-byte block page, which points at TLS/JA3
fingerprinting rather than anything we send. Fails from a home IP too,
so it isn't GitHub's runners being blocklisted.
The one open door: `api.amctheatres.com` is NOT blocked — it answers
400 `{"errors":[{"code":1,"message":"The request requires vendor
authentication"}]}`, i.e. it wants an `X-AMC-Vendor-Key`. Keys come
from developers.amctheatres.com (itself 403 to us — register from a
real browser). That's a user action, so AMC stays dark until someone
decides: get a key and write an `amc-api` source, or drop the three
theatres. Meanwhile the outage is at least *visible* now (see below).

## Silent scraper death — FIXED 2026-09-09
AMC died on 2026-09-02 and nothing said so for seven days: partial
source failures don't fail the run (correctly — sites flake), and the
error only ever landed in state/report.md, which nobody reads on a
phone. Now `state.health` counts consecutive failed runs per source;
crossing DEAD_AFTER_RUNS (3, ~1.5 days at two runs/day) pushes one
"scraper down" note, recovery pushes one "scraper recovered", and
nothing repeats in between. The report's status line carries the
outage age and run count so a flake reads differently from a wall.

## Advance / promo screenings across the metroplex (idea, unshaped)
Not the chains' "advance tickets on sale" — the studio-driven, lightly
publicized press and word-of-mouth screenings held before release to
seed buzz. Different sourcing problem from everything the tracker does
today, which is why it's parked as an idea rather than a task.

What already exists: `chain_theaters.py` matches
`card__movie--advanced-tickets` and sets an `advance` flag, so Cinemark
and AMC already say "advance tickets on sale at ...". `drafthouse.py`
has no advance detection at all despite its feed carrying
`advance-screening-*` presentation slugs — so the chain-side signal is
real but uneven, and evening it out is the cheap half of this.

Where the actual promo screenings live (probed 2026-09-09):
- **Gofobo** — the big one, and Dallas-based. Homepage is up and
  server-renders upcoming titles (two of which, Heart of the Beast and
  Forgotten Island, also show in Cinemark's advance list). No
  `__NEXT_DATA__`, no `/api/` paths, no graphql in the markup;
  `/screenings` 500s. Per-city listings are very likely login-gated.
  Best lead by far.
- **SeeItFirst**, **Film Metro** — DNS failed from here; may be dead.
- **allianceco.com** — now an engineering firm. Dead lead, don't
  re-probe it.
- **Central Track** (Dallas alt-weekly) is alive and sometimes lists
  these; the `pages` source would cover it for nearly nothing.

Why it's a different beast:
- It's **discovery, not matching**. Every Observation hangs off a
  watchlist item_key; a metro-wide screening feed has no item to hang
  on, so it needs its own output surface.
- These screenings are **RSVP/code-gated and fill in hours**. A
  twice-daily cron is the wrong cadence for "an RSVP just opened",
  which is the only moment that matters.
- Titles are often withheld — "Secret Movie Series September 14" and
  "$5 Secret Movie 9/14/26" are already in the Cinemark and Landmark
  feeds — so title matching, the tracker's whole spine, degrades.

Open question before any of this gets built: the output surface. A push
per screening doesn't fit a non-watchlist firehose; a docs/ page or a
digest probably does.

## Older / ambient
- ~~Angelika Dallas showtimes.~~ Done 2026-09-09 —
  `tracker/sources/readingcinemas.py`. The two-month "parked: needs a
  reCAPTCHA-gated bearer token" note was simply wrong. reCAPTCHA is in
  the bundle but only on login/signup/payment; the catalog path never
  touches it. The token is handed out unauthenticated by
  `GET /settings/<country_id>` (`data.settings.token`), and
  `/films?...&status=nowShowing` with it returns 61 dated films with
  full showdates. Two requests, no credentials.
  Worth remembering *how* that was found, since the static analysis all
  came up empty (no Cognito SDK, no oauth2/token, no embedded secret,
  no token route among the app's 99 endpoint constants): a same-origin
  iframe, with its `contentWindow` XHR/fetch re-patched every 3ms while
  the 2.8MB bundle downloaded, caught the cold-boot sequence and showed
  the first call was already authenticated.
- ~~Landmark Inwood (Dallas arthouse).~~ Done 2026-09-09 —
  `tracker/sources/webedia.py`. It turned out not to need the API at
  all: the site is Gatsby, and Gatsby publishes its static query
  results as public JSON (`/page-data/sq/d/<hash>.json`). One blob is
  the whole circuit's film list with the theatre codes each is booked
  at. No auth anywhere in the path.
- Alamo Drafthouse — source exists and works, deliberately left
  disabled; see the rationale in watchlist.yaml before reopening it.
- ISBN → bib_id bridge — demoted 2026-07-19: cached ISBNs mostly
  cover already-read books, not watchlist adds; author_matches + the
  pin queue already fixed the false-positive problem. Revisit only if
  pin-queue traffic gets annoying (better version: iTunes ISBN lookup
  at add time, works for any book).
