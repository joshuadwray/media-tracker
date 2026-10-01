---
name: book-consensus
description: Aggregate cited critical consensus and in-depth published discussion of a book into a structured report. Use when asked for critical consensus, a review roundup, "what do critics think of {book}", or in-depth discussion of a book's themes in published criticism. Invoked per finished book from the reading log.
allowed-tools: WebSearch, WebFetch, Write, Read
---

# Book Critical-Consensus Aggregation

Aggregates professional critical consensus on a specific book release into a
structured, fully cited report that separates what critics say from what the
agent thinks. Follow the procedure and schema exactly; do not freelance.

## When to Use

- User asks for critical consensus, a review roundup, or "what do critics
  think of {book}"
- User asks for in-depth discussion of a book's themes in published criticism
- A book is newly marked `finished` in `reading/log.json`

Don't use for: retailer or reader-only sentiment, a single review summary,
author interviews as a standalone ask.

## Core Contract (read first)

1. **Aggregate, don't review.** Every substantive claim about the book's
   content is attributed to a named outlet and critic. The agent's own voice
   appears ONLY in the labeled "Synthesis" section (or nowhere). A reader
   must be able to tell whose reading every sentence is.
2. **No unfetched citations.** Every source must come from a page actually
   fetched during the run (WebSearch + WebFetch). Anything remembered,
   guessed, or "probably exists" goes to the Unverified Leads section —
   including named-outlet claims that feel plausible. Hallucinated criticism
   is the worst failure mode of this task.
3. **Report, don't perform.** Write like an editor summarizing reviews: plain
   declarative sentences, quotes carry the vividness. Forbidden: standalone
   aphorism paragraphs, "That's crucial."-style zingers, bolded pseudo-
   epiphanies, LinkedIn cadence. The book is literature, not content.
4. **Length budget: ≤1,500 words of prose**, counted before delivering (the
   source table doesn't count). Over budget, trim narrative sections — never
   the table, and never trade attribution for brevity.

## Inputs

Required: TITLE, AUTHOR, YEAR (ask if missing). Optional: SLUG (the
`reading/log.json` slug; derive it if absent — lowercase, non-alphanumerics to
hyphens), outlet tiers, minimum deep sources N (default 4).

## Research budget (hard limits — read before Step 2)

Churning is this task's worst failure mode: an unbounded discovery pass keeps
re-searching after marginal returns and re-fetching dead URLs, burning tokens
without shipping. These limits are part of the procedure, not advice, and they
override any instinct to be thorough.

- **≤4 discovery passes** (Step 2), **≤16 `WebFetch` calls**, **≤24 research
  tool calls total**. Count them as you go.
- **Stop the instant both hold:** ≥6 substantive sources AND the last pass
  produced no new substantive source. Do not run one more pass "to be thorough"
  — that pass is exactly where the bloat lives.
- **A failed fetch is final.** If `WebFetch` errors or times out on a URL, put
  it in Unverified Leads and move on immediately. Never retry that URL, and never
  re-attempt it through a different extractor, cache, or alternate host.
  Exception: a cross-host **redirect notice** is not a failure — `WebFetch`
  returns the target instead of following it, so call it once more with the
  redirect URL. That second call counts against the budget.
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

Run WebSearch in at least 3 passes with different query angles:
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

`WebFetch` every candidate source before citing it. **`WebFetch` answers a
prompt against the page rather than handing back the raw text**, so ask it for
exactly what the report needs, in one call per URL:

> "Give me: the critic's byline exactly as printed, the publication date shown
> on the page, this review's central argument about the book, and two verbatim
> sentences that carry it. If the page is a paywall stub or the book isn't the
> subject, say so."

For each source record outlet, critic (spelling as printed), exact date where
the page shows one, URL. Then:
- Tag depth by the engagement test: **deep** = discusses specific arguments,
  scenes, structure, or themes at length / quotes the text; **medium** =
  some thematic engagement but verdict-driven; **shallow** = impressions
  only ("lyrical," "dreamlike," "stunning").
- Mark paywalled pieces: `paywalled — quoted via {where the quote appeared}`.
  A paywall stub is a failed fetch for citation purposes: it goes to Unverified
  Leads unless another fetched page carries the quote.
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

### Step 5 — Write the file

`Write` the report to `reports/<SLUG>.md` in the repository, with a heading and
a provenance line at the top, then the schema sections 1–9:

```markdown
# {TITLE} — {AUTHOR} ({YEAR})

*Critical consensus · compiled {YYYY-MM-DD} · {N} sources fetched, {D} deep*
```

The file is the deliverable. In chat, print only a short summary — the
consensus shape, the fault line, and the shortlist — plus the file path. Never
paste the full report or the source table into chat: markdown tables render
badly there, chat pastes strip URLs, and the table is the report's spine.

Nothing downstream reads this file yet, so it is also the thing to judge the
run by. Don't render HTML, don't publish, don't notify.

## Pitfalls

- **Fusing synthesis with consensus** (the ChatGPT failure): readers cannot
  un-tangle them. When in doubt, attribute or cut.
- **Tables that smuggle in interpretation** (the ChatGPT table failure):
  cells describe sources; they do not editorialize.
- **Untraceable claims** (the Gemini failure): real outlets named, real-
  sounding specifics, but no way to check which claim came from where.
  URLs alone don't fix this — every substantive claim must be attributed
  inline to its fetched source.
- **Trusting a summary's paraphrase as a quote.** `WebFetch` returns a model's
  reading of the page; a sentence only goes in quotation marks when the fetch
  returned it as verbatim text. Otherwise paraphrase and attribute.
- **Stopping early:** the best material (independent critics' essays,
  prize reading guides, author interviews) sits in passes 2–4.
- **Critic-name drift:** two earlier runs gave "Clemmie Read" and "Clemme Reid."
  Verify spelling on the fetched page.

## Verification

Before delivering, check: every URL in the report was fetched this run;
prose is under the 1,500-word budget (count it); schema order 1–9 holds; each
table row is a description of its source; the report answers "what do critics
actually argue" — not just "do they like it."

## Calibration anchor (what good looks like)

A strong run reads like: attributed readings per outlet ("Literary Review went
furthest theoretically: Perry offers 'a conception of history in which nothing
is absolute…'"), explicit depth discrimination, a fault-line finding ("no major
legacy outlet dissented — the criticism lives at the level of taste rather than
execution"), and a read-in-full shortlist with access notes
(paywalled/free). Discovered long-tail gold: independent long-form essays that
legacy coverage never surfaces.

Original seed prompt for reference: "Let's discuss a recent book release,
{TITLE}. I'd like you to aggregate sharp critical consensus — prefer legacy,
mainstream outlets & additionally in-depth discussions of the book and its
themes. Some reviews, even from legacy outlets, may default toward
impressions without delving into depth; the depth of engagement with content
is what we're looking for more."
