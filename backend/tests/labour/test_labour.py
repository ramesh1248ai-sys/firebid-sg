"""Labour estimation, without a database: the productivity library and its sources, the
multipliers, the hand calculation of hours, and the hourly rate built up from a table."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from firebid.evals import synthetic_labour
from firebid.labour import estimate as est
from firebid.labour import importer, multipliers, productivity, rates
from firebid.labour.productivity import Entry, Unsourced
from firebid.pricing import buildup
from firebid.pricing.keys import ItemKey

TODAY = date(2026, 10, 3)


def entry(**values: object) -> Entry:
    base: dict[str, object] = {
        "id": "e1",
        "type": "pipe",
        "unit": "m",
        "hours": Decimal("0.30"),
        "trade": "pipefitter",
        "source_type": "company_standard",
        "source_reference": "PS-2026",
    }
    return Entry(**{**base, **values})  # type: ignore[arg-type]


@pytest.mark.req("FR-LAB-01")
class TestProductivityLibrary:
    @pytest.mark.parametrize(
        ("source_type", "reference", "message"),
        [
            ("company_standard", "", "says which standard"),
            ("historical_project", " ", "says which project"),
            ("estimator_judgement", "", "says whose judgement"),
            ("a hunch", "someone", "company standard, a historical project"),
        ],
    )
    def test_an_entry_without_a_source_is_refused(
        self, source_type: str, reference: str, message: str
    ) -> None:
        with pytest.raises(Unsourced, match=message):
            productivity.check(entry(source_type=source_type, source_reference=reference))

    def test_an_entry_shows_its_source(self) -> None:
        found = entry(source_type="historical_project", source_reference="Tampines Hub (2025)")

        assert found.source == "historical project: Tampines Hub (2025)"

    def test_the_most_specific_entry_for_an_item_is_the_one_used(self) -> None:
        entries = [
            entry(id="any", hours=Decimal("0.35")),
            entry(id="dn50", dn="50", hours=Decimal("0.30")),
            entry(id="dn50-grooved", dn="50", joining="grooved", hours=Decimal("0.22")),
            entry(id="heads", type="sprinkler", unit="no", hours=Decimal("0.50")),
            entry(id="pendent", type="sprinkler_pendent", unit="no", hours=Decimal("0.40")),
        ]

        def used(key: ItemKey, unit: str) -> str | None:
            found = productivity.match(key, unit, entries)
            return found.id if found else None

        assert used(ItemKey.of(type="pipe", dn=50, joining="screwed"), "m") == "dn50"
        assert used(ItemKey.of(type="pipe", dn=50, joining="grooved"), "m") == "dn50-grooved"
        assert used(ItemKey.of(type="pipe", dn=80), "m") == "any"
        assert used(ItemKey.of(type="sprinkler_pendent"), "nr") == "pendent"
        assert used(ItemKey.of(type="sprinkler_upright"), "nr") == "heads"
        assert used(ItemKey.of(type="check_valve", dn=150), "nr") is None
        assert used(ItemKey.of(type="pipe", dn=50), "nr") is None, "another unit is another entry"

    def test_the_synthetic_list_reads_every_row(self) -> None:
        found = importer.read_json(synthetic_labour.productivity_list())

        assert found["problems"] == [] and found["sheet"] == "Productivity"
        assert len(found["rows"]) == len(synthetic_labour.ENTRIES)
        assert found["rows"][3] == {
            "row": 6,
            "type": "pipe",
            "dn": "150",
            "joining": "",
            "description": "Steel pipe DN150",
            "unit": "m",
            "hours": "0.6200",
            "trade": "pipefitter",
            "source_type": "historical_project",
            "source_reference": "Tampines Hub (2025)",
        }

    def test_every_problem_is_reported_by_row_and_column(self) -> None:
        bad = [
            ("pipe", 65, "", "No source", "m", 0.4, "pipefitter", "company standard", ""),
            ("pipe", 80, "", "Whose?", "m", 0.4, "pipefitter", "a feeling", "me"),
            ("pipe", 25, "", "No hours", "m", "fast", "pipefitter", "company standard", "PS"),
            ("pipe", 50, "", "Twice", "m", 0.3, "pipefitter", "company standard", "PS"),
        ]

        found = importer.read_json(synthetic_labour.productivity_list(extra=bad))

        assert [(p["row"], p["column"]) for p in found["problems"]] == [
            (11, "source reference"),
            (12, "source type"),
            (13, "man-hours per unit"),
            (14, "type"),
        ]


CONFIG = {
    "multipliers": {
        "height_bands": [
            {"key": "low", "up_to_mm": 3000, "value": 1.0, "source": "S", "rationale": "R"},
            {"key": "mid", "up_to_mm": 4500, "value": 1.1, "source": "S", "rationale": "R"},
            {"key": "high", "value": 1.4, "source": "S", "rationale": "R"},
        ],
        "conditions": [
            {"key": "basement", "value": 1.1, "source": "S", "rationale": "R"},
            {"key": "night_work", "value": 1.2, "source": "S", "rationale": "R"},
            {"key": "high_rise", "value": 1.1, "source": "S", "rationale": "R"},
        ],
    },
    "proposals": {"basement_levels": r"^(B\d+|BASEMENT.*)$", "high_rise_from_levels": 20},
}


@pytest.mark.req("FR-LAB-02")
class TestMultipliers:
    def test_the_shipped_catalogue_covers_every_condition_with_source_and_rationale(self) -> None:
        found = multipliers.catalogue()

        assert {m.key for m in found if m.kind == "condition"} == {
            "access_difficult",
            "mep_congestion",
            "basement",
            "occupied_building",
            "night_work",
            "high_rise",
        }
        assert len([m for m in found if m.kind == "height"]) == 4
        assert all(m.source and m.rationale and m.value > 0 for m in found)

    @pytest.mark.parametrize("missing", ["source", "rationale"])
    def test_a_multiplier_without_a_source_or_rationale_is_refused(self, missing: str) -> None:
        item = {"key": "dust", "value": 1.3, "source": "S", "rationale": "R"}
        item[missing] = " "

        with pytest.raises(Unsourced, match=f"'dust' has no {missing}"):
            multipliers.catalogue({"multipliers": {"conditions": [item]}})

    @pytest.mark.parametrize(
        ("height", "band"), [(2700, "low"), (3000, "low"), (3001, "mid"), (9000, "high")]
    )
    def test_a_height_falls_in_one_band(self, height: int, band: str) -> None:
        found = multipliers.height_band(height, multipliers.catalogue(CONFIG))

        assert found is not None and found.key == band

    def test_a_line_carries_its_level_s_multipliers_and_the_bid_s(self) -> None:
        items = multipliers.by_key(CONFIG)
        confirmed = [
            multipliers.Condition("night_work", None, "Esther"),
            multipliers.Condition("low", None, "Esther"),
            multipliers.Condition("high", "L05", "Esther"),
            multipliers.Condition("basement", "B1", "Sam"),
        ]

        def keys(level: str | None) -> list[tuple[str, str]]:
            return [(m.key, m.scope) for m in multipliers.applied_to(level, confirmed, items)]

        assert keys("B1") == [
            ("low", "the whole bid"),
            ("basement", "B1"),
            ("night_work", "the whole bid"),
        ]
        # One height band a line: its level's own, not the bid's as well.
        assert keys("L05") == [("high", "L05"), ("night_work", "the whole bid")]
        assert keys(None) == [("low", "the whole bid"), ("night_work", "the whole bid")]

    def test_parameters_propose_multipliers_for_a_person_to_confirm(self) -> None:
        found = multipliers.proposals(
            {None: (Decimal(2800), "entered by Esther"), "L05": (Decimal(5200), "note on FP-501")},
            ["B1", "L01", "L05"],
            (Decimal(24), "entered by Esther"),
            CONFIG,
        )

        assert [(p.key, p.level) for p in found] == [
            ("low", None),
            ("high", "L05"),
            ("basement", "B1"),
            ("high_rise", None),
        ]
        assert found[1].basis == "ceiling height 5200 mm (note on FP-501)"


def bill(line_id: str, quantity: str, level: str | None = None, unit: str = "m") -> est.BillLine:
    return est.BillLine(
        line_id,
        line_id,
        f"line {line_id}",
        "FIRE SPRINKLER INSTALLATION",
        level,
        unit,
        Decimal(quantity),
    )


RATE_TABLES = {
    "rate_tables": [
        {
            "effective_from": date(2025, 1, 1),
            "source": "Table 2025",
            "productive_hours_per_month": 208,
            "overtime": {"share_percent": 10, "premium": 1.5},
            "supervision": {"ratio": 8, "grade": "supervisor"},
            "insurance_percent": 2.0,
            "grades": {
                "skilled": {"wage": 2600, "levy": 500, "accommodation": 450, "transport": 120},
                "general": {"wage": 1800, "levy": 700, "accommodation": 450, "transport": 120},
                "supervisor": {"wage": 4200, "levy": 0, "accommodation": 0, "transport": 200},
            },
            "trades": {"pipefitter": {"crew": {"skilled": 50, "general": 50}}},
        },
        {
            "effective_from": date(2026, 7, 1),
            "source": "Table 2026 (levy raised)",
            "productive_hours_per_month": 208,
            "overtime": {"share_percent": 10, "premium": 1.5},
            "supervision": {"ratio": 8, "grade": "supervisor"},
            "insurance_percent": 2.0,
            "grades": {
                "skilled": {"wage": 2600, "levy": 600, "accommodation": 450, "transport": 120},
                "general": {"wage": 1800, "levy": 900, "accommodation": 450, "transport": 120},
                "supervisor": {"wage": 4200, "levy": 0, "accommodation": 0, "transport": 200},
            },
            "trades": {"pipefitter": {"crew": {"skilled": 50, "general": 50}}},
        },
    ]
}


@pytest.mark.req("FR-LAB-01")
@pytest.mark.req("FR-LAB-02")
class TestHours:
    def test_hours_are_baseline_times_each_multiplier_as_a_hand_calculation(self) -> None:
        items = multipliers.by_key(CONFIG)
        applied = multipliers.applied_to(
            "B1",
            [
                multipliers.Condition("mid", "B1", "Esther"),
                multipliers.Condition("basement", "B1", "Esther"),
                multipliers.Condition("night_work", None, "Sam"),
            ],
            items,
        )
        rate = rates.trade_rate("pipefitter", date(2026, 1, 1), rates.tables(RATE_TABLES))

        found = est.line_hours(bill("B1", "72.000", "B1"), entry(dn="50"), applied, rate)

        # 72 m x 0.30 h/m = 21.60 h; x 1.1 (height) x 1.1 (basement) x 1.2 (night) = 31.3632.
        assert found.baseline_hours == Decimal("21.60")
        assert [(m.key, m.value) for m in found.multipliers] == [
            ("mid", Decimal("1.1")),
            ("basement", Decimal("1.1")),
            ("night_work", Decimal("1.2")),
        ]
        assert found.factor == Decimal("1.452")
        assert found.hours == Decimal("31.36")
        assert found.cost is not None
        assert found.cost.amount == (Decimal("31.36") * rate.hourly).quantize(Decimal("0.01"))
        shown = found.as_json()
        assert shown["productivity_source"] == "company standard: PS-2026"
        assert [m["confirmed_by"] for m in shown["multipliers"]] == ["Esther", "Esther", "Sam"]

    def test_a_line_rolled_up_over_the_building_is_worked_level_by_level(self) -> None:
        items = multipliers.by_key(CONFIG)
        confirmed = [
            multipliers.Condition("basement", "B1", "Esther"),
            multipliers.Condition("night_work", None, "Sam"),
        ]
        rate = rates.trade_rate("pipefitter", date(2026, 1, 1), rates.tables(RATE_TABLES))
        by_level = [
            (level, Decimal(quantity), multipliers.applied_to(level, confirmed, items))
            for level, quantity in (("B1", "40.000"), ("L05", "32.000"))
        ]

        found = est.line_hours(
            bill("B1", "72.000"),
            entry(dn="50"),
            multipliers.applied_to(None, confirmed, items),
            rate,
            by_level,
        )

        # 40 m on B1: 12.00 h x 1.1 (basement) x 1.2 (night) = 15.84. 32 m on L05: 9.60 h
        # x 1.2 (night) = 11.52. The basement factor does not reach level 5.
        assert found.baseline_hours == Decimal("21.60")
        assert found.hours == Decimal("27.36")
        assert [(p.level, p.baseline_hours) for p in found.portions] == [
            ("B1", Decimal("12.00")),
            ("L05", Decimal("9.60")),
        ]
        assert [[m.key for m in p.multipliers] for p in found.portions] == [
            ["basement", "night_work"],
            ["night_work"],
        ]
        # The line shows each multiplier it carries anywhere, and what they come to.
        assert [m.key for m in found.multipliers] == ["basement", "night_work"]
        assert found.factor == Decimal("1.2667")
        assert found.cost is not None
        assert found.cost.amount == (Decimal("27.36") * rate.hourly).quantize(Decimal("0.01"))

    def test_a_line_all_on_one_level_comes_to_what_that_level_s_line_would(self) -> None:
        items = multipliers.by_key(CONFIG)
        confirmed = [multipliers.Condition("basement", "B1", "Esther")]
        applied = multipliers.applied_to("B1", confirmed, items)
        rate = rates.trade_rate("pipefitter", date(2026, 1, 1), rates.tables(RATE_TABLES))

        rolled_up = est.line_hours(
            bill("B1", "72.000"), entry(dn="50"), [], rate, [("B1", Decimal("72.000"), applied)]
        )
        on_its_level = est.line_hours(bill("B1", "72.000", "B1"), entry(dn="50"), applied, rate)

        assert (rolled_up.hours, rolled_up.factor, rolled_up.cost) == (
            on_its_level.hours,
            on_its_level.factor,
            on_its_level.cost,
        )

    def test_a_line_with_no_entry_has_no_hours_and_says_why(self) -> None:
        found = est.line_hours(bill("C4", "2", unit="nr"), None, [], None)

        assert found.hours is None and found.cost is None
        assert found.reason == "no productivity entry"

    def test_totals_are_per_system_and_by_trade(self) -> None:
        rate = rates.trade_rate("pipefitter", date(2026, 1, 1), rates.tables(RATE_TABLES))
        lines = [
            est.line_hours(bill("B1", "100"), entry(), [], rate),
            est.line_hours(bill("B2", "50"), entry(hours=Decimal("0.5")), [], rate),
            est.line_hours(bill("C4", "2", unit="nr"), None, [], None),
        ]

        found = est.estimate(lines)

        assert (found.baseline_hours, found.hours, found.without_hours) == (
            Decimal("55.00"),
            Decimal("55.00"),
            1,
        )
        [section] = found.by_section
        [trade] = found.by_trade
        assert (section.key, section.hours) == ("FIRE SPRINKLER INSTALLATION", Decimal("55.00"))
        assert (trade.key, trade.hours, trade.hourly_rate) == (
            "pipefitter",
            Decimal("55.00"),
            rate.hourly,
        )
        assert found.cost.amount == trade.cost == section.cost


@pytest.mark.req("FR-LAB-03")
class TestRateBuildUp:
    def test_the_hourly_rate_matches_a_hand_calculation_from_the_table(self) -> None:
        found = rates.trade_rate("pipefitter", date(2026, 1, 1), rates.tables(RATE_TABLES))

        skilled, general = found.grades
        # Skilled, an hour of 208 a month: wages 2600 -> 12.5000; levy 500 -> 2.4038;
        # accommodation 450 -> 2.1635; transport 120 -> 0.5769; insurance 2% of 2600 = 52
        # -> 0.2500; overtime 12.5 x 10% x 0.5 = 0.6250. A supervisor costs 4200 -> 20.1923,
        # transport 200 -> 0.9615, insurance 84 -> 0.4038, overtime 1.0096 = 22.5672 an hour,
        # an eighth of which is 2.8209.
        assert {c.key: c.hourly for c in skilled.components} == {
            "wage": Decimal("12.5000"),
            "levy": Decimal("2.4038"),
            "accommodation": Decimal("2.1635"),
            "transport": Decimal("0.5769"),
            "insurance": Decimal("0.2500"),
            "overtime": Decimal("0.6250"),
            "supervision": Decimal("2.8209"),
        }
        assert skilled.hourly == Decimal("21.3401")
        # General: 8.6538 + 3.3654 + 2.1635 + 0.5769 + 0.1731 + 0.4327 + 2.8209.
        assert general.hourly == Decimal("18.1863")
        # Half the crew's hours each.
        assert found.hourly == Decimal("19.7632")
        assert (found.effective_from, found.source) == (date(2025, 1, 1), "Table 2025")

    def test_a_changed_value_applies_only_from_its_effective_date(self) -> None:
        known = rates.tables(RATE_TABLES)

        before = rates.trade_rate("pipefitter", date(2026, 6, 30), known)
        after = rates.trade_rate("pipefitter", date(2026, 7, 1), known)

        assert before.hourly == Decimal("19.7632") and before.effective_from == date(2025, 1, 1)
        # The levy up 100 and 200 a month: (100 + 200) / 2 / 208 = 0.7212 an hour more.
        assert after.hourly == Decimal("20.4844") and after.effective_from == date(2026, 7, 1)

    def test_the_shipped_tables_build_every_trade_with_every_component(self) -> None:
        found = rates.trades_on(TODAY)

        assert {t.trade for t in found} == {"pipefitter", "sprinkler_fitter", "mechanical_fitter"}
        for trade in found:
            assert trade.hourly > 0 and trade.source
            for grade in trade.grades:
                assert [c.key for c in grade.components] == [key for key, _ in rates.COMPONENTS]

    def test_no_table_in_force_is_an_error_not_a_guess(self) -> None:
        with pytest.raises(ValueError, match="no labour rate table is configured for 2020"):
            rates.trade_rate("pipefitter", date(2020, 1, 1), rates.tables(RATE_TABLES))


@pytest.mark.req("FR-LAB-03")
class TestInTheCostBuildUp:
    def test_labour_is_a_calculated_line_with_its_basis(self) -> None:
        from firebid.domain.values import Money

        labour = buildup.Calculated(
            "labour",
            Money.of(Decimal("619.77")),
            "31.36 man-hours on 1 bill line(s)",
            "the library",
        )

        found = buildup.build([], [], TODAY, None, [labour])

        line = next(item for item in found.lines if item.component == "labour")
        assert (line.basis, line.amount) == ("calculated", Money.of(Decimal("619.77")))
        assert line.detail == "31.36 man-hours on 1 bill line(s)" and line.source == "the library"
        assert found.direct == Money.of(Decimal("619.77")) and "labour" not in found.not_set

    def test_an_estimator_s_own_figure_stands_and_says_what_was_calculated(self) -> None:
        from firebid.domain.values import Money

        labour = buildup.Calculated("labour", Money.of(Decimal("619.77")), "d", "s")
        entered = buildup.Entered(
            component="labour",
            basis="lump_sum",
            entered_by="Esther",
            entered_on=TODAY,
            amount=Money.of(Decimal(900)),
        )

        found = buildup.build([], [entered], TODAY, None, [labour])

        line = next(item for item in found.lines if item.component == "labour")
        assert (line.basis, line.amount) == ("lump_sum", Money.of(Decimal(900)))
        assert line.source.endswith("(in place of the calculated 619.77)")
