"""Text-based edits to watchlist.yaml that preserve hand-written comments.

PyYAML round-trips would strip every comment from the file, so instead we
splice lines in and out of the raw text. Entries are matched/inserted at
the `books:` / `movies:` section markers.
"""
from __future__ import annotations

from pathlib import Path

import yaml


def append_entry(path: Path, section: str, entry: dict) -> bool:
    """Insert an entry under `books:`/`movies:`. Returns False (and prints
    the snippet for manual paste) if the section marker isn't found."""
    snippet = [f"  - title: {yaml_str(entry['title'])}"]
    for k, v in entry.items():
        if k != "title":
            snippet.append(f"    {k}: {yaml_str(v)}")

    lines = path.read_text().splitlines()
    for i, line in enumerate(lines):
        if line.strip() == f"{section}:" or line.strip() == f"{section}: []":
            lines[i] = f"{section}:"
            lines[i + 1:i + 1] = snippet
            path.write_text("\n".join(lines) + "\n")
            return True
    print(f"couldn't find a '{section}:' section in {path}; add manually:")
    print(f"{section}:")
    print("\n".join(snippet))
    return False


def remove_entry(path: Path, section: str, title: str) -> bool:
    """Remove the entry with this exact title from a section.

    Finds the `- title: ...` line whose parsed value equals `title`
    (so quoting differences don't matter) and drops it plus its
    following indented attribute lines. Returns False if not found.
    """
    lines = path.read_text().splitlines()
    in_section = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped in (f"{section}:", f"{section}: []"):
            in_section = True
            continue
        if in_section and stripped and not line.startswith(" ") and not stripped.startswith("#"):
            break  # next top-level key — left the section
        if not in_section or not stripped.startswith("- title:"):
            continue
        try:
            value = yaml.safe_load(stripped[len("- title:"):].strip())
        except yaml.YAMLError:
            continue
        if str(value) != title:
            continue
        end = i + 1
        entry_indent = len(line) - len(line.lstrip())
        while end < len(lines):
            nxt = lines[end]
            if not nxt.strip():
                break
            nxt_indent = len(nxt) - len(nxt.lstrip())
            if nxt_indent <= entry_indent or nxt.lstrip().startswith("- "):
                break
            end += 1
        del lines[i:end]
        path.write_text("\n".join(lines) + "\n")
        return True
    return False


def yaml_str(v: object) -> str:
    if isinstance(v, int):
        return str(v)
    s = str(v)
    if any(ch in s for ch in ":#'\"{}[]") or s != s.strip():
        return '"' + s.replace('"', '\\"') + '"'
    return s
