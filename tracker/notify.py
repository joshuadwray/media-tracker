"""Notification delivery via ntfy.sh phone push.

Setup (once):
  1. Install the ntfy app (iOS/Android) and subscribe to a topic with an
     unguessable name, e.g. "jw-media-tracker-x7k2m9".
  2. Set NTFY_TOPIC to that name (GitHub Actions secret / .env locally).

Env:
  NTFY_TOPIC    required for pushes; without it, pushes are skipped
  NTFY_SERVER   default https://ntfy.sh (set if self-hosting)
  NTFY_TOKEN    optional access token for protected topics

Pushes are per decision, not per sighting.

For a book that means per (item, track): print and ebook are the same
decision, so a book carried by three libraries in two formats you'd read
is one "reading" push. It fires when the track goes unseen -> seen, or
when its shortest wait drops a bucket.

For a film it means per (item, venue), collapsed to one push per film per
run: a theatre speaks the first time it has the film and then stays
quiet, however its listing is worded and however many days of showtimes
go on sale later. Which libraries and which theatres, in what detail,
lives on the dashboard.

Few groups -> one push each (tappable, opens the best option's URL).
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
            # Something borrowable this minute is worth a louder buzz than a
            # place in a queue. Stop short of ntfy's urgent tier, which
            # bypasses do-not-disturb.
            if group.best_bucket == "now":
                headers["Priority"] = "4"
            head = group.headline
            click = (head.url if head and head.url
                     else next((o.url for o in group.observations if o.url), None))
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

    For a book this is the best option and where to get it, because that's
    the only part you can act on: "reading: now at Denton (print)". A book
    with copies at four libraries doesn't need all four in a push — the
    dashboard has them. A debut group (first sighting, both tracks at once)
    lists each track in turn.

    A film keeps the source's own wording, which already carries the dates,
    and names any other theatre found in the same run after it — those are
    the ones you'd otherwise never hear about, since each venue only ever
    gets one push.
    """
    if not group.track and not any(o.track for o in group.observations):
        if not group.observations:
            return ""
        lead = group.observations[0]
        others = [o.venue for o in group.observations[1:] if o.venue]
        return lead.summary + (f" · also at {', '.join(others)}" if others else "")

    lead = "sooner — " if group.reason == "sooner" else ""
    by_track: dict[str, list] = {}
    for obs in group.observations:
        by_track.setdefault(obs.track or "found", []).append(obs)

    parts = []
    for track, obs_list in by_track.items():
        best = min(obs_list, key=lambda o: o.sort_key)
        extra = len({o.where for o in obs_list}) - 1
        # A no-hold copy is the difference between "join the queue" and "go
        # get it", so it earns room in a push that was going out anyway. It
        # never causes one: the shelf is not part of any dedup key.
        shelf = (f" · {best.shelf} copy on shelf, no hold needed"
                 if best.shelf and best.shelf_copies else "")
        parts.append(
            f"{track}: {best.wait_text} at {best.where}"
            f" ({best.medium})" + (f" +{extra} more" if extra > 0 else "") + shelf
        )
    return lead + " · ".join(parts)
