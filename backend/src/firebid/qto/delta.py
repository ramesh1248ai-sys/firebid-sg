"""The delta report: what a revision or addendum changed in the takeoff (FR-QTO-12).

A **snapshot** is the takeoff as it stood at a moment: each live item with its quantity,
state and the BOQ line it was in. The delta is the takeoff now against a snapshot, the
baseline:

* per **item**: added, removed, changed (its quantity or attributes, with the value
  before) or unchanged, and whether a person's verification still stands;
* per **BOQ line**: the quantity before and after, and the difference, for every line an
  item was added to, removed from or changed in.

Items are the same item when they have the same human ID: a recompute keeps the ID when an
item's inputs change, and supersedes the item when it is no longer found.

Pure: two snapshots in, a report out. Quantities are Decimal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

VERIFIED = ("verified", "baselined")
NO_LINE = "(not in the bill)"


@dataclass(frozen=True)
class Entry:
    """One item as a snapshot holds it."""

    human_id: str
    version: int
    description: str
    unit: str
    quantity: Decimal
    state: str
    inputs_hash: str | None = None
    line: str | None = None  # the BOQ line it is in: item number and description
    classification: str | None = None
    level: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "human_id": self.human_id,
            "version": self.version,
            "description": self.description,
            "unit": self.unit,
            "quantity": str(self.quantity),
            "state": self.state,
            "inputs_hash": self.inputs_hash,
            "line": self.line,
            "classification": self.classification,
            "level": self.level,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Entry:
        return cls(
            human_id=str(data["human_id"]),
            version=int(data["version"]),
            description=str(data["description"]),
            unit=str(data["unit"]),
            quantity=Decimal(str(data["quantity"])),
            state=str(data["state"]),
            inputs_hash=data.get("inputs_hash"),
            line=data.get("line"),
            classification=data.get("classification"),
            level=data.get("level"),
        )


@dataclass(frozen=True)
class ItemDelta:
    change: str  # added | removed | changed | unchanged
    human_id: str
    description: str
    unit: str
    before: Decimal | None
    after: Decimal | None
    state_before: str | None
    state_after: str | None
    line: str | None

    @property
    def difference(self) -> Decimal:
        return (self.after or Decimal(0)) - (self.before or Decimal(0))

    @property
    def verification_kept(self) -> bool:
        return self.state_before in VERIFIED and self.state_after in VERIFIED

    @property
    def to_review(self) -> bool:
        """Whether a person has this item to look at because of the change."""
        return self.change in ("added", "changed") and self.state_after not in VERIFIED

    def as_json(self) -> dict[str, Any]:
        return {
            "change": self.change,
            "human_id": self.human_id,
            "description": self.description,
            "unit": self.unit,
            "before": str(self.before) if self.before is not None else None,
            "after": str(self.after) if self.after is not None else None,
            "difference": str(self.difference),
            "state_before": self.state_before,
            "state_after": self.state_after,
            "line": self.line,
            "verification_kept": self.verification_kept,
            "to_review": self.to_review,
        }


@dataclass(frozen=True)
class LineDelta:
    line: str
    unit: str
    before: Decimal
    after: Decimal
    items: tuple[str, ...]

    @property
    def difference(self) -> Decimal:
        return self.after - self.before

    def as_json(self) -> dict[str, Any]:
        return {
            "line": self.line,
            "unit": self.unit,
            "before": str(self.before),
            "after": str(self.after),
            "difference": str(self.difference),
            "items": list(self.items),
        }


@dataclass
class Report:
    items: list[ItemDelta] = field(default_factory=list)
    lines: list[LineDelta] = field(default_factory=list)

    def changed(self) -> list[ItemDelta]:
        return [item for item in self.items if item.change != "unchanged"]

    def counts(self) -> dict[str, int]:
        tally = {"added": 0, "removed": 0, "changed": 0, "unchanged": 0}
        for item in self.items:
            tally[item.change] += 1
        tally["verification_kept"] = sum(1 for item in self.items if item.verification_kept)
        tally["to_review"] = sum(1 for item in self.items if item.to_review)
        return tally


def _changed(before: Entry, after: Entry) -> bool:
    if before.inputs_hash and after.inputs_hash:
        return before.inputs_hash != after.inputs_hash
    return (before.quantity, before.description) != (after.quantity, after.description)


def report(baseline: list[Entry], current: list[Entry]) -> Report:
    """The takeoff now against a baseline. A rejected item counts as nothing on its side."""
    was = {entry.human_id: entry for entry in baseline if entry.state != "rejected"}
    now = {entry.human_id: entry for entry in current if entry.state != "rejected"}
    items: list[ItemDelta] = []
    for human_id in sorted(set(was) | set(now)):
        before, after = was.get(human_id), now.get(human_id)
        shown = after or before
        if shown is None:  # for the type checker: the ID came from one of the two
            continue
        if before is None:
            change = "added"
        elif after is None:
            change = "removed"
        else:
            change = "changed" if _changed(before, after) else "unchanged"
        items.append(
            ItemDelta(
                change=change,
                human_id=human_id,
                description=shown.description,
                unit=shown.unit,
                before=before.quantity if before else None,
                after=after.quantity if after else None,
                state_before=before.state if before else None,
                state_after=after.state if after else None,
                line=(after.line if after and after.line else None)
                or (before.line if before else None),
            )
        )
    totals: dict[tuple[str, str], list[Any]] = {}
    for item in items:
        if item.change == "unchanged":
            continue
        key = (item.line or NO_LINE, item.unit)
        totals.setdefault(key, [Decimal(0), Decimal(0), []])
    for item in items:
        key = (item.line or NO_LINE, item.unit)
        if key not in totals:
            continue
        totals[key][0] += item.before or Decimal(0)
        totals[key][1] += item.after or Decimal(0)
        if item.change != "unchanged":
            totals[key][2].append(item.human_id)
    lines = [
        LineDelta(line, unit, before, after, tuple(ids))
        for (line, unit), (before, after, ids) in sorted(totals.items())
    ]
    return Report(items, lines)
