"""Async pin queue: adds that couldn't be pinned to a catalog record.

When the mobile add flow (`tracker add --auto`) finds zero or multiple
catalog matches, the entry is still added as typed (instant), and a
record lands here so docs/add.html can surface a "needs pinning" card.
`tracker pin` (via pin-item.yml) resolves it later.

Store shape (state/pending-pins.json):
  {"pending": [{id, kind, typed_title, typed_author, added,
                candidates: [{title, author, format, bib_id, isbn, source}]}]}
Zero candidates => candidates: [] (UI offers keep/remove only).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

PENDING_PATH = Path(__file__).resolve().parent.parent / "state" / "pending-pins.json"


def load(path: Path = PENDING_PATH) -> dict:
    if not path.exists():
        return {"pending": []}
    data = json.loads(path.read_text())
    data.setdefault("pending", [])
    return data


def save(data: dict, path: Path = PENDING_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def add_pending(typed_title: str, typed_author: str | None,
                candidates: list[dict], kind: str = "book",
                path: Path = PENDING_PATH) -> dict:
    """Queue a record; replaces any existing record for the same
    normalized typed title (re-adds don't pile up)."""
    from .models import normalize_key

    key = normalize_key(typed_title)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    record = {
        "id": f"{key}-{stamp}",
        "kind": kind,
        "typed_title": typed_title,
        "typed_author": typed_author,
        "added": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "candidates": [
            {"title": c.get("title"), "author": c.get("author"),
             "format": c.get("format"), "bib_id": c.get("bib_id"),
             "isbn": c.get("isbn"), "source": c.get("source")}
            for c in candidates
        ],
    }
    data = load(path)
    data["pending"] = [r for r in data["pending"]
                       if normalize_key(r.get("typed_title", "")) != key]
    data["pending"].append(record)
    save(data, path)
    return record


def pop(path: Path, id: str) -> dict | None:
    """Remove and return the record with this id; None if absent.

    The remove-first order makes `tracker pin` idempotent: a stale
    double-tap finds nothing and exits cleanly."""
    data = load(path)
    for record in data["pending"]:
        if record.get("id") == id:
            data["pending"] = [r for r in data["pending"] if r is not record]
            save(data, path)
            return record
    return None
