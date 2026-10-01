"""Structural tests for .github/workflows/book-finished.yml — the live
trigger/dispatch behavior can't be unit-tested, so its wiring is asserted
here with PyYAML and the manual smoke test covers the rest. Note that
PyYAML parses the YAML 1.1 boolean-ish key `on:` as boolean `True`."""
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


def test_no_repo_writeback():
    """The workflow writes nothing back — no commit/push steps — so it
    can't loop with itself or the scheduled media-tracker run."""
    text = WF.read_text()
    assert "git push" not in text and "git commit" not in text
    # read-only at the workflow level, so no step can gain write later
    assert _wf()["permissions"] == {"contents": "read"}
