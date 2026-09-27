"""Pipe sizes: read, attached, carried through a reducer, and flagged when they disagree
(FR-VIS-06)."""

from __future__ import annotations

from pathlib import Path

import pytest

from firebid.drawings import pipe_sizes
from firebid.evals.synthetic_network import network_plan
from tests.drawings.test_detection import FORMS, detected, lengths_by_dn

pytestmark = pytest.mark.req("FR-VIS-06")


@pytest.mark.parametrize(
    ("text", "dn"),
    [
        ("DN150", 150),
        ("DN 65", 65),
        ("150Ø", 150),
        ("Ø65", 65),
        ("150mm", 150),
        ("100 dia", 100),
        ('6"', 150),
        ('2-1/2"', 65),
        ('1 1/4" dia', 32),
        ("DN151", None),  # not a nominal size
        ("GRID B", None),
        ("150", None),  # a bare number is a dimension, not a size
    ],
)
def test_size_annotations_are_read_as_nominal_dn(text: str, dn: int | None) -> None:
    assert pipe_sizes.parse(text) == dn


@pytest.mark.parametrize("form", FORMS)
def test_a_size_carries_along_the_main_through_valves_and_tees_until_the_reducer(
    form: str, tmp_path: Path
) -> None:
    document, _ = network_plan()
    found = detected(document, form, tmp_path)

    mains = [run for run in found.runs if run.run_class == "main"]
    reducer_x = next(o.x for o in found.objects if o.object_type == "fitting")
    west = {run.dn for run in mains if max(p[0] for p in run.points) <= reducer_x}
    east = {run.dn for run in mains if min(p[0] for p in run.points) >= reducer_x}

    assert west == {150} and east == {100}
    statuses = {run.size_status for run in mains}
    assert statuses == {"labelled", "propagated"}, "one label each side, carried to the rest"


@pytest.mark.parametrize("form", FORMS)
def test_two_annotations_that_disagree_are_flagged_not_guessed(form: str, tmp_path: Path) -> None:
    document, truth = network_plan(conflict=True)
    found = detected(document, form, tmp_path)

    flagged = [run for run in found.runs if run.size_status == "conflict"]

    assert len(flagged) == 1
    [run] = flagged
    assert run.dn is None
    assert {label.text for label in run.labels} == {"DN50", "DN65"}
    assert "disagree" in run.size_reason
    assert lengths_by_dn(found)["conflict"] == pytest.approx(12_000, rel=0.005)
    assert lengths_by_dn(found)[50] == pytest.approx(truth.lengths[50] - 12_000, rel=0.005)
    assert run.raw_confidence < 0.3, "a flagged run goes to the top of the review queue"
