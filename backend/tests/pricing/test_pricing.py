"""Pricing from the rate library: keys, matching, validity, totals and the rate list import
(FR-CST-01). Pure code: no database."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest

from firebid.domain.pricing import Unsourced, price_from
from firebid.domain.values import Money
from firebid.evals import synthetic_rates
from firebid.pricing import rates
from firebid.pricing.importer import read_workbook
from firebid.pricing.keys import ItemKey, unit_of

pytestmark = pytest.mark.req("FR-CST-01")


def entry(
    key: ItemKey,
    rate: str,
    *,
    unit: str = "m",
    source: str = "company_standard",
    effective: date = date(2026, 1, 1),
    until: date | None = None,
) -> rates.Entry:
    return rates.Entry(
        id=str(uuid.uuid4()),
        key=key,
        unit=unit,
        rate=Money.of(rate),
        source_type=source,
        source_reference="REF",
        effective_from=effective,
        valid_until=until,
    )


class TestKeys:
    def test_a_takeoff_item_s_key_folds_in_its_fitting_kind_and_drops_what_is_unstated(
        self,
    ) -> None:
        pipe = ItemKey.from_item(
            "pipe",
            {
                "nominal_diameter_mm": "150",
                "pipe_material": "not specified",
                "joining_method": "Grooved",
            },
        )
        reducer = ItemKey.from_item("fitting", {"fitting": "reducer", "nominal_diameter_mm": "150"})
        tee = ItemKey.from_item("fitting_tee", {"size": "DN100xDN50"})

        assert pipe.text() == "pipe|150|||grooved|"
        assert reducer.type == "fitting_reducer"
        assert tee.dn == "100x50"

    def test_items_disagreeing_on_a_part_leave_it_blank(self) -> None:
        a = ItemKey.of(type="pipe", dn="50", joining="screwed")
        b = ItemKey.of(type="pipe", dn="50", joining="grooved")

        common = ItemKey.common([a, b])

        assert common == ItemKey.of(type="pipe", dn="50")

    def test_a_blank_against_a_value_is_only_a_partial_match(self) -> None:
        line = ItemKey.of(type="fitting_tee", dn="100x50")

        assert line.partial(ItemKey.of(type="fitting_tee", dn="100x50", brand="victaulic"))
        assert not line.partial(ItemKey.of(type="fitting_tee", dn="150x50", brand="victaulic"))
        assert not line.partial(ItemKey.of(type="gate_valve", dn="100x50"))
        assert not line.partial(line), "the same key is an exact match, not a partial one"

    @pytest.mark.parametrize(
        ("unit", "canonical"), [("nr", "no"), ("No.", "no"), ("m", "m"), ("lm", "m"), ("kg", None)]
    )
    def test_units_as_people_write_them(self, unit: str, canonical: str | None) -> None:
        assert unit_of(unit) == canonical


class TestMatching:
    def test_an_exact_key_in_the_same_unit_matches(self) -> None:
        key = ItemKey.of(type="pipe", dn="50")
        found = rates.exact(
            key, "m", [entry(key, "21.35"), entry(ItemKey.of(type="pipe", dn="65"), "30")]
        )

        assert found is not None and found.rate == Money.of("21.35")
        assert rates.exact(key, "no", [entry(key, "21.35")]) is None, "a rate per m is not per nr"

    def test_of_two_entries_the_newest_wins_then_the_preferred_source(self) -> None:
        key = ItemKey.of(type="pipe", dn="50")
        old = entry(key, "20.00", effective=date(2026, 1, 1), source="quotation")
        new = entry(key, "22.00", effective=date(2026, 8, 1), source="company_standard")
        same_day_quote = entry(key, "21.00", effective=date(2026, 8, 1), source="quotation")

        assert rates.exact(key, "m", [old, new]) == new
        assert rates.exact(key, "m", [new, same_day_quote]) == same_day_quote

    def test_candidates_are_the_same_kind_and_size_in_the_same_unit(self) -> None:
        line = ItemKey.of(type="fitting_tee", dn="100x50")
        branded = entry(
            ItemKey.of(type="fitting_tee", dn="100x50", brand="victaulic"), "41.80", unit="nr"
        )
        other_size = entry(
            ItemKey.of(type="fitting_tee", dn="150x50", brand="victaulic"), "55", unit="nr"
        )

        assert rates.candidates(line, "no", [branded, other_size]) == [branded]


class TestValidity:
    KEY = ItemKey.of(type="pipe", dn="150")

    def test_a_rate_ending_before_the_tender_validity_is_warned_of(self) -> None:
        end = rates.tender_validity_end(datetime(2026, 10, 15, 12, 0), 90)
        short = entry(self.KEY, "68.20", until=date(2026, 10, 31))

        codes = [w.code for w in rates.warnings(short, date(2026, 9, 28), end)]

        assert end == date(2027, 1, 13)
        assert codes == ["ends_before_tender_validity"]

    def test_an_expired_rate_is_warned_of(self) -> None:
        expired = entry(self.KEY, "45.60", until=date(2026, 6, 30))

        codes = [w.code for w in rates.warnings(expired, date(2026, 9, 28), date(2027, 1, 13))]

        assert codes == ["expired", "ends_before_tender_validity"]

    def test_a_rate_valid_past_the_tender_is_quiet_and_an_unset_validity_says_so(self) -> None:
        fine = entry(self.KEY, "68.20", until=date(2027, 12, 31))

        assert rates.warnings(fine, date(2026, 9, 28), date(2027, 1, 13)) == []
        assert [w.code for w in rates.warnings(fine, date(2026, 9, 28), None)] == [
            "tender_validity_unknown"
        ]


class TestTotals:
    """A crafted BOQ, added up by hand. Half-up at the cent, line by line."""

    LINES = (
        # section, quantity, rate -> amount by hand
        ("A", Decimal("16"), "38.50"),  # 616.00
        ("A", Decimal("4"), "52.00"),  # 208.00
        ("B", Decimal("8.050"), "68.20"),  # 549.01 (549.010)
        ("B", Decimal("72.000"), "21.35"),  # 1537.20
        ("B", Decimal("0.125"), "0.10"),  # 0.0125 -> 0.01
        ("B", Decimal("0.500"), "0.05"),  # 0.025 -> 0.03 (half-up, not to even)
        ("B", Decimal("3.333"), "1.005"),  # rate 1.005 is 1.01 as Money: 3.36633 -> 3.37
        ("C", Decimal("1"), None),  # unpriced
    )

    def test_line_section_and_grand_totals_match_the_hand_calculation(self) -> None:
        lines = [
            rates.PricedLine(section, quantity, Money.of(rate) if rate else None)
            for section, quantity, rate in self.LINES
        ] + [rates.PricedLine("D", Decimal("1"), None, allowance=Money.of("5000"))]

        found = rates.totals(lines)

        assert rates.line_amount(Decimal("0.500"), Money.of("0.05")) == Money.of("0.03")
        assert rates.line_amount(Decimal("0.125"), Money.of("0.10")) == Money.of("0.01")
        assert found.sections == {
            "A": Money.of("824.00"),
            "B": Money.of("2089.62"),  # 549.01 + 1537.20 + 0.01 + 0.03 + 3.37
            "D": Money.of("5000.00"),
        }
        assert found.priced == Money.of("2913.62")
        assert found.allowances == Money.of("5000.00")
        assert found.grand == Money.of("7913.62")
        assert found.unpriced == 1
        assert found.gst_included is False


class TestDomain:
    def test_a_price_without_a_rate_entry_is_refused(self) -> None:
        with pytest.raises(Unsourced):
            price_from(None, Decimal("10"))

    def test_a_price_is_the_entry_s_rate_times_the_quantity(self) -> None:
        class Entry:
            id = uuid.uuid4()
            unit_rate = Money.of("21.35")

        price = price_from(Entry(), Decimal("72.000"))

        assert (price.rate_id, price.unit_rate, price.amount) == (
            Entry.id,
            Money.of("21.35"),
            Money.of("1537.20"),
        )


class TestImport:
    def test_the_synthetic_rate_list_reads_every_row(self) -> None:
        found = read_workbook(synthetic_rates.rate_list())

        assert found.problems == []
        assert len(found.rows) == len(synthetic_rates.RATES)
        tee = next(row for row in found.rows if row.parts["type"] == "fitting_tee")
        assert (tee.key, tee.unit, tee.rate, tee.source_type) == (
            "fitting_tee|100x50||||victaulic",
            "no",
            "41.80",
            "quotation",
        )

    def test_every_problem_is_reported_by_row_and_column(self) -> None:
        payload = synthetic_rates.rate_list(
            extra=[
                (
                    "pipe",
                    "65",
                    "",
                    "",
                    "",
                    "",
                    "Pipe DN65",
                    "m",
                    -3,
                    "PO",
                    "PO-1",
                    date(2026, 1, 1),
                    None,
                ),
                (
                    "pipe",
                    "80",
                    "",
                    "",
                    "",
                    "",
                    "Pipe DN80",
                    "kg",
                    30,
                    "PO",
                    "PO-1",
                    date(2026, 1, 1),
                    None,
                ),
                (
                    "pipe",
                    "90",
                    "",
                    "",
                    "",
                    "",
                    "Pipe DN90",
                    "m",
                    30,
                    "guess",
                    "X",
                    date(2026, 1, 1),
                    None,
                ),
                (
                    "pipe",
                    "95",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "m",
                    30,
                    "PO",
                    "PO-1",
                    "someday",
                    date(2025, 1, 1),
                ),
                (
                    "pipe",
                    "50",
                    "",
                    "",
                    "",
                    "",
                    "Again",
                    "m",
                    21.35,
                    "company standard",
                    "CS-2026",
                    date(2026, 1, 1),
                    None,
                ),
            ]
        )

        found = read_workbook(payload)

        by_row = {(p.row, p.column) for p in found.problems}
        first = 3 + len(synthetic_rates.RATES) + 1
        assert (first, "rate") in by_row
        assert (first + 1, "unit") in by_row
        assert (first + 2, "source type") in by_row
        assert {(first + 3, "description"), (first + 3, "effective from")} <= by_row
        assert (first + 4, "type") in by_row, "the same item, unit and source twice"
