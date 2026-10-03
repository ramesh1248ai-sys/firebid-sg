"""Obligations, cross-checks and the scope matrix, from the extended synthetic specification
(FR-SPEC-02, 03, 04).

The fixture's answers are exact: every obligation with its clause and quantities
(`EXPECTED_OBLIGATIONS`), every issue seeded between the specification and the drawings
(`SEEDED_ISSUES`), and whose each interface is (`EXPECTED_INTERFACES`).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from firebid.evals.synthetic_spec import (
    CAR_PARK_NOTES,
    EXPECTED_INTERFACES,
    EXPECTED_OBLIGATIONS,
    SEEDED_ISSUES,
    extended,
    specification_docx,
    specification_pdf,
)
from firebid.specs import attributes, crosscheck, obligations, scope_matrix, sections
from firebid.specs.clauses import Clause, parse
from firebid.specs.crosscheck import Note, SheetRef, SpecRef, SpecValue

FORMS: dict[str, Callable[[], bytes]] = {
    "docx": lambda: specification_docx(clauses=extended()),
    "pdf": lambda: specification_pdf(clauses=extended()),
}
REFERENCE = SpecRef("doc-1", "rev-1", "Rev B", "PARTICULAR SPECIFICATION")
CAR_PARK = SheetRef("s1", "FP-B1-201", "R01", "BASEMENT 1 CAR PARK SPRINKLER LAYOUT PLAN", "B1")
LEVEL_5 = SheetRef("s2", "FP-L05-201", "R01", "LEVEL 5 SPRINKLER LAYOUT PLAN", "L05")
DRAWN = {
    "sprinkler_pendent": [CAR_PARK, LEVEL_5],
    "sprinkler_upright": [CAR_PARK, LEVEL_5],
    "sprinkler_sidewall": [LEVEL_5],
    "gate_valve": [LEVEL_5],
    "check_valve": [LEVEL_5],
    "pipe": [LEVEL_5],
    "fitting_tee": [LEVEL_5],
}


@pytest.fixture(scope="module", params=list(FORMS))
def clauses(request: pytest.FixtureRequest) -> list[Clause]:
    return parse(FORMS[request.param](), request.param)


def values(clauses: list[Clause]) -> list[SpecValue]:
    return [
        SpecValue(
            a.system, a.attribute, a.value, a.clause, a.quote, a.dn_min, a.dn_max, a.condition
        )
        for a in attributes.extract(clauses, sections.systems(clauses))
    ]


def issues(clauses: list[Clause], notes: list[Note] | None = None) -> list[crosscheck.Issue]:
    stated = [Note(CAR_PARK, text, 15.0, 20.0) for text in CAR_PARK_NOTES]
    return crosscheck.check(
        clauses,
        sections.systems(clauses),
        values(clauses),
        stated if notes is None else notes,
        DRAWN,
        [CAR_PARK, LEVEL_5],
        REFERENCE,
    )


@pytest.mark.req("FR-SPEC-02")
class TestObligations:
    def test_every_category_is_extracted_with_its_clause_and_quantities(
        self, clauses: list[Clause]
    ) -> None:
        found = obligations.extract(clauses, sections.systems(clauses))

        assert sorted((o.category, o.clause, o.quantities) for o in found) == sorted(
            EXPECTED_OBLIGATIONS, key=lambda e: (e[0], e[1])
        )
        assert {o.category for o in found} == set(obligations.CATEGORY_KEYS)

    def test_every_citation_resolves_to_its_clause_s_words(self, clauses: list[Clause]) -> None:
        by_number = {clause.number: clause for clause in clauses}

        for found in obligations.extract(clauses, sections.systems(clauses)):
            holds, reason = obligations.quote_holds(by_number.get(found.clause), found.quote)
            assert holds, (found.category, reason)

    def test_a_quote_the_clause_does_not_contain_does_not_hold(self) -> None:
        clause = Clause("6.1", "Testing", "Pipework shall be tested at 14 bar.", 1)

        assert obligations.quote_holds(clause, "tested at 16 bar") == (
            False,
            "clause 6.1 does not contain the words quoted",
        )
        assert obligations.quote_holds(None, "anything")[0] is False

    def test_another_trade_s_section_obliges_nothing_here(self) -> None:
        clause = Clause("5.2", "Testing", "Cables shall be pressure tested at 2 bar.", 1)

        assert obligations.read(clause, "other") == []

    @pytest.mark.parametrize(
        ("sentence", "category", "stated"),
        [
            (
                "Pipework shall be tested to 1.5 times the working pressure for 24 hours.",
                "testing",
                {"times_working_pressure": 1.5, "duration_hours": 24},
            ),
            ("The system shall be guaranteed for 2 years.", "warranty", {"period_years": 2}),
            ("Spare heads of 5% of each type shall be handed over.", "spares", {"percent": 5}),
            (
                "The Contractor shall submit 3 copies of the as-built drawings.",
                "submittals",
                {"count": 3, "count_of": "copies"},
            ),
        ],
    )
    def test_quantities_as_consultants_write_them(
        self, sentence: str, category: str, stated: dict[str, object]
    ) -> None:
        [found] = obligations.read(Clause("9.1", "", sentence, 1), "sprinkler")

        assert (found.category, found.quantities) == (category, stated)

    def test_a_sentence_that_obliges_nothing_is_not_an_obligation(self) -> None:
        clause = Clause("9.1", "", "Testing records are kept by the Engineer.", 1)

        assert obligations.read(clause, "sprinkler") == []


@pytest.mark.req("FR-SPEC-03")
class TestCrossCheck:
    def test_every_seeded_conflict_missing_item_and_ambiguity_is_flagged(
        self, clauses: list[Clause]
    ) -> None:
        found = issues(clauses)

        assert sorted((i.rule, i.spec.get("clause"), i.detail["what"]) for i in found) == sorted(
            SEEDED_ISSUES, key=lambda s: (s[0], s[1] or "", s[2])
        )

    def test_the_p1_06_contradiction_cites_both_sides(self, clauses: list[Clause]) -> None:
        [conflict] = [i for i in issues(clauses) if i.rule == "conflict:pipe_material"]

        assert (conflict.category, conflict.severity) == ("conflict", "high")
        assert conflict.spec["clause"] == "2.1.3" and conflict.spec["revision"] == "Rev B"
        assert "hot-dip galvanised" in conflict.spec["quote"]
        assert conflict.spec["document_id"] == "doc-1"
        assert (conflict.drawing["sheet_number"], conflict.drawing["revision"]) == (
            "FP-B1-201",
            "R01",
        )
        assert conflict.drawing["note"] == CAR_PARK_NOTES[0]
        assert (conflict.detail["specified"], conflict.detail["drawn"]) == (
            "galvanised_steel",
            "black_steel",
        )

    def test_every_issue_cites_document_and_revision_on_both_sides(
        self, clauses: list[Clause]
    ) -> None:
        for issue in issues(clauses):
            assert issue.spec["document_revision_id"] and issue.spec["revision"], issue.title
            cited = (
                [issue.drawing] if issue.drawing.get("sheet_number") else issue.drawing["sheets"]
            )
            assert cited, issue.title
            assert all(sheet["sheet_number"] and sheet["revision"] for sheet in cited), issue.title

    def test_the_same_note_on_another_floor_is_no_conflict(self, clauses: list[Clause]) -> None:
        # Black steel is what the specification says everywhere but the car park.
        found = issues(clauses, [Note(LEVEL_5, CAR_PARK_NOTES[0])])

        assert not [i for i in found if i.rule == "conflict:pipe_material"]

    def test_a_note_that_agrees_raises_nothing(self, clauses: list[Clause]) -> None:
        found = issues(clauses, [Note(LEVEL_5, "PIPES 65 MM AND ABOVE: GROOVED COUPLINGS")])

        assert not [i for i in found if i.category == "conflict"]

    def test_what_the_drawings_show_stops_it_being_missing(self, clauses: list[Clause]) -> None:
        drawn = {**DRAWN, "test_header": [CAR_PARK]}
        found = crosscheck.missing(clauses, sections.systems(clauses), drawn, [CAR_PARK], REFERENCE)

        assert "missing_from_drawings" not in {i.rule for i in found}

    def test_two_values_with_nothing_to_choose_between_them_are_ambiguous(self) -> None:
        stated = [
            SpecValue("sprinkler", "joining_method", "grooved", "2.1.2", "... grooved ...", 65),
            SpecValue("sprinkler", "joining_method", "welded", "8.4", "... welded ...", 65),
        ]

        [issue] = crosscheck.ambiguities([], {}, stated, REFERENCE, [LEVEL_5])

        assert (issue.rule, issue.severity) == ("ambiguous_values", "medium")
        assert issue.spec["clause"] == "2.1.2" and issue.spec["also"][0]["clause"] == "8.4"

    def test_an_issue_keeps_its_key_from_one_run_to_the_next(self, clauses: list[Clause]) -> None:
        first = {i.key for i in issues(clauses)}

        assert first == {i.key for i in issues(clauses)} and len(first) == len(SEEDED_ISSUES)


@pytest.mark.req("FR-SPEC-04")
class TestScopeMatrix:
    def rows(self, clauses: list[Clause]) -> list[scope_matrix.Row]:
        placed = sections.systems(clauses)
        return scope_matrix.build(clauses, placed, obligations.extract(clauses, placed))

    def test_a_row_per_obligation_and_interface_for_each_system(
        self, clauses: list[Clause]
    ) -> None:
        rows = self.rows(clauses)

        assert sorted({row.system for row in rows}) == ["hose_reel", "hydrant", "sprinkler"]
        for system in ("sprinkler", "hose_reel", "hydrant"):
            interfaces = [
                (r.key, r.status, r.clause)
                for r in rows
                if r.system == system and r.kind == "interface" and r.key != "excavation"
            ]
            assert interfaces == list(EXPECTED_INTERFACES), system
            owed = {r.key: r for r in rows if r.system == system and r.kind == "obligation"}
            assert set(owed) == set(obligations.CATEGORY_KEYS)
            assert {r.status for r in owed.values()} == {"included"}

    def test_every_row_has_a_status_and_the_clause_it_was_read_from(
        self, clauses: list[Clause]
    ) -> None:
        by_number = {clause.number: clause for clause in clauses}

        for row in self.rows(clauses):
            assert row.status in scope_matrix.STATUSES
            if row.clause is None:
                assert (row.status, row.reason) == (
                    "unclear",
                    "the specification does not mention it",
                )
                continue
            assert row.quote and obligations.quote_holds(by_number[row.clause], row.quote)[0]

    def test_an_interface_for_one_system_is_on_that_system_only(
        self, clauses: list[Clause]
    ) -> None:
        excavation = [r.system for r in self.rows(clauses) if r.key == "excavation"]

        assert excavation == ["hydrant"]

    @pytest.mark.parametrize(
        ("sentence", "status"),
        [
            ("Openings shall be formed by the main contractor.", "by_others"),
            ("Access panels are not included in this contract.", "excluded"),
            ("The Contractor shall allow for all sleeves.", "included"),
            ("Pipework shall be painted.", "included"),
            ("Plinths are shown on the structural drawings.", "unclear"),
            ("Sleeves are excluded but the Contractor shall provide sealing.", "unclear"),
        ],
    )
    def test_whose_a_sentence_says_it_is(self, sentence: str, status: str) -> None:
        assert scope_matrix.status_of(sentence) == status
