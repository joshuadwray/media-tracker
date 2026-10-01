---
name: book-consensus
description: Aggregate cited critical consensus on a book release.
version: 0.1.0
author: Veto (user), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [books, criticism, research, aggregation]
---

# Book Critical-Consensus Aggregation

Aggregates professional critical consensus on a specific book release into a
structured, fully cited report that separates what critics say from what the
agent thinks. Designed to run on lighter models: follow the procedure and
schema exactly; do not freelance.

## When to Use

- User asks for critical consensus, a review roundup, or "what do critics
  think of {book}"
- User asks for in-depth discussion of a book's themes in published criticism

Don't use for: retailer or reader-only sentiment, a single review summary,
author interviews as a standalone ask.

## Core Contract (read first)

1. **Aggregate, don't review.** Every substantive claim about the book's
   content is attributed to a named outlet and critic. The agent's own voice
   appears ONLY in the labeled "Synthesis" section (or nowhere). A reader
   must be able to tell whose reading every sentence is.
2. **No unfetched citations.** Every source must come from a page actually
   fetched during the run (web_search + web_extract). Anything remembered,
   guessed, or "probably exists" goes to the Unverified Leads section —
   including named-outlet claims that feel plausible. Hallucinated criticism
   is the worst failure mode of this task.
3. **Report, don't perform.** Write like an editor summarizing reviews: plain
   declarative sentences, quotes carry the vividness. Forbidden: standalone
   aphorism paragraphs, "That's crucial."-style zingers, bolded pseudo-
   epiphanies, LinkedIn cadence. The book is literature, not content.
4. **Length budget: ≤1,500 words of prose.** Detail belongs in the source
   table, not the narrative. Never trade attribution for brevity.

## Inputs

Required from the user: TITLE, AUTHOR, YEAR (ask if missing).
Optional overrides: outlet tiers, minimum deep sources N (default 4).

## Research budget (hard limits — read before Step 2)

Churning is this task's worst failure mode: an unbounded discovery pass keeps
re-searching after marginal returns and re-fetching dead URLs, burning tokens
without shipping. These limits are part of the procedure, not advice, and they
override any instinct to be thorough.

- **≤4 discovery passes** (Step 2), **≤16 `web_extract` calls**, **≤24 research
  tool calls total**. Count them as you go.
- **Stop the instant both hold:** ≥6 substantive sources AND the last pass
  produced no new substantive source. Do not run one more pass "to be thorough"
  — that pass is exactly where the bloat lives.
- **A failed fetch is final.** If `web_extract` errors or times out on a URL, put
  it in Unverified Leads and move on immediately. Never retry that URL, and never
  re-attempt it through a different extractor, cache, or alternate host.
- **Hitting a limit is a normal ending, not a failure.** Write the report from
  what is verified. A shorter report with live citations beats a longer one that
  never ships.
- When a limit cuts research short, say so in the report's Unverified Leads line
  ("discovery stopped at the N-pass budget") so the reader knows the shape.

## Procedure

### Step 1 — Fact backbone

Search and fetch to verify: publisher(s) + dates, prize wins/shortlists,
author's prior work. Each backbone fact carries its source URL in the report.
If two sources conflict, note the conflict explicitly; do not silently pick.

Done when: every backbone claim has a fetched source.

### Step 2 — Review discovery (multi-pass, varied angles)

Run web_search in at least 3 passes with different query angles:
1. `"{TITLE}" {AUTHOR} review`
2. `"{TITLE}" {AUTHOR} interview OR essay OR podcast`
3. `"{TITLE}" Booker OR prize OR longlist OR shortlist (or the relevant prize)
   + reading guide`
4. Additional passes as results suggest (long-tail blogs, podcasts with
   transcripts, year-end lists).

Stop condition: a full pass yields no NEW substantive source, and you have
≥ N deep/medium sources. Minimum 6 substantive sources overall. Long-tail
finds (independent critics, thematic essays) are often the deepest material —
do not stop at pass 1.

Done when: stop condition met; you have URLs for every candidate source.

### Step 3 — Fetch, verify, depth-tag

web_extract every candidate source before citing it. For each:
- Record outlet, critic (verify name spelling against the fetched page),
  exact date where the page shows one, URL.
- Tag depth by the engagement test: **deep** = discusses specific arguments,
  scenes, structure, or themes at length / quotes the text; **medium** =
  some thematic engagement but verdict-driven; **shallow** = impressions
  only ("lyrical," "dreamlike," "stunning").
- Mark paywalled pieces: `paywalled — quoted via {where the quote appeared}`.
- Drop anything you could not fetch or verify. Move promising-but-unfetched
  leads to Unverified Leads.
- Quarantine reader sentiment (Goodreads, StoryGraph, Reddit): it may appear
  only in its own labeled subsection, never mixed into critical consensus.

Done when: every cited source has a fetched URL and a depth tag.

### Step 4 — Synthesis in fixed schema

Write the report in exactly this order:

1. **Where the book stands** — verified fact backbone, 3–6 sentences.
2. **The consensus** — 1 short paragraph: the shape of critical opinion,
   attributed examples.
3. **Source table** — one row per source: outlet | critic | date | depth |
   what THIS source says (its claim or reading, quoted or closely
   paraphrased — never the agent's interpretation). Sources without URLs
   never appear here.
4. **In-depth readings** — only deep sources: 1 short attributed paragraph
   each (core claim + strongest quote).
5. **Fault lines** — points of critical disagreement, each attributed.
   If no major outlet dissents, say so and state where the criticism lives
   instead (taste vs. execution).
6. **Reader sentiment** (optional, labeled) — quarantine zone.
7. **Read-in-full shortlist** — up to 4 pieces, one clause each on why,
   each with an access note (free / paywalled).
8. **Synthesis** (optional, explicitly labeled) — the agent's own
   connections, clearly marked as such.
9. **Unverified leads** — anything mentioned but never fetched.

### Step 5 — Deliver in reading form

**First check what the session can actually do.** The webhook route's toolset is
read-only (`web_search`, `web_extract`, `vision_analyze`, `clarify` only), so a
run triggered by the media-tracker webhook cannot write files or run scripts.
Never claim a file was written when there is no `write_file`/`terminal` tool —
and never spend turns apologising for it either. Read the surface and pick one:

**A. No file/terminal tools (webhook-triggered runs, read-only surfaces).**
The chat reply IS the report. Deliver the complete markdown report in schema
order, with nothing before it and no sign-off after it. Write the prose tighter
rather than longer — a read-only run has no file to absorb the overflow, so the
≤1,500-word budget binds harder here, not less.

**B. File tools available (desktop app, CLI, cron).** Write both files, then
open the HTML:

```
OUT=~/Documents/critical-consensus   # one report per book, named for the slug
python3 ~/.hermes/skills/book-consensus/scripts/render_report.py REPORT.md OUT/SLUG.html
```

- Desktop app: save the .md, render the .html, and open it with the
  `desktop_preview` tool (`action=open`, `url` = the absolute .html path). Also
  print `MEDIA:<abs path>` so it lands as a file card. Chat gets a summary only:
  the consensus shape, the fault line, the shortlist — never the full table.
- The renderer regenerates `index.html` and a per-report `.meta.json`
  (title, source count, deep count) in the same directory.

The renderer is dependency-free and handles the schema's markdown subset
(headings, bold/italic, links, ordered/bulleted lists, GFM tables with the depth
column highlighted). Restyle it in
`~/.hermes/skills/book-consensus/scripts/render_report.py` — edit the template
only, not the parser.

## Pitfalls

- **Fusing synthesis with consensus** (the ChatGPT failure): readers cannot
  un-tangle them. When in doubt, attribute or cut.
- **Tables that smuggle in interpretation** (the ChatGPT table failure):
  cells describe sources; they do not editorialize.
- **Untraceable claims** (the Gemini failure): real outlets named, real-
  sounding specifics, but no way to check which claim came from where.
  URLs alone don't fix this — every substantive claim must be attributed
  inline to its fetched source.
- **Chat pastes strip URLs:** if delivering in chat, also save the report as a
  markdown file so links survive, and render the HTML for reading.
- **Don't read a long report in a chat window.** The source table is the report's
  spine and chat renders it badly; the preview pane (desktop) or the attached
  file is the reading surface.
- **Stopping early:** the best material (independent critics' essays,
  prize reading guides, author interviews) sits in passes 2–4.
- **Critic-name drift:** two outputs gave "Clemmie Read" and "Clemme Reid."
  Verify spelling on the fetched page.

## Open refinements (agreed with the user — not yet in the procedure above)

- **Model home.** These runs currently inherit the profile-wide default
  (`model.default` in config.yaml), which is not a deliberate choice for this
  task. Pick a cheap lightweight model as the skill's home and A/B the
  candidates on a fresh book before locking one in. Levers: a webhook route
  accepts `profile:` (so the skill can run under a dedicated profile whose
  default model is the chosen one), and cron jobs accept per-job
  `model`/`provider` overrides. A webhook route itself has **no** model field
  — verified by grep; don't go hunting for one.
- **Length.** Output is well organised but bloats past the ≤1,500-word budget
  because nothing enforces it. Count the rendered report's words before
  delivering and cut the prose back to budget — trim narrative sections, never
  the source table, which is the report's spine.

## Verification

Before delivering, check: every URL in the report was fetched this run;
no prose section exceeds the budget; schema order 1–9 holds; each table row
is a description of its source; the report answers "what do critics
actually argue" — not just "do they like it."

## Calibration anchor (what good looks like)

A strong run (Kimi-style) reads like: attributed readings per outlet
("Literary Review went furthest theoretically: Perry offers 'a conception of
history in which nothing is absolute…'"), explicit depth discrimination, a
fault-line finding ("no major legacy outlet dissented — the criticism lives
at the level of taste rather than execution"), and a read-in-full shortlist
with access notes (paywalled/free). Discovered long-tail gold: independent
long-form essays that legacy coverage never surfaces.

Original seed prompt for reference: "Let's discuss a recent book release,
{TITLE}. I'd like you to aggregate sharp critical consensus — prefer legacy,
mainstream outlets & additionally in-depth discussions of the book and its
themes. Some reviews, even from legacy outlets, may default toward
impressions without delving into depth; the depth of engagement with content
is what we're looking for more."
