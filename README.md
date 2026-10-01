# media-tracker

Watches your watchlist so you don't have to: periodically checks library
catalogs for books and DFW-area theaters for movie showtimes, and sends
a phone push (via [ntfy.sh](https://ntfy.sh)) once per thing worth
knowing — a book becoming readable, a theatre picking up a film.

It also keeps a diary: a reading log, a Letterboxd-synced film log, and
ranked lists — all published to the same GitHub Pages site.

Deliberately lean: a small Python CLI, flat files you can edit by hand
(YAML watchlist, JSON logs), GitHub Actions as the runtime, no database
and no server.

```
watchlist.yaml → source adapters → observations → diff vs state.json → ntfy push + report.md
reading/log.json, lists/*.yaml → covers + page counts → docs/data/*.json → rendered in the browser
```

## Sources

| id | what | how |
|---|---|---|
| `denton-library` | Denton Public Library print (BiblioCommons) | parses the JSON embedded in catalog search pages |
| `cloudlibrary` | Denton ebooks/audiobooks (cloudLibrary) | unauthenticated web-patron search API |
| `cloudlibrary-lewisville` | Lewisville ebooks/audiobooks (cloudLibrary) | same, different library id |
| `lewisville-print` | Lewisville Public Library print (SirsiDynix Enterprise) | Atom feed for discovery, one AJAX call per record for copies/holds |
| `libby-fortworth` | Fort Worth ebooks/audiobooks (Libby/OverDrive) | OverDrive's unauthenticated "thunder" API |
| `libby-houston` | Houston ebooks/audiobooks (Libby/OverDrive) | same, different library key |
| `texas-theatre` | Texas Theatre, The Modern (Fort Worth) | page watcher (title appears on the site) |
| `angelika` | Angelika Dallas | Reading Cinemas API; its bearer token is handed out unauthenticated |
| `inwood` | Landmark Inwood | the site is Gatsby, and its static-query JSON is public |
| `cinemark` / `amc` | chain theaters (config per location) | schema.org ld+json on showtime pages, page-text fallback |
| `alamo` (off by default) | every Alamo Drafthouse in DFW | their market-wide JSON schedule feed |
| `advance-screenings` | free studio promo screenings across DFW | advancescreenings.com, which aggregates ~10 outlets incl. Gofobo |

Sources are isolated: one failing never kills the run; failures show in
the report. Add a source by editing `sources:` in `watchlist.yaml`; add
a new *kind* by dropping a file in `tracker/sources/` (subclass
`Source`, decorate with `@register`).

Every library card is its own source, because no two of these cities run
the same stack — there is no single "add a library" knob. Overlap is
cheap, though: notifications are one push per (book, track) — reading or
listening — so a title carried by four libraries in two formats you'd
read is announced once, and the report still shows which libraries have
it. A newly added library can only speak up by beating the shortest wait
a track has already reached.

Films work the same way, ranked on *where* instead of on a wait. Each
theatre has a `tier` in `watchlist.yaml` — `home` (Cinemark Denton 14),
`preferred` (any other Cinemark), `nearby` (a hop that stays out of the
metroplex), `other` (Dallas, Frisco, Fort Worth) — and a `distance_mi`
that orders theatres *within* one tier. A film speaks the first time
it's playing anywhere and afterwards only when it reaches a better tier,
so a wide release doesn't drip-feed a push a day as it works across the
metro. Theatres found in the same run share one push, best first.

Tiers rather than a mileage column because miles lie: Stonebriar is
nearer than Grapevine Mills and the worse drive, because it's Frisco. A
step between tiers is a different decision; a few miles isn't.

## Setup

```bash
cd media-tracker
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# phone push: install the ntfy app, subscribe to an unguessable topic
cp .env.example .env   # put the topic name in NTFY_TOPIC
set -a; source .env; set +a
```

## Use

### The web app (easiest)

```bash
python -m tracker web
```

Opens a dashboard in your browser: add titles (books are verified
against the live catalogs — you click the exact record you mean),
remove titles, run a check, browse sightings, and test each source with
one click (the "test" button shows exactly what the source returned —
paste that output into a Claude session to get scrapers fixed).

Phones on your home wifi can use it too: find your computer's IP
(System Settings → Wi-Fi → Details on a Mac) and open
`http://<that-ip>:8765`.

### The phone dashboard (read-only, from anywhere)

Every check run writes `docs/index.html` — a clean, phone-friendly
summary of new/current sightings, source health, and your watchlist.
Enable **GitHub Pages** (Settings → Pages → "Deploy from a branch" →
`/docs` folder) and that page gets a public URL that auto-updates after
every scheduled run — bookmark it on your phone.

### The diary (reading log, film log, lists)

The same Pages site carries three more surfaces, all backed by flat
files you can edit by hand:

| surface | data | built by |
|---|---|---|
| reading diary — calendar, flat list, per-book pages | `reading/log.json` | `python -m tracker reading` |
| film diary | `watching/log.json` (Letterboxd RSS sync) | `python -m tracker letterboxd` |
| ranked lists | `lists/*.yaml` (file order = rank) | `python -m tracker lists` |

Both logs can be written from the phone — `docs/reading/log.html` logs
sessions, `docs/lists/edit.html` edits lists — via the GitHub Contents
API with a fine-grained PAT kept in the browser's local storage.

**How these pages render, and why it differs from the dashboard.** The
watchlist dashboard is *machine*-authored: the scraper writes it on a
cron, ntfy tells you when something happened, and nobody sits waiting on
the page. Static generation is exactly right there.

The diary and lists are *human*-authored — you type something and
immediately want to see it — so a CI run plus a Pages deploy (~56s
measured, plus Pages' ten-minute `cache-control`) is the wrong shape.
Those surfaces are therefore split:

- CI does only what a browser **cannot**: the iTunes / Open Library /
  Apple Books lookups behind covers and page counts. It publishes
  `docs/data/{diary,lists}.json` and ~1KB page shells.
- `docs/assets/diary.js` renders the calendar, flat list, book pages and
  lists grid in the browser, computing per-day deltas and streaks itself
  — it has to, because it also folds in an edit you saved seconds ago
  that no build has seen.
- A save is stashed in local storage and shown immediately (marked
  "syncing"); the overlay retires itself once a build contains it.

If you change `diary.js`, note that it is the *only* renderer for those
pages — deliberately, to avoid maintaining the same layout in two
languages. `docs/404.html` covers the gap where a book logged seconds
ago has no shell yet.

### Book-finished consensus reports

Marking a book finished from the phone researches its critical consensus
and commits the report — one per read, fully event-driven:

```
phone edit → GitHub commit to reading/log.json
  → .github/workflows/book-finished.yml
     detect: before/after diff → newly-finished books, minus any that
             already have a report, capped → one job-matrix entry each
     report: claude-code-action runs .claude/skills/book-consensus,
             writes reports/<slug>.md, commits it back
```

**Dedupe is the committed file**, not a state file: a book has been
reported iff `reports/<slug>.md` exists and is non-empty. That makes every
run idempotent and the recovery path trivial — a `workflow_dispatch` with
no `before_sha` diffs every finished book against nothing, and everything
already reported drops out. An empty file counts as absent, so a run that
died mid-write doesn't permanently block the book.

Because that recovery path re-offers ~150 already-finished books, `--limit`
(default 2) is what keeps it bounded; deferred books are named in a
workflow annotation and picked up by the next run, `--limit` at a time.

Setup: the `CLAUDE_CODE_OAUTH_TOKEN` repository secret, from
`claude setup-token` — a subscription token, so runs bill to the Claude
plan rather than API credits.

Debugging entry points:

- `python -m tracker book-finished --diff-before OLD.json` — print the job
  matrix as JSON without touching Actions. Get a real base with
  `git show <sha>:reading/log.json > OLD.json`.
- Manual `workflow_dispatch` with `dry_run=true` — runs `detect` only, so
  it lists what *would* be reported and skips the research entirely.
- An *empty* list when the old snapshot was empty means broken wiring, not
  a no-op, and the command exits 1 saying so.

Two known limits:

- **Local (non-phone) edits to `reading/log.json` don't trigger it** — the
  event source is GitHub, and almost all edits are phone-based.
- **The legacy review tier is mostly unreachable.** theguardian.com,
  nytimes.com, the TLS and the New Yorker refuse the fetcher's user agent,
  and thebookerprizes.com and Foreword Reviews return 403. The skill is
  built to privilege exactly that tier, so reports lean on independent
  critics, trade reviews and whatever the publisher's page carries as
  blurbs. `.claude/skills/book-consensus/reference/calibration/` holds two
  earlier runs on the same book made through a real browser, for contrast.

Nothing downstream reads `reports/` yet — whether a report surfaces as a
page on the book's diary entry or as an artifact in the Claude app is
undecided (see TODO.md).

#### The superseded Hermes relay

`tracker/hermes_relay.py` and the workflow's `relay` job are the previous
consumer: the same diff published one signed JSON payload per book to a
dedicated ntfy topic, which a launchd daemon on this Mac streamed and
POSTed to Hermes at `localhost:8644`. Actions can't reach a localhost
endpoint, which is the only reason the ntfy hop and the HMAC signing
existed.

It is kept until deliberately retired, so both legs currently fire on one
finish. The diff itself now lives in `tracker/book_finished.py` and is
re-exported here, so retiring the relay is a pure deletion: this module,
the `relay` job, the `HERMES_*` entries in `.env.example`,
`state/hermes-sent.json`, `tests/test_hermes_relay.py`, and

```bash
launchctl bootout gui/$UID ~/Library/LaunchAgents/dev.media-tracker.hermes-relay.plist
```

(without which the daemon keeps retrying a dead endpoint into
`~/Library/Logs/hermes-relay.err.log`).

### The CLI

```bash
python -m tracker list                      # show parsed watchlist + sources
python -m tracker add book "nickel boys"    # guardrailed add: searches the live
                                            # catalogs, you pick the exact record,
                                            # canonical IDs (isbn/bib_id) are stored
python -m tracker add movie "the substance" --year 2024
python -m tracker check                     # full run: report + state + push
python -m tracker check --dry-run           # look, don't touch
python -m tracker probe --source cloudlibrary   # raw responses, for debugging
python -m tracker book-finished --diff-before OLD.json   # which finished books
                                            # still need a consensus report
```

Each *decision* notifies **once**: `state/state.json` remembers what
you've been told (pruned after 180 days) — every sighting in `seen` for
the report and dashboard, and the coarser keys that actually drive
pushes in `media` (book, track) and `venues` (film, theatre). `state/report.md` is the
browsable record of the latest run, ending with **Still looking** —
watchlist entries that have never matched anywhere, with how long
they've been waiting. That list is a status, not an error: a book the
libraries haven't bought and a forthcoming film both live there, and
most entries leave it on their own. Only after 90 fruitless days does
it suggest checking the spelling.

## First run: validate the scrapers

Scraping targets drift, and two of these sites bot-protect datacenter
IPs. From your own machine run:

```bash
python -m tracker probe
```

per source. Expected outcomes: `denton-library` and `alamo` should just
work; `cloudlibrary` may need its endpoint chain re-pointed (the adapter
is built so that's a one-line fix — send me the probe output);
`cinemark` will tell you whether structured data or only the text
fallback is available, and whether your theater URLs are right. `amc`
is expected to fail — every theatre page has returned a hard Cloudflare
403 since 2026-09-02, and the replacement is AMC's official API (see
TODO.md), not anything fixable in the page scraper.

## If the Cinemark/AMC probes fail

Those two are the most load-bearing sources and the most likely to
block scrapers. The agreed escalation ladder, cheapest first — decide
after seeing real probe output, not before:

1. **Probe from home wifi** and codify whichever direct endpoint works
   (bot walls usually target datacenter IPs, not homes).
2. **Headless-browser fallback** — an invisible Chrome loads the page
   like a human. Free; costs ~250MB of disk and occasional 15-minute
   fixes when the chains change defenses.
3. **Aggregator adapter (SerpAPI)** — one paid-service integration
   (free tier likely covers 2 checks/day) returning Google's showtime
   data for *every* nearby theater. Most durable option.
4. **AMC's official API** — free developer key, but approval is slow
   and not guaranteed. Worth submitting in parallel if 1–2 struggle.
5. **Run just the blocked sources from a home machine** on cron:
   `python -m tracker check --source cinemark` — a deployment fix,
   not a code fix.

## Scheduling

The workflow in `.github/workflows/media-tracker.yml` runs the check
~8am and ~6pm Central and commits state back. To finish setup:

1. Add the `NTFY_TOPIC` Actions secret (Settings → Secrets → Actions).
2. Run the workflow manually once (workflow_dispatch) and check the
   report; if a source 403s from GitHub's IPs, disable it there and run
   just that source from a home machine via cron:
   `python -m tracker check --source denton-library`.

## Deliberately not built (yet)

A database, hold-placement/checkout automation, email digests, more
chains/metros. The scrapers had to prove themselves first; the diary,
lists and imports (Bookmory, Letterboxd) landed once they had.
