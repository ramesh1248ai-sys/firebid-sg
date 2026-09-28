"""No double counting across sheets and views (FR-QTO-08).

Each seeded repetition is found: an enlarged plan of the riser area, a match-lined pair of
plans overlapping on one branch, and a riser schematic. After de-duplication each tender's
quantities are the installation's, once, the same as from the general arrangement alone.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.qto.conftest import Tender


def quantities(tender: Tender, decisions: dict[str, str] | None = None) -> dict[str, Decimal]:
    return {item.description: item.net_quantity for item in tender.items(decisions=decisions)}


@pytest.mark.req("FR-QTO-08")
class TestSeededDuplicates:
    def test_the_general_arrangement_alone_repeats_nothing(self, general: Tender) -> None:
        assert general.groups == []

    def test_the_enlarged_plan_is_grouped_with_the_general_plan(
        self, with_enlarged_and_schematic: Tender
    ) -> None:
        groups = [g for g in with_enlarged_and_schematic.groups if g.kind == "enlarged_plan"]

        assert len(groups) == 1
        group = groups[0]
        assert group.status == "unresolved" and group.level == "L05"
        kept = {m["sheet_number"] for m in group.members if m["keep"]}
        dropped = {m["sheet_number"] for m in group.members if not m["keep"]}
        assert (kept, dropped) == ({"FP-L05-201"}, {"FP-L05-301"})
        # Every symbol the enlarged plan draws is a member, with where it is.
        enlarged = [m for m in group.members if m["sheet_number"] == "FP-L05-301"]
        assert {m["object_type"] for m in enlarged if m.get("detection_kind") == "object"} >= {
            "gate_valve",
            "check_valve",
        }
        assert all(m["grid_reference"] for m in group.members)

    def test_the_schematic_is_excluded_by_default_with_its_reason(
        self, with_enlarged_and_schematic: Tender
    ) -> None:
        groups = [g for g in with_enlarged_and_schematic.groups if g.kind == "schematic"]

        assert len(groups) == 1
        assert groups[0].status == "auto_excluded"
        assert "schematic" in groups[0].reason
        assert {m["sheet_number"] for m in groups[0].members} == {"FP-SCH-001"}

    def test_the_match_line_overlap_is_grouped(self, match_lined: Tender) -> None:
        (group,) = match_lined.groups

        assert group.kind == "match_line" and group.status == "unresolved"
        runs = [m for m in group.members if m["kind"] == "run" and not m["keep"]]
        assert sum(m["excluded_length_mm"] for m in runs) == 12_000 + 1_000 + 1_000
        # The east sheet's main is unlabelled where it overlaps: its size is carried over.
        assert any(m.get("carried_dn") == 150 for m in runs)

    @pytest.mark.parametrize("name", ["with_enlarged_and_schematic", "match_lined"])
    def test_each_tender_counts_the_installation_once(
        self, name: str, general: Tender, request: pytest.FixtureRequest
    ) -> None:
        tender: Tender = request.getfixturevalue(name)

        assert quantities(tender) == quantities(general)

    def test_a_person_can_say_a_group_is_not_a_duplicate(
        self, with_enlarged_and_schematic: Tender
    ) -> None:
        group = next(g for g in with_enlarged_and_schematic.groups if g.kind == "enlarged_plan")

        counted = quantities(with_enlarged_and_schematic, {group.key: "not_duplicate"})

        assert counted["Gate valve, DN150"] == 2
