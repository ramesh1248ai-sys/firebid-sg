"""The Phase 2 KPIs (requirements §14), as arithmetic.

* **Tender turnaround:** working days from the day a tender is received to the day it is
  ready to submit.
* **Price provenance:** priced lines with a valid source, over priced lines.
* **Clarification acceptance:** drafts issued with only minor edits, over drafts issued.
  "Minor" is an edit distance between the draft and what was issued, as a share of the
  longer of the two, at or under a configured threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


def working_days(start: date, end: date, holidays: frozenset[date] = frozenset()) -> int:
    """Working days from `start` to `end`, counting the days after `start` up to and
    including `end`: Monday to Friday, less the holidays given. Same day, or earlier: 0."""
    if end <= start:
        return 0
    count = 0
    day = start
    while day < end:
        day += timedelta(days=1)
        if day.weekday() < 5 and day not in holidays:
            count += 1
    return count


def _words(text: str) -> list[str]:
    return text.lower().split()


def edit_ratio(drafted: str, issued: str) -> float:
    """How much of a draft was changed before it was issued: the edit distance between the
    two, word by word, over the length of the longer. 0.0: issued as drafted; 1.0: nothing
    of the draft is left."""
    a, b = _words(drafted), _words(issued)
    if not a and not b:
        return 0.0
    previous = list(range(len(b) + 1))
    for i, word in enumerate(a, start=1):
        current = [i]
        for j, other in enumerate(b, start=1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (word != other))
            )
        previous = current
    return previous[-1] / max(len(a), len(b))


def minor_edits(drafted: str, issued: str, threshold: float) -> bool:
    return edit_ratio(drafted, issued) <= threshold


@dataclass(frozen=True)
class Share:
    """A KPI that is a share: so many of so many."""

    count: int
    of: int

    @property
    def value(self) -> float | None:
        return self.count / self.of if self.of else None


def provenance(lines: list[tuple[bool, bool]]) -> Share:
    """Priced lines with a valid source, over priced lines. Each line is (priced, sourced)."""
    priced = [sourced for is_priced, sourced in lines if is_priced]
    return Share(sum(1 for sourced in priced if sourced), len(priced))


def reduction(baseline: float | None, measured: float | None) -> float | None:
    """How far a measure is under its baseline, as a share of the baseline. None while
    either is unknown."""
    if not baseline or measured is None:
        return None
    return (baseline - measured) / baseline
