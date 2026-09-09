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


def fold(text: str) -> str:
    """Drop diacritics: "Pérez-Carbonell" -> "Perez-Carbonell".

    Two sources rarely agree about accents on the same name — a log typed
    without them, a store record with them — and an unfolded comparison
    reads that as a different person entirely.
    """
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


# Sequels are the one place a title reliably gets respelled between the
# watchlist and a marquee: "dune part 3" typed by hand vs "Dune: Part
# Three" as the studio bills it scored 0.769 against the 0.88 threshold,
# so a season's biggest film went unseen at every source at once
# (verified 2026-09-09 against live Cinemark and Alamo feeds). Both sides
# run through normalize(), so folding the words to digits only ever
# merges two spellings of the same number. Roman numerals stay alone on
# purpose: "X" and "V" are real film titles, not part numbers.
_NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
    "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18",
    "nineteen": "19", "twenty": "20",
}


def _fold_numbers(text: str) -> str:
    if not text:
        return text
    return " ".join(_NUMBER_WORDS.get(t, t) for t in text.split())


def normalize(text: str) -> str:
    text = fold(text)
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
    return _fold_numbers(text)


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
    # Sequel numbers are one character in a long string, so the fuzzy
    # ratio cannot see them: "dune part 3" scores 0.909 against "dune
    # part 2" and sails past the threshold. Any number a title carries is
    # load-bearing, so the ratio only gets to decide among titles that
    # already agree on their numbers. The prefix rule above is exempt --
    # it is what lets "cars" match "cars 20th anniversary".
    if _numbers(a) != _numbers(b):
        return False
    return SequenceMatcher(None, a, b).ratio() >= threshold


def _numbers(normalized: str) -> list[str]:
    return [t for t in normalized.split() if t.isdigit()]


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
    """Loose normalization for haystacks: page text and author strings.

    Folds number words exactly like normalize() does. It has to: a
    watched title becomes the needle via normalize() and the page becomes
    the haystack via this, so folding on only one side would mean a
    marquee reading "Dune: Part Three" no longer contains its own title.
    """
    blob = re.sub(r"[^a-z0-9]+", " ", fold(text).lower()).strip()
    return _fold_numbers(blob)
