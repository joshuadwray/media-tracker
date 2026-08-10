# media-tracker

Watches your watchlist so you don't have to: periodically checks library
catalogs for books and DFW-area theaters for movie showtimes, and sends
a phone push (via [ntfy.sh](https://ntfy.sh)) exactly once per new
sighting — a book appearing or becoming available, a showtime landing.

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
| `denton-library` | Denton Public Library (BiblioCommons) | parses the JSON embedded in catalog search pages |
| `cloudlibrary` | cloudLibrary ebooks/audiobooks | unauthenticated web-patron search API |
| `texas-theatre` | Texas Theatre | page watcher (title appears on the site) |
| `cinemark` / `amc` | chain theaters (config per location) | schema.org ld+json on showtime pages, page-text fallback |
| `alamo` (off by default) | every Alamo Drafthouse in DFW | their market-wide JSON schedule feed |

Sources are isolated: one failing never kills the run; failures show in
the report. Add a source by editing `sources:` in `watchlist.yaml`; add
a new *kind* by dropping a file in `tracker/sources/` (subclass
`Source`, decorate with `@register`).

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
```

Each sighting notifies **once**: `state/state.json` remembers what
you've been told (pruned after 180 days). `state/report.md` is the
browsable record of the latest run, including watchlist entries that
have never matched anywhere (likely typos — re-add them with
`tracker add`).

## First run: validate the scrapers

Scraping targets drift, and two of these sites bot-protect datacenter
IPs. From your own machine run:

```bash
python -m tracker probe
```

per source. Expected outcomes: `denton-library` and `alamo` should just
work; `cloudlibrary` may need its endpoint chain re-pointed (the adapter
is built so that's a one-line fix — send me the probe output);
`cinemark`/`amc` will tell you whether structured data or only the text
fallback is available, and whether your theater URLs are right.

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
