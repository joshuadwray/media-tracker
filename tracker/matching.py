"""Title normalization and fuzzy matching.

Search results and theater pages spell titles inconsistently ("The
Substance" vs "SUBSTANCE, THE" vs "The Substance (2024) - 35mm").
Everything funnels through normalize() before comparison.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

_ARTICLES = ("the ", "a ", "an ")
_PAREN_RE = re.compile(r"\([^)]*\)")
_NOISE_RE = re.compile(
    r"\b(35mm|70mm|4k|restoration|remastered|extended|director'?s cut|q&a|w/ q&a|imax)\b"
)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = _PAREN_RE.sub(" ", text)
    text = _NOISE_RE.sub(" ", text)
    # "substance, the" -> "the substance"
    m = re.match(r"^(.*),\s*(the|a|an)$", text.strip())
    if m:
        text = f"{m.group(2)} {m.group(1)}"
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    for art in _ARTICLES:
        if text.startswith(art):
            text = text[len(art):]
            break
    return text


def titles_match(wanted: str, found: str, threshold: float = 0.88) -> bool:
    """True if a found title is (fuzzily) the watched title."""
    a, b = normalize(wanted), normalize(found)
    if not a or not b:
        return False
    if a == b:
        return True
    # Prefix match covers subtitles ("nickel boys" ~ "nickel boys a novel")
    # but must not swallow sequels ("heat" vs "heat 2").
    for shorter, longer in ((a, b), (b, a)):
        if longer.startswith(shorter + " ") and not _sequel_suffix(
            longer[len(shorter) + 1:]
        ):
            return True
    return SequenceMatcher(None, a, b).ratio() >= threshold


_SEQUEL_WORDS = {
    "2", "3", "4", "5", "ii", "iii", "iv", "v", "part", "chapter",
    "vol", "volume", "returns", "reloaded",
}


def _sequel_suffix(remainder: str) -> bool:
    first = remainder.split()[0] if remainder.split() else ""
    return first in _SEQUEL_WORDS or first.isdigit()


def text_contains_title(page_text: str, title: str) -> bool:
    """True if a blob of page text mentions the title as a phrase.

    Used by the generic page watcher where we can't isolate individual
    listings — we just look for the normalized title as a substring of
    the normalized page text, on word boundaries.
    """
    hay = " " + normalize_blob(page_text) + " "
    needle = " " + normalize(title) + " "
    return bool(needle.strip()) and needle in hay


def normalize_blob(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
