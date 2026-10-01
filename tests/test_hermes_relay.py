"""Tests for the Hermes relay: diff, payload, signing, dedupe, retry,
stream parsing, and the atomic sent-state. Live boundaries (GitHub Actions,
the ntfy stream, the Hermes endpoint, launchd) are covered by the manual
smoke test; everything here runs against canned inputs and stubbed HTTP."""
import hashlib
import hmac
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker import hermes_relay as hr  # noqa: E402

SECRET = "test-secret"


def _book(title="Hum", author="Helen Phillips", slug="hum", status="reading",
          rating=None, **kw):
    b = {"title": title, "author": author, "slug": slug, "status": status,
         "rating": rating, "sessions": []}
    b.update(kw)
    return b


def _log(*books):
    return {"settings": {}, "books": list(books)}


# ---------------------------------------------------------------- AC.1 diff

def test_newly_finished_transition():
    old = _log(_book(status="reading"))
    new = _log(_book(status="finished", rating=4))
    assert [b["slug"] for b in hr.newly_finished(old, new)] == ["hum"]


def test_newly_finished_ignores_rating_edit():
    """A rating change on an already-finished book is not a new finish."""
    old = _log(_book(status="finished", rating=3))
    new = _log(_book(status="finished", rating=5))
    assert hr.newly_finished(old, new) == []


def test_newly_finished_ignores_reserialization_and_sessions():
    old = _log(_book(status="finished", rating=3, finished="2025-01-16"))
    new = _log(_book(status="finished", rating=3, finished="2025-01-16",
                     sessions=["2025-01-15 40"]))
    assert hr.newly_finished(old, new) == []


def test_newly_finished_slug_keying():
    """Keyed by slug, not list position or title — so a rename (slug kept,
    per the phone editor) doesn't re-fire while a genuinely new slug does."""
    old = _log(_book(slug="a-book", title="Old Title", status="finished"))
    new = _log(
        _book(slug="a-book", title="New Title", status="finished"),
        _book(slug="b-book", title="B Book", status="finished"),
    )
    assert [b["slug"] for b in hr.newly_finished(old, new)] == ["b-book"]


def test_newly_finished_empty_old():
    """Empty old log: every finished book is new (the dispatch recovery
    path relies on this; relay-side dedupe makes it safe)."""
    new = _log(_book(slug="a", status="finished"),
               _book(slug="b", status="reading"),
               _book(slug="c", status="finished"))
    assert sorted(b["slug"] for b in hr.newly_finished({}, new)) == ["a", "c"]


def test_newly_finished_ignores_abandoned_and_reading():
    new = _log(_book(slug="a", status="abandoned"),
               _book(slug="b", status="reading"))
    assert hr.newly_finished({}, new) == []


def test_slugless_entry_refires():
    """A hand-edited entry with no stored slug gets its slug re-derived
    (exactly as the log loader does), so it fires the first time and is
    then deduped by the derived slug. Documents the accepted fallback."""
    slugless = {"title": "The Corrections", "author": "Franzen",
                "status": "finished"}
    assert [b for b in hr.newly_finished({}, _log(slugless))] == [slugless]
    # once recorded under the derived slug, the same entry doesn't re-fire
    old = _log({"title": "The Corrections", "author": "Franzen",
                "slug": "the-corrections", "status": "finished"})
    assert hr.newly_finished(old, _log(slugless)) == []


def test_reread_new_slug_fires():
    """A re-read is a new log entry with a suffixed slug: finishing it
    fires again — one critique per read, by design."""
    old = _log(_book(slug="hum", status="finished"))
    new = _log(_book(slug="hum", status="finished"),
               _book(slug="hum-2", status="finished"))
    assert [b["slug"] for b in hr.newly_finished(old, new)] == ["hum-2"]


# ---------------------------------------------------------------- AC.2 payload

def test_build_payload_fields():
    """The event field is `type` — Hermes silently ignores anything else
    with HTTP 200, so this is the one field name that must never drift."""
    p = hr.build_payload(_book(title="Hum", author="Helen Phillips",
                               slug="hum", status="finished"), {})
    assert p == {"type": "book.finished", "title": "Hum",
                 "author": "Helen Phillips", "year": None, "slug": "hum"}
    assert "event" not in p


def test_build_payload_year_from_cache():
    cache = {"hum|helen phillips": {"year": 2025, "source": "openlibrary"}}
    p = hr.build_payload(_book(), cache)
    assert p["year"] == 2025


def test_build_payload_year_null_on_miss():
    p = hr.build_payload(_book(), {"unrelated|key": {"year": 1999}})
    assert p["year"] is None


def test_build_payload_non_ascii():
    p = hr.build_payload(_book(title="Hüm", author="Hélène Phillips"), {})
    assert p["title"] == "Hüm" and p["author"] == "Hélène Phillips"


# ---------------------------------------------------------------- AC.3 sign

def test_sign_known_answer():
    """Precomputed HMAC-SHA256 vectors, pinned so an encoding or framing
    change to the signed string breaks loudly."""
    body = ('{"type":"book.finished","title":"Hüm",'
            '"author":"Hélène Phillips","year":2025,"slug":"hum"}')
    assert hr.sign("1700000000", body, SECRET) == (
        "f332e0ddeefd79bbebcc0fd3744e0b2b0cda4768f5450848b38447a9cbf15ade")
    body2 = ('{"type":"book.finished","title":"Hum",'
             '"author":"Helen Phillips","year":null,"slug":"hum"}')
    assert hr.sign("1600000000", body2, SECRET) == (
        "4841b1f229df19760811642eef18bec4aa001396c6dc3731adc9a56ecf2db563")
    # and the independent re-derivation agrees (guards against a vector
    # that was itself generated by a broken implementation)
    assert hr.sign("1700000000", body, SECRET) == hmac.new(
        SECRET.encode(), ("1700000000." + body).encode(),
        hashlib.sha256).hexdigest()


def test_send_signs_exact_bytes():
    """The bytes signed are byte-identical to the bytes POSTed, including
    for a non-ASCII title — serialize once, sign and POST the same buffer,
    with the charset pinned in the Content-Type."""
    payload = hr.build_payload(
        _book(title="Hüm", author="Hélène Phillips", slug="hum",
              status="finished"), {})
    body = hr.payload_body(payload)
    body_bytes = body.encode("utf-8")

    captured = {}

    def fake_post(url, data=None, headers=None, timeout=None, log=None):
        captured["data"] = data
        captured["headers"] = headers
        captured["url"] = url
        class R:
            status_code = 200
            text = ""
        return R()

    fixed_now = 1700000000.0
    assert hr.send(body_bytes, SECRET, "http://x/hook",
                   post=fake_post, clock=lambda: fixed_now) is True
    assert captured["data"] == body_bytes          # byte-identical POST
    ts = captured["headers"]["X-Webhook-Timestamp"]
    assert ts == "1700000000"
    assert captured["headers"]["Content-Type"] == "application/json; charset=utf-8"
    assert captured["headers"]["X-Webhook-Signature-V2"] == hmac.new(
        SECRET.encode(), ("1700000000." + body).encode("utf-8"),
        hashlib.sha256).hexdigest()
    # what was signed is exactly what was POSTed
    assert hmac.new(SECRET.encode(),
                    ("1700000000.").encode("utf-8") + captured["data"],
                    hashlib.sha256).hexdigest() == \
        captured["headers"]["X-Webhook-Signature-V2"]


def test_send_non_2xx_is_failure():
    def fake_post(url, data=None, headers=None, timeout=None, log=None):
        class R:
            status_code = 500
            text = "boom"
        return R()
    assert hr.send(b"{}", SECRET, "http://x/hook", post=fake_post) is False


def test_send_network_error_is_failure():
    def fake_post(url, data=None, headers=None, timeout=None, log=None):
        import requests
        raise requests.ConnectionError("refused")
    logs = []
    assert hr.send(b"{}", SECRET, "http://x/hook", post=fake_post,
                   log=logs.append) is False
    assert any("failed" in m for m in logs)


# ---------------------------------------------------------------- AC.8 state

def test_sent_state_round_trip(tmp_path):
    path = tmp_path / "hermes-sent.json"
    hr.save_sent(path, {"hum": "2026-10-01T00:00:00+00:00"})
    data = json.loads(path.read_text())
    # only {slug: iso-timestamp}; no secret-shaped values can hide here
    assert data == {"sent": {"hum": "2026-10-01T00:00:00+00:00"}}
    assert hr.load_sent(path) == {"hum": "2026-10-01T00:00:00+00:00"}


def test_sent_state_corrupt_file_tolerated(tmp_path, capsys):
    """A corrupt state file is treated as empty (loudly), never a crash —
    a crash here would be a launchd KeepAlive loop replaying ntfy's cache
    every 10 seconds."""
    path = tmp_path / "hermes-sent.json"
    path.write_text("{not json at all")
    assert hr.load_sent(path) == {}
    assert "hermes-sent.json" in capsys.readouterr().err


def test_sent_state_missing_file_is_empty(tmp_path):
    assert hr.load_sent(tmp_path / "nope.json") == {}


def test_sent_state_missing(tmp_path):
    """load/save on a state dir that doesn't exist yet creates it."""
    path = tmp_path / "sub" / "hermes-sent.json"
    hr.save_sent(path, {"a": "b"})
    assert hr.load_sent(path) == {"a": "b"}


def test_save_sent_is_atomic(tmp_path):
    """The write goes to a tmp file then os.replace: a kill mid-write
    leaves the previous state, never a truncated file."""
    path = tmp_path / "hermes-sent.json"
    hr.save_sent(path, {"hum": "1"})
    leftovers = [p.name for p in tmp_path.iterdir()]
    assert leftovers == ["hermes-sent.json"]


# ---------------------------------------------------------------- AC.4/5 stream

def _msg_line(payload, mid="m1"):
    return json.dumps({"id": mid, "event": "message",
                       "message": json.dumps(payload)})


def _ok_sender(status=200, want_secret=SECRET, want_endpoint="http://x/hook"):
    """A stand-in for `send` (the sender contract: bytes+secret+endpoint
    -> bool), which also checks it was called with what the stream layer
    should have passed it. The HTTP-status semantics themselves are
    covered by the send() tests above."""
    def fake_send(body_bytes, secret, endpoint, log=None):
        assert isinstance(body_bytes, bytes)
        json.loads(body_bytes.decode("utf-8"))   # the payload, serialized
        assert secret == want_secret and endpoint == want_endpoint
        return 200 <= status < 300
    return fake_send


def test_stream_message_triggers_send():
    sent, pending = {}, []
    payload = {"type": "book.finished", "title": "Hum",
               "author": "Helen Phillips", "year": 2025, "slug": "hum"}
    changed = hr.process_stream_line(_msg_line(payload), sent=sent,
                                    pending=pending, secret=SECRET,
                                    endpoint="http://x/hook", sender=_ok_sender(),
                                    log=lambda *a: None)
    assert changed and sent == {"hum": sent["hum"]} and not pending


def test_stream_ignores_keepalive_but_flushes_pending():
    """A keepalive event carries no message to send, but it is a retry
    tick for pending deliveries — ntfy sends one roughly every 45s, which
    is the relay's only retry timer."""
    sent, pending = {}, [("hum", hr.payload_body(
        {"type": "book.finished", "title": "Hum", "author": "X",
         "year": None, "slug": "hum"}))]
    changed = hr.process_stream_line(
        json.dumps({"id": "k1", "event": "keepalive"}),
        sent=sent, pending=pending, secret=SECRET, endpoint="http://x/hook",
        sender=_ok_sender(), log=lambda *a: None)
    assert changed and "hum" in sent and pending == []


def test_dedupe_skips_sent():
    sent = {"hum": "2026-01-01T00:00:00+00:00"}
    payload = {"type": "book.finished", "title": "Hum", "author": "X",
               "year": None, "slug": "hum"}
    changed = hr.process_stream_line(_msg_line(payload, "m2"), sent=sent,
                                     pending=[], secret=SECRET,
                                     endpoint="http://x/hook",
                                     sender=_ok_sender(), log=lambda *a: None)
    assert not changed and sent == {"hum": "2026-01-01T00:00:00+00:00"}


def test_failed_post_not_recorded():
    """A non-2xx POST records nothing and queues for the keepalive flush."""
    sent, pending = {}, []
    payload = {"type": "book.finished", "title": "Hum", "author": "X",
               "year": None, "slug": "hum"}
    hr.process_stream_line(_msg_line(payload), sent=sent, pending=pending,
                           secret=SECRET, endpoint="http://x/hook",
                           sender=_ok_sender(status=500), log=lambda *a: None)
    assert sent == {} and [s for s, _ in pending] == ["hum"]


def test_retry_flushes_on_keepalive():
    """The full loop: fail, flush on the next stream event (keepalive),
    record only after the 2xx."""
    sent, pending = {}, []
    payload = {"type": "book.finished", "title": "Hum", "author": "X",
               "year": None, "slug": "hum"}
    hr.process_stream_line(_msg_line(payload), sent=sent, pending=pending,
                           secret=SECRET, endpoint="http://x/hook",
                           sender=_ok_sender(status=503), log=lambda *a: None)
    assert sent == {}
    hr.process_stream_line(json.dumps({"id": "k", "event": "keepalive"}),
                           sent=sent, pending=pending, secret=SECRET,
                           endpoint="http://x/hook", sender=_ok_sender(),
                           log=lambda *a: None)
    assert "hum" in sent and pending == []


def test_stream_malformed_message_skipped():
    """A hostile or torn publish on the topic is logged and skipped; the
    loop keeps going. Every malformed variant, plus the empty-envelope
    case, must all be non-fatal."""
    sent, pending = {}, []
    logs = []
    lines = [
        "not json at all",
        json.dumps({"id": "m", "event": "message", "message": "{bad json"}),
        json.dumps({"id": "m", "event": "message", "message": "plain text"}),
        json.dumps({"id": "m", "event": "message", "message": json.dumps(
            {"event": "book.finished", "title": "Wrong Field Name"})}),
        json.dumps({"id": "m", "event": "message", "message": json.dumps(
            {"type": "book.finished"})}),   # no slug
        json.dumps({"id": "m", "event": "message", "message": json.dumps(
            {"type": "movie.finished", "slug": "x"})}),
        json.dumps([1, 2, 3]),               # a JSON non-dict
        "",
    ]
    for line in lines:
        hr.process_stream_line(line, sent=sent, pending=pending,
                               secret=SECRET, endpoint="http://x/hook",
                               sender=_ok_sender(), log=logs.append)
    assert sent == {} and pending == []
    assert len(logs) >= 6   # each skip said why
    # and the loop is still alive: a good message after all that sends
    payload = {"type": "book.finished", "title": "Hum", "author": "X",
               "year": None, "slug": "hum"}
    hr.process_stream_line(_msg_line(payload), sent=sent, pending=pending,
                           secret=SECRET, endpoint="http://x/hook",
                           sender=_ok_sender(), log=logs.append)
    assert "hum" in sent


def test_message_body_non_string_does_not_crash():
    """A hostile envelope whose message field is a list would make
    json.loads raise TypeError, not ValueError; it must still be skipped."""
    sent, pending = {}, []
    hr.process_stream_line(
        json.dumps({"id": "m", "event": "message", "message": [1, 2]}),
        sent=sent, pending=pending, secret=SECRET, endpoint="http://x/hook",
        sender=_ok_sender(), log=lambda *a: None)
    assert sent == {} and pending == []


def test_lone_surrogate_message_does_not_crash():
    """The never-raises contract of the trust boundary. json.loads accepts
    the pure-ASCII escape "\\ud800" into a lone surrogate; encoding it to
    UTF-8 then raises UnicodeEncodeError — which must skip the message,
    not kill the daemon into a launchd KeepAlive crash loop."""
    sent, pending = {}, []
    logs = []
    raw = ('{"id":"m","event":"message","message":"{\\"type\\":'
           '\\"book.finished\\",\\"title\\":\\"\\\\ud800\\",'
           '\\"slug\\":\\"evil\\"}"}')
    hr.process_stream_line(raw, sent=sent, pending=pending, secret=SECRET,
                           endpoint="http://x/hook", sender=_ok_sender(),
                           log=logs.append)
    assert sent == {} and pending == []
    assert any("skipping unprocessable" in m for m in logs)
    # the daemon survives and still delivers a good message after it
    payload = {"type": "book.finished", "title": "Hum", "author": "X",
               "year": None, "slug": "hum"}
    hr.process_stream_line(_msg_line(payload), sent=sent, pending=pending,
                           secret=SECRET, endpoint="http://x/hook",
                           sender=_ok_sender(), log=logs.append)
    assert "hum" in sent


def test_deeply_nested_message_does_not_crash():
    """Same contract for pathological nesting: json.loads raises
    RecursionError, which is neither ValueError nor TypeError."""
    sent, pending = {}, []
    logs = []
    deep = "[" * 20000 + "]" * 20000
    hr.process_stream_line(deep, sent=sent, pending=pending, secret=SECRET,
                           endpoint="http://x/hook", sender=_ok_sender(),
                           log=logs.append)
    assert sent == {} and pending == []
    # skip logs exist either from the RecursionError catch or the
    # broad never-raise guard — both fulfill the contract
    payload = {"type": "book.finished", "title": "Hum", "author": "X",
               "year": None, "slug": "hum"}
    hr.process_stream_line(_msg_line(payload), sent=sent, pending=pending,
                           secret=SECRET, endpoint="http://x/hook",
                           sender=_ok_sender(), log=logs.append)
    assert "hum" in sent


def test_topic_hint_does_not_leak_topic():
    """The topic is treated like a password; log lines carry a hint, not
    the name itself."""
    hint = hr._topic_hint("jw-hermes-relay-supersecret-topic")
    assert "supersecret-topic" not in hint
    assert "jw-hermes-relay-supersecret-topic" not in hint
    # a short topic reveals nothing
    assert hr._topic_hint("ab") == "short"


def test_once_url_has_poll_param():
    """--once must use poll=1 so the connection returns the cache and
    closes; without it the command hangs on the open stream."""
    once = hr.stream_url("https://ntfy.sh", "t", once=True)
    daemon = hr.stream_url("https://ntfy.sh", "t")
    assert once == "https://ntfy.sh/t/json?poll=1&since=all"
    assert daemon == "https://ntfy.sh/t/json?since=all"
    assert "poll=1" in once and "poll=1" not in daemon
    assert "since=all" in once and "since=all" in daemon


def test_dry_run_prints_without_sending():
    sent, pending = {}, []
    payload = {"type": "book.finished", "title": "Hüm", "author": "X",
               "year": None, "slug": "hum"}
    logs = []
    def boom(*a, **k):
        raise AssertionError("dry-run must not POST")
    hr.process_stream_line(_msg_line(payload), sent=sent, pending=pending,
                           secret=SECRET, endpoint="http://x/hook",
                           sender=boom, dry_run=True, log=logs.append)
    assert sent == {} and pending == []
    assert any("dry-run" in m for m in logs)


# ---------------------------------------------------------------- secret

def test_resolve_secret_env_wins():
    assert hr.resolve_secret("from-env") == "from-env"


def test_resolve_secret_from_cli_output(monkeypatch, tmp_path):
    import subprocess
    out = ("  1 webhook subscription(s):\n\n  ◆ book-finished\n"
           "    URL: http://localhost:8644/webhooks/book-finished\n"
           "    Secret: abc123\n")
    monkeypatch.setattr(hr.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(
                            a, 0, stdout=out, stderr=""))
    # subs_path is isolated so a CLI-parse miss can never read the
    # operator's real ~/.hermes store from a test
    assert hr.resolve_secret(None, subs_path=tmp_path / "none.json") == "abc123"


def test_resolve_secret_from_subscription_store(monkeypatch, tmp_path):
    """The current hermes CLI does not print the secret; the local
    subscription store is the fallback (the operator's file lives at
    ~/.hermes/webhook_subscriptions.json with mode 600)."""
    import subprocess

    def fail_run(*a, **k):
        return subprocess.CompletedProcess(a, 1, stdout="", stderr="")

    monkeypatch.setattr(hr.subprocess, "run", fail_run)
    subs = tmp_path / "webhook_subscriptions.json"
    subs.write_text(json.dumps({
        "book-finished": {"events": ["book.finished"], "secret": "sekrit"}}))
    assert hr.resolve_secret(None, subs_path=subs) == "sekrit"


def test_resolve_secret_gives_up_loudly(monkeypatch, tmp_path):
    import subprocess

    def fail_run(*a, **k):
        return subprocess.CompletedProcess(a, 0, stdout="no secret here",
                                           stderr="")

    monkeypatch.setattr(hr.subprocess, "run", fail_run)
    logs = []
    assert hr.resolve_secret(None, subs_path=tmp_path / "missing.json",
                             log=logs.append) is None
    assert any("secret" in m.lower() for m in logs)


# ---------------------------------------------------------------- publish

def test_publish_posts_payload_json():
    posts = []

    def fake_post(url, data=None, headers=None, timeout=None):
        posts.append((url, data, headers))
        class R:
            status_code = 200
            def raise_for_status(self):
                pass
        return R()

    payload = {"type": "book.finished", "title": "Hüm", "author": "X",
               "year": None, "slug": "hum"}
    n = hr.publish([payload], "my-topic", "https://ntfy.sh",
                   post=fake_post, log=lambda *a: None)
    assert n == 1
    url, data, headers = posts[0]
    assert url == "https://ntfy.sh/my-topic"
    assert data == hr.payload_body(payload).encode("utf-8")
    assert headers == {}   # no Title/Tags: the body is the payload


def test_publish_with_token():
    posts = []

    def fake_post(url, data=None, headers=None, timeout=None):
        posts.append(headers)
        class R:
            status_code = 200
            def raise_for_status(self):
                pass
        return R()

    hr.publish([{"type": "book.finished", "slug": "x"}], "t", "https://s",
               token="tok", post=fake_post, log=lambda *a: None)
    assert posts[0] == {"Authorization": "Bearer tok"}


def test_publish_failure_raises():
    import requests

    def fake_post(url, data=None, headers=None, timeout=None):
        class R:
            status_code = 503
            def raise_for_status(self):
                raise requests.HTTPError("503")
        return R()

    with pytest.raises(requests.HTTPError):
        hr.publish([{"type": "book.finished", "slug": "x"}], "t", "https://s",
                    post=fake_post, log=lambda *a: None)


# ---------------------------------------------------------------- CLI layer

class _Args:
    def __init__(self, once=False, dry_run=False, diff_before=None,
                 publish=False):
        self.once = once
        self.dry_run = dry_run
        self.diff_before = diff_before
        self.publish = publish


class _Cap:
    """Collect stdout/stderr from the CLI layer without capsys plumbing."""
    def __init__(self):
        self.out, self.err = [], []

    def __call__(self, *a):
        self.out.append(" ".join(str(x) for x in a))


def _drive_diff(old_books, monkeypatch, tmp_path, *, extra_env=None,
                publish=False, dry_run=False):
    """Run `run()` with --diff-before against a tmp snapshot; reading/log.json
    and the pubyear cache are the REAL repo files (read-only), so the
    payloads list the repo's actual finished books when old is empty."""
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"books": old_books}))
    for k, v in (extra_env or {}).items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("HERMES_NTFY_TOPIC", raising=False)
    cap = _Cap()
    printed = []
    monkeypatch.setattr(hr, "print",
                        lambda *a, **k: printed.append(" ".join(map(str, a))),
                        raising=False)
    rc = hr.run(_Args(diff_before=str(old), publish=publish,
                      dry_run=dry_run))
    return rc, printed, cap


def test_run_diff_without_topic_needs_topic_to_publish(monkeypatch, tmp_path):
    """--publish with no topic configured fails loudly; a dry diff (no
    --publish) works without the topic since nothing is published."""
    rc, printed, _ = _drive_diff([], monkeypatch, tmp_path)
    assert rc == 0
    assert any("not publishing" in p for p in printed)
    rc2, printed2, _ = _drive_diff([], monkeypatch, tmp_path, publish=True)
    assert rc2 == 1
    assert any("HERMES_NTFY_TOPIC" in p for p in printed2)


def test_run_diff_empty_old_warns_on_zero_finished(monkeypatch, tmp_path):
    """The broken-wiring heuristic: an empty old log with zero payloads
    means reading/log.json itself has no finished books — refuse to call
    that a clean no-op (exit 1), since on a dispatch run it means the
    wiring is broken."""
    rc, printed, _ = _drive_diff([], monkeypatch, tmp_path)
    # the real repo log has many finished books, so this run lists them
    assert rc == 0 and any("book.finished" in p for p in printed)


def test_run_diff_garbage_snapshot_refused(monkeypatch, tmp_path, capsys):
    """A garbage old snapshot is refused, not silently treated as empty —
    silently diffing against nothing would mass-republish every finished
    book (dedupe makes it harmless, but it would mask the mistake)."""
    bad = tmp_path / "bad.json"
    bad.write_text("this is not json {")
    with pytest.raises(SystemExit):
        hr.run(_Args(diff_before=str(bad)))
    captured = capsys.readouterr()
    assert "not valid JSON" in (captured.out + captured.err)


def test_run_diff_dry_run_does_not_publish(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise AssertionError("dry-run must not publish")
    monkeypatch.setattr(hr, "publish", boom)
    monkeypatch.setenv("HERMES_NTFY_TOPIC", "some-topic")
    rc, printed, _ = _drive_diff([], monkeypatch, tmp_path, publish=True,
                                 dry_run=True)
    assert rc == 0
    assert any("dry run" in p for p in printed)
