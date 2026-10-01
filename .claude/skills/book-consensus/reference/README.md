# Provenance: rescued from Hermes before the uninstall (2026-10-01)

Everything here came off `~/.hermes` or `~/Documents/critical-consensus`
before Hermes was purged from the machine. None of it is on the live code
path — it is reference material the port still needs.

| File | Was | Why it was kept |
| --- | --- | --- |
| `render_report.py` | `~/.hermes/skills/book-consensus/scripts/` | Stage 2 of the plan ports its `convert()` (lines 25–129) into `tracker/consensus_gen.py`: a dependency-free markdown→HTML converter that handles the report schema's subset incl. GFM tables. Feeds either surface (artifact or leaf page). The rest of the file (TEMPLATE, `stats`, `write_index`) is Hermes-shaped and not being ported. |
| `SKILL.hermes-original.md` | `~/.hermes/skills/book-consensus/SKILL.md` | The authored original, before the port stripped its Hermes-only parts (the Step 5 delivery branch, `desktop_preview`, the read-only-webhook caveat, the profile/model "open refinements"). Kept for provenance and for the calibration anchor's original wording. |
| `calibration/may-we-feed-the-king.{strong,weak}.md` | `~/.hermes/cache/scratch/` | Two prior runs on the same book, graded. The reference for "what good looks like" when tuning the ported skill, and a direct A/B against `prior-runs/may-we-feed-the-king.claude-port.md`. |
| `prior-runs/exit-party.md` | `~/Documents/critical-consensus/` | A full Hermes run on a book that is still in the pipeline's queue — the other available A/B. |
| `prior-runs/may-we-feed-the-king.claude-port.md` | `reports/` (this repo) | The ported skill's first run, 2026-10-01, done inline in a session rather than by CI. Held out of `reports/` deliberately so the book stays unreported and can be re-run against the revised skill — it is the third leg of the A/B, not a delivered report. |
| `prior-runs/may-we-feed-the-king.html` | `~/Documents/critical-consensus/` | The rendered Hermes report. Kept because no `.md` was ever written for it (the webhook route delivered to the log, not to a file), so this HTML is the only copy of that particular run. |

## Two things these files settle

**The legacy-tier access problem is tooling, not prompting.** The strong run
cites `theguardian.com`, `observer.co.uk`, `irishtimes.com` and the Sunday
Times — all of which refuse the current fetcher's user agent or 403. Hermes
drove a real browser. Any judgement about the ported skill's output quality
has to account for that difference first.

**The Booker longlist/shortlist conflict has a documented resolution.**
The ported skill's run flags it as unresolved because
`thebookerprizes.com` 403s. The weak run reached the catalogue and recorded:
the Booker's own catalogue says *Longlisted*, Transit Books' page says
*Shortlisted for the 2026 Booker Prize*, and it treated the Booker catalogue
as controlling. The strong run says longlisted **then** shortlisted.

## Do not move these into `reports/`

`reports/<slug>.md` is the pipeline's dedupe key — `book_finished.has_report()`
reads exactly that path. Dropping `exit-party.md` there would make CI believe
exit-party is already reported and skip it. That is why the prior runs live
here instead.
