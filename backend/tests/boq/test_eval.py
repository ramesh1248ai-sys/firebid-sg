"""The client BOQ mapping suite (FR-BOQ-02: 90% of client lines mapped correctly)."""

from __future__ import annotations

import pytest

from firebid.evals.cli import main
from firebid.evals.p1_boq import TARGET, evaluate


@pytest.mark.req("FR-BOQ-02")
def test_the_rules_meet_the_mapping_target_on_the_synthetic_bill() -> None:
    report = evaluate()

    assert report.accuracy >= TARGET, report.markdown()
    assert {o.client_item for o in report.outcomes if o.truth is None} == {"C3", "D1", "D2"}


def test_the_suite_runs_from_the_command_line(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    out = tmp_path / "p1_boq.md"

    assert main(["run", "--suite", "p1_boq", "--report", str(out)]) == 0
    assert "client BOQ mapping" in out.read_text(encoding="utf-8")
