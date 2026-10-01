"""Hermes book-finished webhook relay.

reading/log.json is edited from the phone, so the event source is GitHub,
not this disk. The pipeline is fully event-driven — nothing polls, and no
tokens are spent on the idle path:

  phone edit -> GitHub commit to reading/log.json
    -> .github/workflows/book-finished.yml diffs the before/after commits
    -> one JSON message per newly-finished book -> a dedicated ntfy topic
    -> this daemon (launchd) streams that topic, dedupes by slug, signs,
       and POSTs to the local Hermes webhook endpoint
    -> Hermes runs its book-consensus critique skill

Delivery is at-least-once, not exactly-once: a slug is recorded as sent
only after a 2xx response, so a crash between a successful POST and the
state write can re-deliver once. The window is sub-second; accepted. If
Hermes ever dedupes by slug itself, it closes entirely.

Env (mirrors tracker/notify.py; tracker/cli.py loads the repo .env):
  HERMES_NTFY_TOPIC      required — the dedicated relay topic. Unguessable
                         like NTFY_TOPIC: anyone who learns it can trigger
                         a critique (token spend), so treat it as a password.
  HERMES_NTFY_SERVER     default https://ntfy.sh
  HERMES_WEBHOOK_URL     default http://localhost:8644/webhooks/book-finished
  HERMES_WEBHOOK_SECRET  optional override; otherwise the secret is read
                         from the local Hermes install
  HERMES_NTFY_TOKEN      optional ntfy access token for publishing
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from .config import env

from .book_finished import (            # noqa: F401  (re-exported)
    LOG_PATH,
    PUBYEAR_CACHE_PATH,
    REPO,
    _slug_of,
    load_pubyear_cache,
    newly_finished,
    read_log_snapshot as _read_log_snapshot,
)

SENT_PATH = REPO / "state" / "hermes-sent.json"

DEFAULT_SERVER = "https://ntfy.sh"
DEFAULT_ENDPOINT = "http://localhost:8644/webhooks/book-finished"

TIMEOUT = 20            # plain POST/poll
CONNECT_TIMEOUT = 10    # stream: time to establish
READ_TIMEOUT = 120      # stream: ntfy keepalives arrive every ~45s, so a
                        # silent connection is dead after ~3 missed ones


# ------------------------------------------------- payload (relay-only)
#
# The diff itself (newly_finished / slug keying / the pubyear cache) now
# lives in tracker/book_finished.py and is imported above, so this module
# is only the ntfy+webhook transport to a local agent.

def build_payload(book: dict, pubyear_cache: dict) -> dict:
    """The webhook payload: {"type", "title", "author", "year", "slug"}.

    The event field MUST be `type` — Hermes silently ignores anything else
    with HTTP 200, so a typo here fails invisibly. `slug` exists for
    relay-side dedupe (Hermes tolerates the extra field). Year comes from
    the pubyear cache and is null on a miss; Hermes resolves it itself.
    """
    title = str(book.get("title") or "")
    author = str(book.get("author") or "")
    # Same key the reading log uses for its caches (Book.cache_key).
    key = f"{title.strip().lower()}|{author.strip().lower()}"
    entry = pubyear_cache.get(key) if isinstance(pubyear_cache, dict) else None
    year = entry.get("year") if isinstance(entry, dict) else None
    return {
        "type": "book.finished",
        "title": title,
        "author": author,
        "year": year,
        "slug": _slug_of(book),
    }


def payload_body(payload: dict) -> str:
    """Canonical JSON body. Serialized once and reused for both signing and
    the request body — never re-serialized, so the two can never diverge."""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------- sign/send

def sign(timestamp: str, body: str, secret: str) -> str:
    """Hex HMAC-SHA256 over the UTF-8 bytes of "<timestamp>.<body>"."""
    return _sign_bytes(timestamp, body.encode("utf-8"), secret)


def _sign_bytes(timestamp: str, body_bytes: bytes, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"),
                   f"{timestamp}.".encode("utf-8") + body_bytes, hashlib.sha256)
    return mac.hexdigest()


def send(body_bytes: bytes, secret: str, endpoint: str, *,
         post=None, clock=time.time, log=None) -> bool:
    """POST the already-serialized body, freshly signed, to Hermes.

    The bytes signed are literally the bytes POSTed (`data=body_bytes`),
    so a non-ASCII title survives identically on both sides of the
    signature. True only on a 2xx — a failure is never recorded as sent.
    """
    timestamp = str(int(clock()))
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "X-Webhook-Timestamp": timestamp,
        "X-Webhook-Signature-V2": _sign_bytes(timestamp, body_bytes, secret),
    }
    try:
        resp = (post or requests.post)(endpoint, data=body_bytes,
                                       headers=headers, timeout=TIMEOUT)
    except requests.RequestException as exc:
        if log:
            log(f"hermes relay: POST {endpoint} failed: {exc}")
        return False
    if not 200 <= resp.status_code < 300:
        if log:
            log(f"hermes relay: POST {endpoint} returned HTTP "
                f"{resp.status_code}; not recorded as sent")
        return False
    return True


# ---------------------------------------------------------------- sent state

def load_sent(path: Path) -> dict:
    """The sent-slug set: {"sent": {slug: iso timestamp}}.

    A corrupt or unreadable file is treated as empty, loudly — the same
    corrupt-file tolerance as tracker/state.py. A kill mid-write degrades
    to at-least-once re-delivery, never to a launchd crash loop (and a
    crash loop would replay ntfy's cache every 10s forever).
    """
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        print(f"hermes relay: WARNING: {path} unreadable ({exc}); starting "
              "from empty sent-state — already-delivered books may be "
              "re-sent", file=sys.stderr)
        return {}
    sent = data.get("sent") if isinstance(data, dict) else None
    return dict(sent) if isinstance(sent, dict) else {}


def save_sent(path: Path, sent: dict) -> None:
    """Atomic write (tmp file + os.replace), so a kill mid-write leaves the
    previous state intact rather than a truncated file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"sent": sent}, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- secret

def resolve_secret(explicit: str | None = None, *, run=None,
                   subs_path: Path | None = None, log=None) -> str | None:
    """The Hermes webhook signing secret. Never sent anywhere but the
    signature, never written to any file in this repo.

    Order: explicit override (HERMES_WEBHOOK_SECRET), then the `hermes`
    CLI (if the installed version prints the secret — today's does not),
    then Hermes' own subscription store, then give up with a clear error —
    an unsigned send is worse than no send.
    """
    if explicit:
        return explicit

    run = run or subprocess.run   # resolved at call time, not import time,
    # so tests (and anything else) can swap the runner without reaching
    # into this module's globals
    try:
        proc = run(["hermes", "webhook", "list"],
                   capture_output=True, text=True, timeout=30)
        if getattr(proc, "returncode", 1) == 0:
            m = re.search(r"(?im)^[ \t]*secret[ \t]*[:=][ \t]*(\S+)[ \t]*$",
                          proc.stdout or "")
            if m and m.group(1).strip("*()").lower() not in ("hidden",):
                return m.group(1)
    except (OSError, subprocess.SubprocessError):
        pass   # no hermes on PATH / it hung: fall through, don't fail yet

    path = subs_path or Path.home() / ".hermes" / "webhook_subscriptions.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = None
    if isinstance(data, dict):
        sub = data.get("book-finished")
        if not isinstance(sub, dict):
            sub = next((s for s in data.values()
                        if isinstance(s, dict)
                        and "book.finished" in (s.get("events") or [])), None)
        secret = (sub or {}).get("secret")
        if secret:
            return str(secret)

    if log:
        log("hermes relay: could not find the Hermes webhook secret "
            "(tried HERMES_WEBHOOK_SECRET, `hermes webhook list`, and "
            f"{path}); refusing to send unsigned")
    return None


# ---------------------------------------------------------------- stream

def _topic_hint(topic: str) -> str:
    """A non-reversible topic hint for log lines — the topic is treated
    like a password, so it never appears in the daemon's persistent log
    (the full URL still goes to ntfy itself, where it belongs)."""
    return f"{len(topic)} chars, ends …{topic[-4:]}" if len(topic) > 4 \
        else "short"


def stream_url(server: str, topic: str, *, once: bool = False) -> str:
    """The ntfy JSON-stream URL. `since=all` replays the topic's cached
    messages first, which is how a finish that happened while the laptop
    was off is recovered at startup. `poll=1` (once mode only) makes the
    connection return the cache and close — without it, --once hangs on
    the open stream."""
    base = f"{server.rstrip('/')}/{topic}/json"
    return f"{base}?poll=1&since=all" if once else f"{base}?since=all"


def process_stream_line(line: str, *, sent: dict, pending: list,
                        secret: str, endpoint: str,
                        sender=send, dry_run: bool = False,
                        log=print) -> bool:
    """Handle one ntfy stream line. True if the sent-state changed.

    Never raises: anyone who learns the topic name can publish to it, so a
    stray or hostile message (or a torn line) must be logged and skipped,
    not crash the daemon — a KeepAlive crash loop would replay ntfy's
    cache every 10s forever.
    """
    before = len(sent)
    # Every stream event — message, keepalive, open, anything — is an
    # occasion to retry whatever failed earlier.
    _flush_pending(pending, sent=sent, secret=secret, endpoint=endpoint,
                   sender=sender, log=log)
    try:
        _process_message(line, sent=sent, pending=pending, secret=secret,
                          endpoint=endpoint, sender=sender,
                          dry_run=dry_run, log=log)
    except Exception as exc:  # noqa: BLE001 — the trust boundary. This
        # function promises never to raise: a hostile publish on the topic
        # must never crash the daemon (a KeepAlive loop would replay
        # ntfy's cache every 10s forever and block all delivery). Known
        # vectors reach here include a lone-surrogate JSON escape
        # (json.loads accepts it; .encode("utf-8") then fails) and
        # pathologically nested JSON (RecursionError). Skip, say why, go on.
        log(f"hermes relay: skipping unprocessable message "
            f"({type(exc).__name__}: {str(exc)[:100]}); "
            f"line was {str(line)[:100]!r}")
    return len(sent) != before


def _process_message(line: str, *, sent: dict, pending: list, secret: str,
                     endpoint: str, sender, dry_run: bool, log) -> None:
    """Parse and act on one stream line; may raise, caller never does."""
    try:
        envelope = json.loads(line)
    except (ValueError, TypeError, RecursionError):
        log(f"hermes relay: skipping unparseable stream line: {str(line)[:120]!r}")
        return
    if not isinstance(envelope, dict) or envelope.get("event") != "message":
        return   # keepalive/open/... — the pending flush already ran
    try:
        payload = json.loads(envelope.get("message") or "")
    except (ValueError, TypeError, RecursionError):
        log("hermes relay: skipping message whose body is not JSON")
        return
    if not isinstance(payload, dict) or payload.get("type") != "book.finished":
        log(f"hermes relay: skipping non-book.finished message "
            f"({str(payload)[:100]!r})")
        return
    slug = payload.get("slug")
    if not isinstance(slug, str) or not slug:
        log("hermes relay: skipping book.finished message with no usable slug")
        return
    if slug in sent:
        log(f"hermes relay: {slug} already delivered; skipping")
        return
    if any(pslug == slug for pslug, _ in pending):
        return   # queued from a failed attempt already

    body = payload_body(payload)
    if dry_run:
        log(f"hermes relay (dry-run): would POST {body} to {endpoint}")
        return
    if sender(body.encode("utf-8"), secret, endpoint, log=log):
        sent[slug] = _now_iso()
        log(f"hermes relay: delivered {slug}")
    else:
        pending.append((slug, body))
        log(f"hermes relay: delivery of {slug} failed; queued for retry "
            f"({len(pending)} pending)")


def _flush_pending(pending: list, *, sent: dict, secret: str, endpoint: str,
                   sender=send, log=print) -> None:
    """Retry failed deliveries — on every stream event, including keepalives.

    ntfy sends a keepalive roughly every 45s on the /json stream, so a
    transient failure retries within a minute with no extra connections or
    timers. Pending is in-memory only: a restart recovers it from ntfy's
    `since=all` cache replay, which re-offers every undelivered message.
    """
    if not pending:
        return
    still = []
    for slug, body in pending:
        if sender(body.encode("utf-8"), secret, endpoint, log=log):
            sent[slug] = _now_iso()
            log(f"hermes relay: delivered {slug} (retry)")
        else:
            still.append((slug, body))
    pending[:] = still


def subscribe_loop(topic: str, server: str, endpoint: str, secret: str,
                   state_path: Path, *, session=None, log=print,
                   dry_run: bool = False) -> int:
    """Daemon mode: stream the topic forever. Returns non-zero on stream
    error so launchd's KeepAlive restarts it (default ThrottleInterval
    10s); each restart replays the ntfy cache, and dedupe makes that
    harmless."""
    sent = load_sent(state_path)
    pending: list = []
    session = session or requests.Session()
    url = stream_url(server, topic)
    log(f"hermes relay: streaming {server}/<topic:{_topic_hint(topic)}> -> "
        f"{endpoint} ({'dry-run' if dry_run else 'live'})")
    while True:
        try:
            resp = session.get(url, stream=True,
                               timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            resp.raise_for_status()
            # ntfy's Content-Type carries no charset; pin it so non-ASCII
            # titles decode the same way they were published.
            resp.encoding = "utf-8"
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if process_stream_line(line, sent=sent, pending=pending,
                                        secret=secret, endpoint=endpoint,
                                        dry_run=dry_run, log=log):
                    _save_quietly(state_path, sent, log)
        except Exception as exc:  # noqa: BLE001 — a daemon dies loudly and
            # lets launchd restart it; it never hangs silently. Message
            # content can't reach here (process_stream_line never raises);
            # what's left is the stream/HTTP layer itself.
            log(f"hermes relay: stream error ({type(exc).__name__}: {exc}); "
                "exiting for launchd to restart")
            return 1


def run_once(topic: str, server: str, endpoint: str, secret: str,
             state_path: Path, *, session=None, log=print,
             dry_run: bool = False) -> int:
    """--once: poll ntfy's cached messages (poll=1 closes the stream) and
    exit. The debug entry point — never run this as a daemon."""
    sent = load_sent(state_path)
    pending: list = []
    url = stream_url(server, topic, once=True)
    log(f"hermes relay: polling {server}/<topic:{_topic_hint(topic)}> -> "
        f"{endpoint} ({'dry-run' if dry_run else 'live'})")
    try:
        resp = (session or requests).get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        resp.encoding = "utf-8"   # same charset pin as the daemon stream
        lines = resp.text.splitlines()
    except requests.RequestException as exc:
        log(f"hermes relay: poll failed ({exc})")
        return 1
    initial = len(sent)
    for line in lines:
        if not line:
            continue
        process_stream_line(line, sent=sent, pending=pending, secret=secret,
                            endpoint=endpoint, dry_run=dry_run, log=log)
    if len(sent) != initial and not dry_run:
        _save_quietly(state_path, sent, log)
    log(f"hermes relay: polled {len(lines)} line(s); "
        f"{len(sent) - initial} delivered, {len(pending)} left pending")
    return 0


def _save_quietly(path: Path, sent: dict, log) -> None:
    try:
        save_sent(path, sent)
    except OSError as exc:
        # Losing the state file only ever widens the at-least-once window;
        # it must never take the daemon down.
        log(f"hermes relay: could not write {path} ({exc}); "
            "delivered books may be re-sent on restart")


# ---------------------------------------------------------------- publish

def publish(payloads: list, topic: str, server: str, *, token: str | None = None,
            post=None, log=print) -> int:
    """Publish each payload JSON as the ntfy message body — the workflow's
    leg. Deliberately not tracker/notify.py's push helpers: a different,
    dedicated topic, and the message body IS the payload (no Title/Tags
    headers, nothing for a phone to render). Raises on a non-2xx so a
    failed workflow run is visible in Actions; the relay's dedupe makes a
    re-run harmless."""
    post = post or requests.post
    url = f"{server.rstrip('/')}/{topic}"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for payload in payloads:
        resp = post(url, data=payload_body(payload).encode("utf-8"),
                    headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        log(f"hermes relay: published {payload.get('slug')}")
    return len(payloads)


# ---------------------------------------------------------------- CLI

def run(args) -> int:
    """CLI entry for `python -m tracker hermes-relay` (see tracker/cli.py)."""
    topic = env("HERMES_NTFY_TOPIC")
    server = env("HERMES_NTFY_SERVER", DEFAULT_SERVER) or DEFAULT_SERVER
    endpoint = env("HERMES_WEBHOOK_URL", DEFAULT_ENDPOINT) or DEFAULT_ENDPOINT

    if args.diff_before:
        return _run_diff(args, topic, server)

    if not topic:
        print("hermes-relay: HERMES_NTFY_TOPIC is not set — the relay needs "
              "its dedicated ntfy topic (see .env.example)", file=sys.stderr)
        return 1

    secret = None
    if not args.dry_run:
        secret = resolve_secret(env("HERMES_WEBHOOK_SECRET"))
        if not secret:
            return 1   # resolve_secret already said why

    try:
        if args.once:
            return run_once(topic, server, endpoint, secret or "", SENT_PATH,
                            dry_run=args.dry_run)
        return subscribe_loop(topic, server, endpoint, secret or "",
                              SENT_PATH, dry_run=args.dry_run)
    except KeyboardInterrupt:
        print("hermes relay: stopped")
        return 0


def _run_diff(args, topic: str, server: str) -> int:
    """`--diff-before`: diff an old log snapshot against reading/log.json
    and publish (or print) one payload per newly-finished book — the mode
    the Actions workflow uses."""
    old_log, old_empty = _read_log_snapshot(args.diff_before)
    try:
        new_log = json.loads(LOG_PATH.read_text())
    except (OSError, ValueError) as exc:
        print(f"hermes-relay: could not read {LOG_PATH} ({exc})",
              file=sys.stderr)
        return 1
    payloads = [build_payload(b, load_pubyear_cache())
                for b in newly_finished(old_log, new_log)]
    for payload in payloads:
        print(payload_body(payload))
    if not payloads and old_empty:
        # An empty old log lists every currently-finished book; zero
        # results there means the wiring is broken, not that nothing
        # changed (a real before/after diff can legitimately be empty).
        print("hermes-relay: WARNING: old log was empty but no finished "
              "books were found in reading/log.json — this looks like "
              "broken wiring, not a no-op", file=sys.stderr)
        return 1
    if args.dry_run or not args.publish:
        verb = ("dry run — would publish" if args.dry_run and args.publish
                else "not publishing (no --publish)")
        print(f"{verb}: {len(payloads)} newly finished book(s)")
        return 0
    if not topic:
        print("hermes-relay: HERMES_NTFY_TOPIC is not set; cannot publish",
              file=sys.stderr)
        return 1
    try:
        count = publish(payloads, topic, server, token=env("HERMES_NTFY_TOKEN"))
    except requests.RequestException as exc:
        print(f"hermes-relay: publish failed ({exc})", file=sys.stderr)
        return 1
    print(f"published {count} message(s) to the relay topic")
    return 0
