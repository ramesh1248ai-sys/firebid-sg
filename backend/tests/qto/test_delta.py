"""The delta report between two snapshots of a takeoff (FR-QTO-12)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from firebid.qto import delta
from firebid.qto.delta import Entry

pytestmark = pytest.mark.req("FR-QTO-12")

HEADS = "A1 Pendent sprinkler head"
PIPE = "A4 50 mm pipe (branch)"


def entry(
    human_id: str,
    quantity: str,
    state: str = "verified",
    *,
    line: str | None = HEADS,
    digest: str = "h1",
    unit: str = "no",
    version: int = 1,
) -> Entry:
    return Entry(
        human_id, version, f"item {human_id}", unit, Decimal(quantity), state, digest, line
    )


BASELINE = [
    entry("QTO-1", "16"),
    entry("QTO-2", "4"),
    entry("QTO-3", "72.000", line=PIPE, unit="m"),
    entry("QTO-4", "4"),
]
CURRENT = [
    entry("QTO-1", "17", "proposed", digest="h2", version=2),  # a head added
    entry("QTO-2", "4"),  # untouched, still verified
    entry("QTO-3", "72.000", line=PIPE, unit="m"),
    # QTO-4 is gone: its heads were removed
    entry("QTO-5", "12.000", "proposed", line=PIPE, unit="m"),  # a new size of pipe
]


def test_items_are_added_removed_changed_or_unchanged() -> None:
    report = delta.report(BASELINE, CURRENT)

    assert {i.human_id: i.change for i in report.items} == {
        "QTO-1": "changed",
        "QTO-2": "unchanged",
        "QTO-3": "unchanged",
        "QTO-4": "removed",
        "QTO-5": "added",
    }
    assert report.counts() == {
        "added": 1,
        "removed": 1,
        "changed": 1,
        "unchanged": 2,
        "verification_kept": 2,
        "to_review": 2,
    }


def test_a_changed_item_shows_its_previous_value() -> None:
    [changed] = [i for i in delta.report(BASELINE, CURRENT).items if i.change == "changed"]

    assert (changed.before, changed.after, changed.difference) == (
        Decimal(16),
        Decimal(17),
        Decimal(1),
    )
    assert (changed.state_before, changed.state_after) == ("verified", "proposed")
    assert changed.to_review and not changed.verification_kept


def test_quantity_changes_are_totalled_per_boq_line_against_the_baseline() -> None:
    lines = {line.line: line for line in delta.report(BASELINE, CURRENT).lines}

    # Heads: 16 + 4 + 4 before; 17 + 4 after. Pipe: 72 before; 72 + 12 after.
    assert (lines[HEADS].before, lines[HEADS].after, lines[HEADS].difference) == (
        Decimal(24),
        Decimal(21),
        Decimal(-3),
    )
    assert lines[HEADS].items == ("QTO-1", "QTO-4")
    assert lines[PIPE].difference == Decimal("12.000") and lines[PIPE].items == ("QTO-5",)


def test_a_line_nothing_changed_in_is_not_in_the_report() -> None:
    report = delta.report(BASELINE, BASELINE)

    assert report.lines == [] and report.changed() == []
    assert report.counts()["verification_kept"] == 4


def test_the_same_quantity_from_other_inputs_is_a_change() -> None:
    moved = [entry("QTO-2", "4", "proposed", digest="other")]

    [item] = delta.report([entry("QTO-2", "4")], moved).items

    assert item.change == "changed" and item.difference == 0


def test_a_rejected_item_counts_as_nothing() -> None:
    report = delta.report([entry("QTO-1", "16")], [entry("QTO-1", "16", "rejected")])

    assert [i.change for i in report.items] == ["removed"]


def test_an_item_in_no_bill_is_reported_under_its_own_heading() -> None:
    report = delta.report([], [entry("QTO-9", "2", "proposed", line=None)])

    assert [line.line for line in report.lines] == [delta.NO_LINE]


def test_a_snapshot_entry_survives_being_stored() -> None:
    original = entry("QTO-3", "72.000", line=PIPE, unit="m")

    assert Entry.from_json(original.as_json()) == original
