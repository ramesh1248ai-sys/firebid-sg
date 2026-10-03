"""The baseline productivity library (FR-LAB-01).

An entry says how many man-hours one unit of an item takes in ordinary conditions: a metre
of pipe by size and joining method, a sprinkler by type, a valve assembly, an equipment
item. Every entry has a source: the company standard, a historical project (which one), or
an estimator's judgement (whose). An entry without one is refused.

A BOQ line is matched to the most specific entry for its item key: its type, then its size
and joining method where the entry states them. An entry that leaves the size blank is for
every size of that type.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from firebid.pricing.keys import ItemKey, unit_of

SOURCES = ("company_standard", "historical_project", "estimator_judgement")
SOURCE_LABELS = {
    "company_standard": "company standard",
    "historical_project": "historical project",
    "estimator_judgement": "estimator judgement",
}


class Unsourced(ValueError):
    """A productivity entry or multiplier that does not say where it comes from."""


@dataclass(frozen=True)
class Entry:
    id: str
    type: str
    unit: str
    hours: Decimal  # man-hours per unit
    trade: str
    source_type: str
    source_reference: str
    dn: str = ""
    joining: str = ""
    description: str = ""

    @property
    def source(self) -> str:
        return f"{SOURCE_LABELS.get(self.source_type, self.source_type)}: {self.source_reference}"


def check_source(source_type: str, reference: str) -> None:
    """A source is one of the three kinds, and says which standard, project or person."""
    if source_type not in SOURCES:
        raise Unsourced(
            "a productivity entry's source is the company standard, a historical project "
            "or an estimator's judgement"
        )
    if not reference.strip():
        what = {
            "company_standard": "which standard",
            "historical_project": "which project",
            "estimator_judgement": "whose judgement",
        }[source_type]
        raise Unsourced(f"a productivity entry from {SOURCE_LABELS[source_type]} says {what}")


def check(entry: Entry) -> None:
    check_source(entry.source_type, entry.source_reference)
    if entry.hours <= 0:
        raise ValueError("man-hours per unit are more than zero")
    if not entry.type or not entry.trade:
        raise ValueError("a productivity entry has an item type and a trade")


def _fits(entry: Entry, key: ItemKey) -> int | None:
    """How specifically an entry fits a key, or None when it does not."""
    if entry.type == key.type:
        score = 4
    elif key.type.startswith(entry.type + "_"):
        score = 0  # "fitting" for every kind of fitting, "sprinkler" for every type
    else:
        return None
    for mine, theirs, weight in ((entry.dn, key.dn, 2), (entry.joining, key.joining, 1)):
        if not mine:
            continue
        if mine != theirs:
            return None
        score += weight
    return score


def match(key: ItemKey | None, unit: str | None, entries: Iterable[Entry]) -> Entry | None:
    """The most specific entry for an item key in its unit, or None."""
    if key is None or not key.type:
        return None
    wanted = unit_of(unit)
    best: tuple[int, Entry] | None = None
    for entry in entries:
        if unit_of(entry.unit) != wanted:
            continue
        score = _fits(entry, key)
        if score is None:
            continue
        if best is None or score > best[0] or (score == best[0] and entry.id < best[1].id):
            best = (score, entry)
    return best[1] if best else None
