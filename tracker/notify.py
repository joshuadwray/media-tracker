"""Notification delivery via ntfy.sh phone push.

Setup (once):
  1. Install the ntfy app (iOS/Android) and subscribe to a topic with an
     unguessable name, e.g. "jw-media-tracker-x7k2m9".
  2. Set NTFY_TOPIC to that name (GitHub Actions secret / .env locally).

Env:
  NTFY_TOPIC    required for pushes; without it, pushes are skipped
  NTFY_SERVER   default https://ntfy.sh (set if self-hosting)
  NTFY_TOKEN    optional access token for protected topics

Few sightings -> one push each (tappable, opens the source URL).
Many sightings -> a single digest push so your phone doesn't melt.
"""
from __future__ import annotations

import requests

from .config import env
from .models import Observation

MAX_INDIVIDUAL_PUSHES = 5
TIMEOUT = 20


def push_configured() -> bool:
    return bool(env("NTFY_TOPIC"))


def send_push(new_observations: list[Observation]) -> None:
    if not new_observations or not push_configured():
        return
    server = (env("NTFY_SERVER", "https://ntfy.sh") or "").rstrip("/")
    url = f"{server}/{env('NTFY_TOPIC')}"
    headers_base = {}
    if env("NTFY_TOKEN"):
        headers_base["Authorization"] = f"Bearer {env('NTFY_TOKEN')}"

    if len(new_observations) <= MAX_INDIVIDUAL_PUSHES:
        for obs in new_observations:
            headers = dict(headers_base)
            headers["Title"] = obs.item_label.encode("ascii", "ignore").decode()
            headers["Tags"] = "books" if obs.item_key.startswith("book:") else "movie_camera"
            if obs.url:
                headers["Click"] = obs.url
            requests.post(url, data=obs.summary.encode(), headers=headers,
                          timeout=TIMEOUT).raise_for_status()
    else:
        lines = [f"• {o.item_label}: {o.summary}" for o in new_observations]
        headers = dict(headers_base)
        headers["Title"] = f"{len(new_observations)} new watchlist sightings"
        headers["Tags"] = "mag"
        requests.post(url, data="\n".join(lines).encode(), headers=headers,
                      timeout=TIMEOUT).raise_for_status()
