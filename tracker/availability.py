"""How long until you can actually read it — and when that's news.

Every library source reports the same three numbers in its own spelling:
copies owned, copies free right now, and how many people are in the queue.
From those we derive one wait in days, then quantize it onto a ladder of
buckets. The ladder is what notifications ratchet on: a queue shrinking
from 400 days to 380 is not news, but 400 to 60 is, and either way you
only hear about it once.

Why not use the wait the API hands us: OverDrive's `estimatedWaitDays` is
not an estimate at all. Fitted against live records it is exactly

    ceil((holdsCount + 1) / ownedCopies * 14)

— no history, no turnover data, and crucially *no reference to available
copies*, so a title sitting 3-of-3 on the shelf still reports 14 days.
It also hardcodes a 14-day turn, which is wrong at a 21-day library. Same
inputs, better arithmetic, so we do it ourselves.
"""
from __future__ import annotations

import math
from typing import Optional

# Bucket name -> inclusive upper bound in days. `turn` is the odd one out:
# its bound is the library's own loan period, so "front of the queue" means
# the same thing at 14-day Houston and 21-day Lewisville. None = unbounded.
#
# There is no step below `turn` on purpose. Being first in line and being
# third in line are the same decision — you place the hold and wait — so
# splitting them would only spend notifications on news you can't act on.
#
# These names are notification keys. Renaming one re-ratchets that bucket.
_LADDER: tuple[tuple[str, Optional[int]], ...] = (
    ("now", 0),           # a copy is free this minute
    ("turn", None),       # within one loan period: front of the queue
    ("plannable", 42),    # close enough to line up with the other format
    ("horizon", 90),
    ("someday", None),    # unbounded
)

BUCKETS: tuple[str, ...] = tuple(name for name, _ in _LADDER)

# Not a step on the ladder: we have the record but not the copy counts.
# Sorts last, and deliberately never sets or improves a watermark — "we
# finally learned the wait" is not the same news as "the wait got better".
UNKNOWN = "unknown"

_RANK = {name: i for i, name in enumerate(BUCKETS)}
_RANK[UNKNOWN] = len(BUCKETS)

# Physical copies further away than this are still reported, but they never
# headline a track or satisfy its watermark: a book on a shelf 200 miles
# away is not a book you can read this week. Digital sources are distance 0.
MAX_DISTANCE_MI = 60.0


def wait_days(available: Optional[int], owned: Optional[int],
              holds: Optional[int], loan_days: int) -> Optional[int]:
    """Days until a copy could plausibly reach you.

    A free copy is zero — that's the distinction the vendors' own numbers
    lose. With no copies owned we return None rather than infinity: an
    unowned record is a pre-release or a marketplace listing, not a queue.
    """
    if (available or 0) > 0:
        return 0
    if not owned:
        return None
    return math.ceil(((holds or 0) + 1) / owned * loan_days)


def wait_after_arrival(holds: Optional[int], copies: Optional[int],
                       loan_days: int) -> Optional[int]:
    """Queue time for a title the library has ordered but not received.

    Nothing is circulating yet, so the usual formula's assumption — that
    you're waiting out a loan already in progress — doesn't hold. The
    first `copies` holds are filled the day the box lands; you'd be hold
    number holds+1, so you wait that many turns *after* arrival.

    The arrival date itself is not published anywhere we can see, so this
    is deliberately only half the answer. Callers mark it provisional:
    good enough to rank against other options, never good enough to
    print as a date or to move a notification watermark.
    """
    if not copies:
        return None
    return math.ceil((holds or 0) / copies) * loan_days


def bucket(wait: Optional[int], loan_days: int) -> str:
    """Quantize a wait onto the ladder."""
    if wait is None:
        return UNKNOWN
    for name, bound in _LADDER:
        if name == "turn":
            bound = loan_days
        if bound is None:
            return name
        if wait <= bound:
            return name
    return BUCKETS[-1]


def rank(name: str) -> int:
    """Lower is better. Unknown sorts last."""
    return _RANK.get(name, len(BUCKETS))


def improves(current: str, best: Optional[str]) -> bool:
    """Should a track that we've already announced speak up again?

    Only for a known bucket that beats a previously *known* watermark.
    Setting the watermark for the first time is silent: the track's debut
    push already told you about the book, and repeating it to report the
    queue length would be the same news twice.
    """
    if current == UNKNOWN or best is None or best == UNKNOWN:
        return False
    return rank(current) < rank(best)


def better(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """The better of two buckets, tolerating None."""
    if a is None:
        return b
    if b is None:
        return a
    return a if rank(a) <= rank(b) else b


def describe(wait: Optional[int]) -> str:
    """Short human wait for a push or a card."""
    if wait is None:
        return "wait unknown"
    if wait <= 0:
        return "now"
    if wait < 14:
        return f"~{wait}d"
    if wait < 60:
        return f"~{round(wait / 7)} wk"
    if wait < 365:
        return f"~{round(wait / 30)} mo"
    return f"~{wait / 365:.1f} yr".replace(".0 yr", " yr")


def overlap(reading: Optional[int], listening: Optional[int],
            loan_days: int) -> Optional[tuple[int, bool]]:
    """Gap between the two formats, and whether one loan outlasts it.

    You read a book in a day or three, so the two holds don't have to land
    together — they only have to be in your hands at the same time, which
    is true whenever the gap is shorter than a loan. When it isn't, the gap
    is how long to suspend the hold that would otherwise arrive first.
    """
    if reading is None or listening is None:
        return None
    gap = abs(reading - listening)
    return gap, gap < loan_days
