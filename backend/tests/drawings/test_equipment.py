"""What drawings say of equipment in words: tags, schedules, level schedules, systems (P2-01).

Small hand-written sheets of text. The synthetic tender's own sheets are read through the
whole pipeline in `tests/qto/test_systems.py`.
"""

from __future__ import annotations

from typing import Any

import pytest

from firebid.drawings import equipment
from firebid.drawings.symbol_rules import load

pytestmark = pytest.mark.req("FR-VIS-04")


def span(text: str, x: float, y: float, height: float = 2.0) -> dict[str, Any]:
    return {
        "text": text,
        "minx": x,
        "miny": y,
        "maxx": x + 0.6 * height * len(text),
        "maxy": y + height,
        "height": height,
    }


def table(rows: list[list[tuple[str, float]]], top: float = 100.0, pitch: float = 6.0) -> list[Any]:
    """Lines of (text, x), the first at `top`, each `pitch` further down the sheet."""
    return [span(text, x, top + index * pitch) for index, row in enumerate(rows) for text, x in row]


class TestTags:
    @pytest.mark.parametrize(
        ("text", "tag"),
        [("FP-01", "FP-01"), ("JP 1", "JP-1"), ("HR-3A", "HR-3A"), ("FHC/2", "FHC-2")],
    )
    def test_a_tag_is_letters_and_a_number(self, text: str, tag: str) -> None:
        assert equipment.tag_of(text) == tag

    @pytest.mark.parametrize("text", ["DN100", "L05", "B1", "A", "150", "RISER R1", "FP-L05-201"])
    def test_sizes_levels_and_drawing_numbers_are_not_tags(self, text: str) -> None:
        assert equipment.tag_of(text) is None

    def test_fp_1_and_fp_01_are_one_tag(self) -> None:
        assert equipment.same_tag("FP-1", "FP-01")
        assert not equipment.same_tag("FP-1", "FP-10")

    def test_each_symbol_takes_the_nearest_tag_and_a_tag_is_given_once(self) -> None:
        # Two pumps 8 mm apart, each with its tag above it; the second tag is nearer the
        # first pump than the first pump's own tag is to the second.
        symbols = [(10.0, 10.0, 5.0), (18.0, 10.0, 5.0)]
        spans = [span("FP-01", 7.0, 13.0), span("FP-02", 14.0, 13.0), span("DN150", 9.0, 6.0)]

        found = equipment.tags(symbols, spans)

        assert {index: tagged.tag for index, tagged in found.items()} == {0: "FP-01", 1: "FP-02"}

    def test_a_tag_across_the_sheet_is_nobody_s(self) -> None:
        assert equipment.tags([(10.0, 10.0, 5.0)], [span("FP-01", 80.0, 80.0)]) == {}


class TestSchedules:
    def rows(self) -> list[Any]:
        header = [
            ("TAG", 10.0),
            ("DESCRIPTION", 30.0),
            ("DUTY", 80.0),
            ("FLOW (L/MIN)", 105.0),
            ("HEAD (M)", 135.0),
        ]
        return [
            span("FIRE PUMP SCHEDULE", 10.0, 92.0, 3.0),
            *table(
                [
                    header,
                    [
                        ("FP-01", 10.0),
                        ("ELECTRIC", 30.0),
                        ("DUTY", 80.0),
                        ("2850", 105.0),
                        ("80", 135.0),
                    ],
                    [
                        ("FP-02", 10.0),
                        ("DIESEL", 30.0),
                        ("STANDBY", 80.0),
                        ("2850", 105.0),
                        ("80", 135.0),
                    ],
                ]
            ),
        ]

    def test_a_row_per_tag_with_what_each_column_states(self) -> None:
        first, second = equipment.schedules(self.rows())

        assert (first.tag, first.heading) == ("FP-01", "FIRE PUMP SCHEDULE")
        assert first.values == {
            "description": "ELECTRIC",
            "duty": "duty",
            "flow_l_min": "2850",
            "head_m": "80",
        }
        assert second.values["duty"] == "standby"
        assert first.quote == "FP-01 | ELECTRIC | DUTY | 2850 | 80"

    @pytest.mark.parametrize(
        ("heading", "cell", "litres_a_minute"),
        [("FLOW (L/S)", "47.5", "2850"), ("FLOW (M3/H)", "171", "2850"), ("FLOW LPM", "90", "90")],
    )
    def test_a_flow_is_brought_to_litres_a_minute(
        self, heading: str, cell: str, litres_a_minute: str
    ) -> None:
        spans = [
            span("PUMP SCHEDULE", 10.0, 92.0, 3.0),
            *table([[("TAG", 10.0), (heading, 40.0)], [("FP-01", 10.0), (cell, 40.0)]]),
        ]

        [row] = equipment.schedules(spans)

        assert row.values == {"flow_l_min": litres_a_minute}
        assert row.written == {"flow_l_min": cell}

    def test_a_flow_with_no_unit_in_its_heading_is_not_guessed(self) -> None:
        spans = [
            span("PUMP SCHEDULE", 10.0, 92.0, 3.0),
            *table([[("TAG", 10.0), ("FLOW", 40.0)], [("FP-01", 10.0), ("47.5", 40.0)]]),
        ]

        [row] = equipment.schedules(spans)

        assert "flow_l_min" not in row.values

    def test_words_among_the_rows_that_are_not_the_table_s_are_passed_over(self) -> None:
        # A grid bubble's letter between two rows, and a legend across the sheet level with
        # the header: neither is a row or a column.
        spans = [*self.rows(), span("A", 4.0, 109.0), span("HOSE REEL", 300.0, 100.0)]

        assert [row.tag for row in equipment.schedules(spans)] == ["FP-01", "FP-02"]

    def test_the_table_ends_at_the_first_line_that_is_not_a_row(self) -> None:
        spans = [*self.rows(), span("NOTES", 10.0, 118.0), span("XX-09", 10.0, 124.0)]

        assert [row.tag for row in equipment.schedules(spans)] == ["FP-01", "FP-02"]

    def test_a_blank_cell_states_nothing(self) -> None:
        spans = [
            span("PUMP SCHEDULE", 10.0, 92.0, 3.0),
            *table([[("TAG", 10.0), ("DUTY", 40.0)], [("JP-01", 10.0), ("-", 40.0)]]),
        ]

        [row] = equipment.schedules(spans)

        assert row.values == {}

    def test_a_sheet_with_no_schedule_has_no_rows(self) -> None:
        assert (
            equipment.schedules([span("FP-01", 10.0, 10.0), span("GENERAL NOTES", 10.0, 20.0)])
            == []
        )


class TestLevelSchedule:
    def test_levels_with_their_floor_levels_lowest_first(self) -> None:
        spans = [
            span("L03 FFL +9.000", 10, 10),
            span("LEVEL 2 FFL 4500", 10, 20),
            span("B1 SSL -4.500", 10, 30),
            span("ROOF FFL +22.5 m", 10, 40),
            span("L05", 10, 50),
            span("DN100", 10, 60),
        ]

        marks = equipment.level_marks(spans)

        assert [(m.level, m.elevation_mm) for m in marks] == [
            ("B1", -4_500),
            ("L02", 4_500),
            ("L03", 9_000),
            ("RF", 22_500),
        ]


class TestSystems:
    @pytest.mark.parametrize(
        ("types", "categories", "words", "system"),
        [
            ({"hydrant"}, {"equipment"}, [], "hydrant"),
            ({"hose_reel"}, {"equipment"}, [], "hose_reel"),
            ({"landing_valve"}, {"equipment"}, [], "rising_main"),
            ({"landing_valve"}, {"equipment", "pipe"}, ["WET RISING MAIN"], "wet_riser"),
            ({"breeching_inlet"}, {"equipment"}, [], "rising_main"),
            ({"sprinkler_pendent", "gate_valve"}, {"sprinkler", "valve"}, [], "sprinkler"),
            (set(), {"pipe"}, ["DRY RISER"], "dry_riser"),
        ],
    )
    def test_what_stands_on_pipework_says_its_system(
        self, types: set[str], categories: set[str], words: list[str], system: str
    ) -> None:
        assert equipment.system_of(types, categories, words) == system

    def test_pipework_that_says_two_systems_or_none_is_not_given_one(self) -> None:
        assert equipment.system_of({"hydrant", "hose_reel"}, {"equipment"}, []) is None
        assert equipment.system_of({"gate_valve"}, {"valve"}, []) is None


class TestLegendRules:
    @pytest.mark.parametrize(
        ("description", "object_type"),
        [
            ("FIRE PUMP", "fire_pump"),
            ("HYDRANT PUMP (DUTY)", "fire_pump"),
            ("JOCKEY PUMP", "jockey_pump"),
            ("FIRE PUMP CONTROL PANEL", "pump_controller"),
            ("FIRE WATER TANK", "fire_water_tank"),
            ("BREECHING INLET", "breeching_inlet"),
            ("LANDING VALVE", "landing_valve"),
            ("PILLAR HYDRANT", "hydrant"),
            ("HOSE REEL", "hose_reel"),
            ("HOSEREEL DRUM", "hose_reel"),
            ("TEST HEADER", "test_header"),
            ("DRY PIPE VALVE SET", "dry_pipe_valve_set"),
            ("PRE-ACTION VALVE SET", "pre_action_valve_set"),
            ("DELUGE VALVE", "deluge_valve_set"),
            ("AIR COMPRESSOR", "air_compressor"),
            ("WET RISING MAIN", "pipe"),
            ("HOSE REEL RISER", "pipe"),
        ],
    )
    def test_equipment_as_consultants_name_it(self, description: str, object_type: str) -> None:
        proposal = load().propose(description)

        assert proposal is not None and proposal.object_type == object_type
