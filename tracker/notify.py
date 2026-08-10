"""Notification delivery via ntfy.sh phone push.

Setup (once):
  1. Install the ntfy app (iOS/Android) and subscribe to a topic with an
     unguessable name, e.g. "jw-media-tracker-x7k2m9".
  2. Set NTFY_TOPIC to that name (GitHub Actions secret / .env locally).

Env:
  NTFY_TOPIC    required for pushes; without it, pushes are skipped
  NTFY_SERVER   default https://ntfy.sh (set if self-hosting)
  NTFY_TOKEN    optional access token for protected topics

Pushes are per (item, medium), not per sighting: a book carried by three
libraries as an ebook is one "ebook" push, and only when that medium goes
unseen -> seen. Which libraries have it lives in the message body and on
the dashboard.

Few groups -> one push each (tappable, opens the source URL).
Many groups -> a single digest push so your phone doesn't melt.
"""
from __future__ import annotations

import requests

from .config import env
from .models import NotifyGroup

MAX_INDIVIDUAL_PUSHES = 5
TIMEOUT = 20


def push_configured() -> bool:
    return bool(env("NTFY_TOPIC"))


def send_note(title: str, message: str, tags: str = "heavy_plus_sign") -> None:
    """One-off informational push (e.g. add-item confirmations)."""
    if not push_configured():
        return
    server = (env("NTFY_SERVER", "https://ntfy.sh") or "").rstrip("/")
    headers = {"Title": title.encode("ascii", "ignore").decode(), "Tags": tags}
    if env("NTFY_TOKEN"):
        headers["Authorization"] = f"Bearer {env('NTFY_TOKEN')}"
    requests.post(f"{server}/{env('NTFY_TOPIC')}", data=message.encode(),
                  headers=headers, timeout=TIMEOUT).raise_for_status()


def send_push(groups: list[NotifyGroup]) -> None:
    if not groups or not push_configured():
        return
    server = (env("NTFY_SERVER", "https://ntfy.sh") or "").rstrip("/")
    url = f"{server}/{env('NTFY_TOPIC')}"
    headers_base = {}
    if env("NTFY_TOKEN"):
        headers_base["Authorization"] = f"Bearer {env('NTFY_TOKEN')}"

    if len(groups) <= MAX_INDIVIDUAL_PUSHES:
        for group in groups:
            headers = dict(headers_base)
            headers["Title"] = group.item_label.encode("ascii", "ignore").decode()
            headers["Tags"] = ("books" if group.item_key.startswith("book:")
                               else "movie_camera")
            click = next((o.url for o in group.observations if o.url), None)
            if click:
                headers["Click"] = click
            requests.post(url, data=body(group).encode(), headers=headers,
                          timeout=TIMEOUT).raise_for_status()
    else:
        ordered = sorted(groups, key=lambda g: g.item_label.lower())
        lines = [f"• {g.item_label}: {body(g)}" for g in ordered]
        headers = dict(headers_base)
        headers["Title"] = f"{len(groups)} new watchlist sightings"
        headers["Tags"] = "mag"
        requests.post(url, data="\n".join(lines).encode(), headers=headers,
                      timeout=TIMEOUT).raise_for_status()


def body(group: NotifyGroup) -> str:
    """One line describing a group.

    A lone sighting keeps the source's own wording, which already names the
    library and any hold queue. Otherwise collapse to "<medium> at A, B" —
    the medium is the news, the libraries are where to go get it. A debut
    group (a book seen for the first time, in several mediums at once) lists
    each medium in turn.
    """
    if len(group.observations) == 1:
        return group.observations[0].summary

    by_medium: dict[str, list[str]] = {}
    for obs in group.observations:
        sources = by_medium.setdefault(obs.medium or "found", [])
        if obs.source not in sources:
            sources.append(obs.source)
    return " · ".join(f"{medium} at {', '.join(sources)}"
                      for medium, sources in by_medium.items())
