"""Shadow mode: a manual takeoff beside the AI-assisted one (requirements §13.3, §14)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from firebid.evals.shadow import AiItem, compare, manual_totals, synthetic_manual_takeoff

pytestmark = pytest.mark.req("FR-QTO-01")

AGREEING = [
    AiItem("sprinkler_pendent", "no", Decimal(16)),
    AiItem("sprinkler_upright", "no", Decimal(4)),
    AiItem("sprinkler_sidewall", "no", Decimal(4)),
    AiItem("gate_valve", "no", Decimal(1), 150),
    AiItem("check_valve", "no", Decimal(1), 150),
    AiItem("pipe", "m", Decimal("8.050"), 150),
    AiItem("pipe", "m", Decimal("8.250"), 100),
    AiItem("pipe", "m", Decimal("72.000"), 50),
]


def test_a_repeated_sheet_is_counted_once_in_the_manual_takeoff() -> None:
    counts, lengths = manual_totals(synthetic_manual_takeoff())

    assert counts["sprinkler_pendent"] == 16
    assert lengths == {150: 8050, 100: 8250, 50: 72000}


def test_the_comparison_is_line_by_line_and_says_where_they_differ() -> None:
    items = [i for i in AGREEING if i.item_type != "sprinkler_upright"] + [
        AiItem("sprinkler_upright", "no", Decimal(3)),
        AiItem("fitting_tee", "no", Decimal(6)),
    ]

    report = compare(synthetic_manual_takeoff(), items, manual_hours=10.0, ai_hours=6.0)

    lines = {(line.kind, line.key): line for line in report.lines}
    assert lines[("count", "sprinkler_pendent")].difference == 0
    assert lines[("count", "sprinkler_upright")].percent == pytest.approx(-25.0)
    assert lines[("length", "50")].manual == 72.0
    assert "fitting_tee" in report.notes[0]
    assert report.effort_reduction == pytest.approx(0.4)
    assert "meets the Phase 1 target" in report.markdown()


def test_no_workbench_time_says_the_effort_cannot_be_measured() -> None:
    report = compare(synthetic_manual_takeoff(), AGREEING, manual_hours=10.0, ai_hours=0.0)

    assert all(line.difference == 0 for line in report.lines)
    assert "cannot be measured yet" in report.markdown()
