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


def search_query(title: str) -> str:
    """A watched title trimmed down for a catalog search box.

    Parenthetical suffixes — series names, edition notes, the
    "(Ana and Din Mysteries)" that a Goodreads-style title carries —
    return ZERO results at cloudLibrary and OverDrive, which match the
    query as a phrase. Verified 2026-08: "A Trade of Blood (Ana and Din
    Mysteries)" found nothing at either, "A Trade of Blood" found it at
    both. Matching still happens against the full title via titles_match,
    which normalizes parentheticals away too.
    """
    cleaned = re.sub(r"\s+", " ", _PAREN_RE.sub(" ", title)).strip()
    return cleaned or title


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


def author_matches(wanted: str, found: str | None) -> bool:
    """True if a found author plausibly is the watched author.

    Any name token of the watched author has to turn up in the found
    author. Fails OPEN when the source didn't report an author — presence
    checks shouldn't drop records over missing metadata.

    This used to test the *last* token only, described as a surname check.
    It isn't one: watchlist authors are written "Surname, First", so the
    last token is the given name, and it passed because the given name is
    normally in the found string too. That works right up until the entry
    names more than one contributor — "Harpman, Jacqueline, Schwartz, Ros"
    (author plus translator) tested for "ros" against "Harpman, Jacqueline,
    author." and rejected a correct match, hiding a book Lewisville holds.
    Sources also hand us narrator lists, which have the same shape.

    Matching on any token is deliberately a superset of the old rule, so
    nothing that matched before stops matching. It stays a real guard
    because it runs *after* titles_match: its job is only to catch a fuzzy
    title landing on a different person, and a wrong author sharing no name
    token with the right one is the overwhelmingly common case.
    """
    if not found:
        return True
    # Bare initials and the year ranges catalogs staple on ("1963-") say
    # nothing about identity and would match far too much.
    names = {t for t in normalize_blob(wanted).split()
             if len(t) > 1 and not t.isdigit()}
    if not names:
        return True
    return bool(names & set(normalize_blob(found).split()))


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
