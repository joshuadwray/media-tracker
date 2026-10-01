# Consensus reports

One file per finished book: `reports/<slug>.md`, where `<slug>` is the
book's slug in `reading/log.json`. Written by the `report` job in
`.github/workflows/book-finished.yml`, which runs
`.claude/skills/book-consensus` against each newly-finished book.

**This directory is the pipeline's dedupe key.** A book counts as reported
iff `reports/<slug>.md` exists and is non-empty — see
`book_finished.has_report()`. There is no state file; adding or deleting a
file here is how you mark a book done or re-queue it.

So:

- To re-run a book, delete its file. The next finish, or a
  `workflow_dispatch` with no `before_sha`, picks it up.
- Don't park sample runs, drafts or rescued reports here — they would make
  CI skip a book that was never really reported. Those live in
  `.claude/skills/book-consensus/reference/prior-runs/`.
- A zero-byte file counts as absent, so a run that dies mid-write doesn't
  block the book permanently.

Nothing reads these files yet; how a report surfaces is still open (TODO.md).
