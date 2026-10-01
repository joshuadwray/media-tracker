"""Structural tests for .github/workflows/book-finished.yml — the live
trigger/dispatch behavior can't be unit-tested, so its wiring is asserted
here with PyYAML and the manual smoke test covers the rest. Note that
PyYAML parses the YAML 1.1 boolean-ish key `on:` as boolean `True`."""
import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

WF = Path(__file__).resolve().parent.parent / ".github/workflows/book-finished.yml"


def _wf():
    return yaml.safe_load(WF.read_text())


def test_workflow_file_exists_and_parses():
    data = _wf()
    assert isinstance(data, dict)


def test_push_trigger_paths_and_branches():
    """The event source is a phone edit committing reading/log.json on
    main, and nothing else: not other branches, not other files."""
    trig = _wf()[True]   # YAML 1.1: `on:` parses as boolean True
    assert trig["push"]["branches"] == ["main"]
    assert trig["push"]["paths"] == ["reading/log.json"]


def test_dispatch_inputs_present():
    trig = _wf()[True]
    inputs = trig["workflow_dispatch"]["inputs"]
    assert inputs["dry_run"]["type"] == "boolean"
    assert inputs["before_sha"]["type"] == "string"
    assert inputs["before_sha"].get("required") in (False, None)


def test_fetch_depth_zero():
    """The diff needs the before SHA; a shallow checkout doesn't have it."""
    job = _wf()["jobs"]["relay"]
    checkout = next(s for s in job["steps"] if "checkout" in str(s.get("uses", "")))
    assert checkout["with"]["fetch-depth"] == 0


def test_old_snapshot_is_event_type_aware():
    """github.event.before exists only on push; on dispatch it is absent
    (empty string), so the all-zeros guard alone never fires there. The
    step must branch on event name, guard the all-zeros SHA, fall back to
    the before_sha input, and tolerate a `git show` failure. Event
    expressions are read via env: indirection (quoted shell variables),
    never interpolated straight into the script body."""
    job = _wf()["jobs"]["relay"]
    step = next(s for s in job["steps"]
                if "old log snapshot" in (s.get("name") or "").lower())
    text = step["run"]
    whole = WF.read_text()
    # the values arrive through env vars, so the script quotes them
    envs = step["env"]
    assert envs["EVENT_NAME"] == "${{ github.event_name }}"
    assert envs["BEFORE_SHA"] == "${{ github.event.before }}"
    assert envs["INPUT_SHA"] == "${{ inputs.before_sha }}"
    assert '"$EVENT_NAME"' in text and '"$BEFORE_SHA"' in text \
        and '"$INPUT_SHA"' in text
    # no raw interpolation of event expressions inside the script body
    assert "${{ github.event" not in text and "${{ inputs." not in text
    assert "0000000000000000000000000000000000000000" in text
    # git show failure tolerated (path absent in the before commit)
    assert "git show" in text and "2>/dev/null" in text
    # dispatch-without-before_sha is warned about, and empty-old is stated
    assert "::warning::" in text and "EMPTY" in text
    assert "workflow_dispatch without before_sha" in whole


def test_topic_comes_from_secret():
    """The topic is the trust boundary — unguessable and never plaintext."""
    job = _wf()["jobs"]["relay"]
    publish = next(s for s in job["steps"]
                   if "diff" in (s.get("name") or "").lower())
    env_block = publish["env"]
    assert env_block["HERMES_NTFY_TOPIC"] == "${{ secrets.HERMES_NTFY_TOPIC }}"
    # and it's not hardcoded anywhere in the file
    assert "secrets.HERMES_NTFY_TOPIC" in WF.read_text()
    assert "HERMES_NTFY_TOPIC:" in WF.read_text()


def test_concurrency_group_serializes():
    """Overlapping runs (a batch finish, or a dispatch racing a push)
    serialize rather than cancelling a diff mid-flight; the relay's
    slug dedupe covers any residual race."""
    conc = _wf()["concurrency"]
    assert conc["group"] == "book-finished"
    assert conc["cancel-in-progress"] is False


def test_diff_command_wired():
    """The publish step runs the relay's diff mode against the snapshot
    the previous step computed, honoring dry_run."""
    job = _wf()["jobs"]["relay"]
    step = next(s for s in job["steps"]
                if "Diff and publish" in (s.get("name") or ""))
    script = step["run"]
    assert "--diff-before /tmp/log-old.json" in script
    assert "--publish" in script and "--dry-run" in script
    assert "inputs.dry_run" in script


def test_the_only_writeback_is_the_report_job():
    """The report job commits reports/<slug>.md; nothing else writes. The
    loop stays closed for two independent reasons — its commits use
    GITHUB_TOKEN, which triggers no workflows, and the push trigger is
    scoped to reading/log.json, which a report commit never touches."""
    jobs = _wf()["jobs"]
    for name in ("relay", "detect"):
        text = json.dumps(jobs[name])
        assert "git push" not in text and "git commit" not in text
    assert "git push" in json.dumps(jobs["report"])
    assert _wf()[True]["push"]["paths"] == ["reading/log.json"]
    # read-only at the workflow level, so a job must opt in explicitly
    assert _wf()["permissions"] == {"contents": "read"}


# --------------------------------------------- detect -> report (live path)

def test_detect_exposes_the_matrix_as_job_outputs():
    job = _wf()["jobs"]["detect"]
    assert job["outputs"]["books"].startswith("${{ steps.diff.outputs.books")
    assert job["outputs"]["count"].startswith("${{ steps.diff.outputs.count")
    checkout = next(s for s in job["steps"] if "checkout" in str(s.get("uses", "")))
    assert checkout["with"]["fetch-depth"] == 0   # the diff needs the before SHA


def test_detect_passes_the_limit_through_env_with_a_default():
    """Repo convention: event/input values arrive via env:, quoted — never
    interpolated into the shell body. The default keeps a push (where the
    input is absent) bounded."""
    step = next(s for s in _wf()["jobs"]["detect"]["steps"]
                if s.get("id") == "diff")
    assert step["env"]["LIMIT"] == "${{ inputs.limit }}"
    assert '"${LIMIT:-2}"' in step["run"]
    assert "--github-output" in step["run"]


def test_report_job_is_gated_and_serialized():
    job = _wf()["jobs"]["report"]
    assert job["needs"] == "detect"
    assert "needs.detect.outputs.count != '0'" in job["if"]
    assert "inputs.dry_run != true" in job["if"]
    assert job["strategy"]["fail-fast"] is False   # one book must not cancel others
    assert job["strategy"]["max-parallel"] == 1    # they all push to main
    assert job["strategy"]["matrix"]["book"] == \
        "${{ fromJSON(needs.detect.outputs.books) }}"


def test_report_job_permissions_are_scoped():
    job = _wf()["jobs"]["report"]
    assert job["permissions"]["contents"] == "write"   # commits the report
    assert job["permissions"]["id-token"] == "write"   # action's App auth
    # the workflow default stays read-only, so only this job can write
    assert _wf()["permissions"]["contents"] == "read"


def test_action_is_pinned_and_uses_the_subscription_token():
    """A subscription OAuth token, not an API key: runs bill to the plan.
    Pinned to @v1 rather than @beta or a floating ref."""
    step = next(s for s in _wf()["jobs"]["report"]["steps"]
                if "claude-code-action" in str(s.get("uses", "")))
    assert step["uses"] == "anthropics/claude-code-action@v1"
    assert step["with"]["claude_code_oauth_token"] == \
        "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"
    assert "anthropic_api_key" not in step["with"]


def test_prompt_invokes_the_repo_skill_with_the_matrix_book():
    step = next(s for s in _wf()["jobs"]["report"]["steps"]
                if "claude-code-action" in str(s.get("uses", "")))
    prompt = step["with"]["prompt"]
    assert prompt.startswith("/book-consensus ")
    for field in ("title", "author", "year", "slug"):
        assert f"matrix.book.{field}" in prompt
    args = step["with"]["claude_args"]
    assert "--max-turns" in args       # bounded run
    assert "WebSearch" in args and "WebFetch" in args and "Write" in args


def test_repo_skill_exists_for_that_prompt():
    """The prompt is a repo skill, so checkout must actually carry it."""
    skill = (WF.parent.parent.parent
             / ".claude/skills/book-consensus/SKILL.md")
    assert skill.exists()
    assert "name: book-consensus" in skill.read_text()


def test_commit_step_reapplies_the_report_after_the_reset():
    """build-lists.yml can reset and regenerate; a researched report
    cannot be regenerated, so it must be copied aside and restored —
    otherwise the rebase-by-reset silently discards the run's only output."""
    step = next(s for s in _wf()["jobs"]["report"]["steps"]
                if "commit" in (s.get("name") or "").lower())
    run = step["run"]
    assert run.index('cp "reports/$SLUG.md"') < run.index("git reset --hard")
    assert run.index("git reset --hard") < run.index('cp "$RUNNER_TEMP/report.md"')
    assert "for attempt in 1 2 3" in run      # push races with other workflows
    assert 'if [ ! -s "reports/$SLUG.md" ]' in run   # empty output is an error
    assert step["env"]["SLUG"] == "${{ matrix.book.slug }}"


def test_nothing_in_the_live_path_depends_on_the_hermes_secret():
    """detect/report must stand alone, so retiring the relay job is a pure
    deletion."""
    wf = _wf()
    for name in ("detect", "report"):
        assert "HERMES" not in json.dumps(wf["jobs"][name])
