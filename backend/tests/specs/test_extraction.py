"""Specification attributes from the synthetic specification, DOCX and PDF (FR-SPEC-01, 05).

The fixture's answers are exact (`synthetic_spec.EXPECTED`): every attribute, its size range
and any condition, and the clause it comes from. The rules must read all of them, nothing
else, from both forms of the document; and every citation must hold up against its clause.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from firebid.evals.synthetic_spec import EXPECTED, specification_docx, specification_pdf
from firebid.specs import attributes, citations, sections
from firebid.specs.clauses import Clause, parse

FORMS: dict[str, Callable[[], bytes]] = {"docx": specification_docx, "pdf": specification_pdf}


@pytest.fixture(scope="module", params=list(FORMS))
def clauses(request: pytest.FixtureRequest) -> list[Clause]:
    return parse(FORMS[request.param](), request.param)


def key(item: object) -> tuple[object, ...]:
    return tuple(
        getattr(item, name)
        for name in ("system", "attribute", "value", "clause", "dn_min", "dn_max", "condition")
    )


@pytest.mark.req("FR-SPEC-01")
class TestTheClauseTree:
    def test_every_clause_is_found_with_its_number_and_anchor(self, clauses: list[Clause]) -> None:
        numbers = [clause.number for clause in clauses]

        assert numbers[:5] == ["1", "1.1", "1.2", "2", "2.1"]
        assert "2.1.3" in numbers and "5.1" in numbers
        assert all(clause.anchor for clause in clauses)
        [piping] = [c for c in clauses if c.number == "2.1.1"]
        assert piping.parent == "2.1" and piping.level == 3
        assert "Schedule 40" in piping.text

    def test_sections_are_placed_by_their_headings(self, clauses: list[Clause]) -> None:
        placed = sections.systems(clauses)

        assert placed["2.1.1"] == "sprinkler"
        assert placed["3.1"] == "hose_reel"
        assert placed["4.1"] == "hydrant"
        assert placed["5.1"] == "other", "the electrical section is not fire protection"
        assert placed["1.2"] == "general"

    def test_a_heading_the_rules_cannot_place_waits_for_the_model(self) -> None:
        tree = [Clause("7", "SPECIAL HAZARDS", "", 0), Clause("7.1", "", "Gas suppression.", 1)]

        assert [c.number for c in sections.unknown_sections(tree)] == ["7"]
        assert sections.systems(tree, {"7": "fire_protection"})["7.1"] == "fire_protection"


@pytest.mark.req("FR-SPEC-01")
class TestAttributes:
    def test_every_attribute_is_read_and_nothing_else(self, clauses: list[Clause]) -> None:
        found = attributes.extract(clauses, sections.systems(clauses))

        assert sorted(map(key, found), key=str) == sorted(map(key, EXPECTED), key=str)

    def test_joining_follows_the_size_ranges(self, clauses: list[Clause]) -> None:
        found = attributes.extract(clauses, sections.systems(clauses))
        joining = {
            (item.dn_min, item.dn_max): item.value
            for item in found
            if item.system == "sprinkler" and item.attribute == "joining_method"
        }

        assert joining == {(None, 50): "threaded", (65, None): "grooved"}

    def test_a_clause_for_one_place_is_kept_as_a_condition(self, clauses: list[Clause]) -> None:
        found = attributes.extract(clauses, sections.systems(clauses))
        [car_park] = [item for item in found if item.condition]

        assert (car_park.value, car_park.condition) == ("galvanised_steel", "basement car park")

    @pytest.mark.parametrize(
        ("sentence", "expected"),
        [
            ("Pipes up to and including DN 50 shall be screwed.", (None, 50)),
            ("Pipes 65 mm and above shall be grooved.", (65, None)),
            ("Pipes ≤ 50 shall be screwed.", (None, 50)),
            ("Pipes DN 100 and larger shall be flanged.", (100, None)),
            ("Joints shall be screwed throughout.", (None, None)),
        ],
    )
    def test_size_ranges_are_read_as_consultants_write_them(
        self, sentence: str, expected: tuple[int | None, int | None]
    ) -> None:
        assert attributes.size_range(sentence) == expected


@pytest.mark.req("FR-SPEC-05")
class TestCitations:
    def test_every_rule_citation_is_supported_by_its_clause(self, clauses: list[Clause]) -> None:
        for item in attributes.extract(clauses, sections.systems(clauses)):
            result = citations.check(clauses, item.clause, item.value)
            assert result.ok, (item, result.reason)

    def test_a_citation_to_the_wrong_clause_is_flagged_with_a_low_confidence(
        self, clauses: list[Clause]
    ) -> None:
        result = citations.check(clauses, "3.1", "grooved")  # the hose reel clause: screwed

        assert not result.ok
        assert "does not state" in result.reason
        assert citations.adjusted(0.95, result) == citations.FLAGGED_CONFIDENCE

    def test_a_citation_to_a_clause_that_does_not_exist_is_flagged(
        self, clauses: list[Clause]
    ) -> None:
        result = citations.check(clauses, "9.9.9", "grooved")

        assert not result.ok and "not in the specification" in result.reason
