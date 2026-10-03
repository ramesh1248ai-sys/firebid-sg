"""Quotations, landed cost, GST, the cost build-up, history and the ERP import (P2-04).

Pure: the synthetic quotations are read as their files are, and every sum is checked against
a hand calculation.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from firebid.domain.values import Money
from firebid.evals import synthetic_quotes as fixture
from firebid.parsing import quotation as reader
from firebid.pricing import buildup, erp, history, landed, quotation
from firebid.pricing.buildup import BillLine, Entered

TODAY = date(2026, 10, 3)
USD = landed.FxRate("USD", Decimal("1.3500"), "MAS reference rate", date(2026, 9, 30))
CONFIG = {
    "fx": {"buffer_percent": 2.0},
    "import_costs": [
        {"key": "freight", "label": "Freight", "percent": 5.0, "applies_to": ["EXW", "FOB"]},
        {
            "key": "insurance",
            "label": "Insurance",
            "percent": 0.5,
            "applies_to": ["EXW", "FOB", "CFR"],
        },
        {
            "key": "import_charges",
            "label": "Import and handling charges",
            "percent": 1.0,
            "applies_to": ["EXW", "FOB", "CFR", "CIF"],
        },
    ],
}


def read(payload: bytes, filename: str) -> quotation.Quote:
    kind = reader.kind_of(payload, filename)
    assert kind is not None
    return quotation.extract(reader.read(payload, kind)["lines"], TODAY)


@pytest.mark.req("FR-CST-02")
class TestQuotationCapture:
    @pytest.mark.parametrize(
        ("payload", "filename", "expected"),
        [
            (fixture.pdf_quote, "PV-Q-2026-1042.pdf", fixture.PDF),
            (fixture.xlsx_quote, "LCS quotation.xlsx", fixture.WORKBOOK),
            (fixture.email_quote, "Quotation AFP-7731.eml", fixture.EMAIL),
        ],
        ids=["pdf", "xlsx", "email"],
    )
    def test_every_listed_field_is_extracted(
        self, payload: object, filename: str, expected: fixture.Expected
    ) -> None:
        quote = read(payload(), filename)  # type: ignore[operator]

        assert quote.missing == []
        assert quote.value("supplier") == expected.supplier
        assert quote.value("quote_number") == expected.quote_number
        assert quote.value("quote_date") == expected.quote_date
        assert quote.value("valid_until") == expected.valid_until
        assert quote.value("currency") == expected.currency
        assert quote.value("delivery_terms") == expected.delivery_terms
        assert quote.value("incoterm") == expected.incoterm
        assert [
            (
                line.description,
                line.brand,
                line.model,
                line.unit,
                line.unit_price,
                line.moq,
                line.lead_time,
            )
            for line in quote.lines
        ] == [
            (e.description, e.brand, e.model, e.unit, e.unit_price, e.moq, e.lead_time)
            for e in expected.lines
        ]
        assert [read.value for read in quote.exclusions] == list(expected.exclusions)

    def test_every_field_says_which_line_of_the_file_it_was_read_from(self) -> None:
        quote = read(fixture.email_quote(), "q.eml")

        assert quote.fields["supplier"].source["part"] == "headers"
        assert quote.fields["valid_until"].source["part"] == "body"
        assert "Valid until: 24/11/2026" in quote.fields["valid_until"].source["text"]
        assert quote.lines[0].source["part"] == "AFP-7731.xlsx"
        assert quote.lines[0].source["sheet"] == "Quotation" and quote.lines[0].source["row"] == 2

    def test_a_validity_in_days_runs_from_the_quotation_s_date(self) -> None:
        quote = read(fixture.xlsx_quote(), "q.xlsx")

        assert quote.fields["valid_until"].source["text"] == "Validity | 30 days"
        assert quote.value("valid_until") == date(2026, 10, 20)

    def test_what_the_file_does_not_state_is_missing_not_guessed(self) -> None:
        lines = [
            {"part": "document", "row": 1, "cells": ["Description", "Unit price"]},
            {"part": "document", "row": 2, "cells": ["Gate valve DN150", "to be advised"]},
            {"part": "document", "row": 3, "cells": ["Check valve DN150", "86.50"]},
        ]

        quote = quotation.extract(lines)

        assert [line.description for line in quote.lines] == ["Check valve DN150"]
        assert quote.missing == [
            "supplier",
            "quote_number",
            "quote_date",
            "valid_until",
            "currency",
        ]

    def test_an_outlook_msg_is_not_read(self) -> None:
        compound = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64

        assert reader.kind_of(compound, "quote.msg") is None
        with pytest.raises(ValueError, match=r"PDF, a workbook or an \.eml"):
            reader.read(compound, "msg")

    @pytest.mark.parametrize(
        ("text", "value"),
        [
            ("1,250.00", Decimal("1250.00")),
            ("USD 86.5", Decimal("86.5")),
            ("TBA", None),
            ("", None),
        ],
    )
    def test_a_price_is_read_as_written_or_not_at_all(
        self, text: str, value: Decimal | None
    ) -> None:
        assert quotation.parse_money(text) == value


@pytest.mark.req("FR-CST-02")
@pytest.mark.parametrize(
    ("words", "code"),
    [
        ("Prices in SGD, firm for acceptance", "SGD"),
        ("firm for 30 days", None),
        ("US$ 100.00", "USD"),
        ("S$18.40", "SGD"),
        ("RM 40.00 per length", "MYR"),
        ("Unit price (RMB)", "CNY"),
    ],
)
def test_a_currency_is_its_code_or_a_symbol_before_a_figure(words: str, code: str | None) -> None:
    assert quotation.currency_in(words) == code


@pytest.mark.req("FR-CST-03")
class TestValidityFlags:
    TENDER_ENDS = date(2026, 12, 31)

    def codes(self, valid_until: date | None, exclusions: list[str] | None = None) -> list[str]:
        found = quotation.flags(valid_until, exclusions or [], TODAY, self.TENDER_ENDS)
        return [flag.code for flag in found]

    def test_an_expired_quotation_is_flagged(self) -> None:
        assert self.codes(date(2026, 9, 30)) == ["expired"]

    def test_a_quotation_that_ends_before_the_tender_s_validity_is_flagged(self) -> None:
        [flag] = quotation.flags(date(2026, 12, 14), [], TODAY, self.TENDER_ENDS)

        assert flag.code == "ends_before_tender"
        assert "2026-12-14" in flag.message and "2026-12-31" in flag.message

    def test_a_quotation_with_exclusions_is_flagged(self) -> None:
        [flag] = quotation.flags(
            date(2027, 1, 31), ["Import duties and GST"], TODAY, self.TENDER_ENDS
        )

        assert (flag.code, flag.message) == (
            "exclusions",
            "the quotation excludes: Import duties and GST",
        )

    def test_one_that_outlasts_the_tender_and_excludes_nothing_is_not(self) -> None:
        assert self.codes(date(2027, 1, 31)) == []
        assert self.codes(None) == ["no_validity"]

    def test_the_fixtures_are_flagged_as_they_should_be(self) -> None:
        def flagged(expected: fixture.Expected) -> list[str]:
            return self.codes(expected.valid_until, list(expected.exclusions))

        assert flagged(fixture.PDF) == ["ends_before_tender", "exclusions"]
        assert flagged(fixture.WORKBOOK) == ["ends_before_tender"]
        assert flagged(fixture.EMAIL) == ["ends_before_tender", "exclusions"]


@pytest.mark.req("FR-CST-04")
class TestLandedCost:
    def test_a_usd_quote_converts_with_the_fx_rate_buffer_and_import_lines(self) -> None:
        found = landed.landed(Decimal("100.00"), "USD", fx=USD, delivery_terms="FOB", config=CONFIG)

        # By hand: 100.00 x 1.35 = 135.00; buffer 2% = 2.70, so 137.70; freight 5% = 6.885;
        # insurance 0.5% = 0.6885; import charges 1% = 1.377; in all 146.6505, so 146.65.
        assert [(line.key, line.amount) for line in found.lines] == [
            ("price", Decimal("135.0000")),
            ("fx_buffer", Decimal("2.7000")),
            ("freight", Decimal("6.8850")),
            ("insurance", Decimal("0.6885")),
            ("import_charges", Decimal("1.3770")),
        ]
        assert found.unit_cost == Money(Decimal("146.65"))
        assert found.lines[0].basis == "100.00 USD x 1.3500 (MAS reference rate, 2026-09-30)"
        assert found.as_json()["fx"] == {
            "rate": "1.3500",
            "source": "MAS reference rate",
            "as_of": "2026-09-30",
        }

    def test_a_cif_price_takes_import_charges_only(self) -> None:
        found = landed.landed(Decimal("100.00"), "USD", fx=USD, delivery_terms="CIF", config=CONFIG)

        assert [line.key for line in found.lines] == ["price", "fx_buffer", "import_charges"]
        assert found.unit_cost == Money(Decimal("139.08"))  # 137.70 + 1.377

    def test_a_delivered_sgd_price_is_the_price(self) -> None:
        found = landed.landed(Decimal("18.40"), "SGD", delivery_terms="DDP", config=CONFIG)

        assert [line.key for line in found.lines] == ["price"]
        assert found.unit_cost == Money(Decimal("18.40")) and found.fx is None

    def test_a_freight_quote_in_hand_replaces_the_default_percentage(self) -> None:
        found = landed.landed(
            Decimal("100.00"),
            "USD",
            fx=USD,
            delivery_terms="FOB",
            config=CONFIG,
            overrides={"freight": Decimal("8")},
        )

        assert dict((line.key, line.amount) for line in found.lines)["freight"] == Decimal(
            "11.0160"
        )

    def test_no_recorded_rate_no_conversion(self) -> None:
        with pytest.raises(landed.NoFxRate, match="no FX rate is recorded for EUR"):
            landed.landed(Decimal("10"), "EUR", fx=USD, config=CONFIG)

    def test_the_latest_rate_on_or_before_the_day_is_used(self) -> None:
        rates = [
            landed.FxRate("USD", Decimal("1.32"), "MAS", date(2026, 8, 31)),
            USD,
            landed.FxRate("USD", Decimal("1.40"), "MAS", date(2026, 10, 31)),
        ]

        assert landed.latest_rate(rates, "usd", TODAY) == USD
        assert landed.latest_rate(rates, "EUR", TODAY) is None


@pytest.mark.req("FR-CST-05")
class TestGst:
    RATES = (
        landed.GstRate(date(2023, 1, 1), Decimal(8)),
        landed.GstRate(date(2024, 1, 1), Decimal(9)),
    )

    def test_gst_is_computed_separately_at_the_configured_rate(self) -> None:
        found = landed.gst_on(Money(Decimal("12345.67")), TODAY, list(self.RATES))

        # 12,345.67 x 9% = 1,111.1103, so 1,111.11.
        assert (found.rate.percent, found.gst, found.inclusive) == (
            Decimal(9),
            Money(Decimal("1111.11")),
            Money(Decimal("13456.78")),
        )
        assert found.exclusive == Money(Decimal("12345.67"))

    def test_a_new_rate_changes_only_bids_priced_from_its_date(self) -> None:
        later = [*self.RATES, landed.GstRate(date(2027, 1, 1), Decimal(10))]
        total = Money(Decimal("1000.00"))

        before = landed.gst_on(total, date(2026, 12, 31), later)
        after = landed.gst_on(total, date(2027, 1, 1), later)

        assert (before.gst, after.gst) == (Money(Decimal("90.00")), Money(Decimal("100.00")))
        # The bid priced before it is what it was without the new rate.
        assert before.gst == landed.gst_on(total, date(2026, 12, 31), list(self.RATES)).gst

    def test_the_configured_table_is_read_with_its_effective_dates(self) -> None:
        rates = landed.gst_rates()

        assert landed.gst_rate_on(date(2023, 6, 1), rates).percent == Decimal(8)
        assert landed.gst_rate_on(TODAY, rates).percent == Decimal(9)
        with pytest.raises(ValueError, match="no GST rate is configured"):
            landed.gst_rate_on(date(2020, 1, 1), rates)


def money(value: str) -> Money:
    return Money(Decimal(value))


BILL = [
    BillLine("A1", "Sprinkler heads", Decimal(16), money("156.80"), money("9.80"), Decimal(0)),
    BillLine("A4", "Pipework", Decimal("72.000"), money("1324.80"), money("18.40"), Decimal(5)),
    BillLine("A9", "Fittings", Decimal(6), money("90.00"), money("15.00"), Decimal(0)),
    BillLine("A14", "Valves and ancillaries", Decimal(1), money("146.65"), money("146.65")),
    BillLine("A20", "Equipment", Decimal(1), money("5000.00"), allowance=True),
    BillLine("A15", "Valves and ancillaries", Decimal(1), None),
]
ESTHER = ("Esther Tan", TODAY)


def entered(component: str, **given: object) -> Entered:
    basis = "percentage" if "percent" in given else "lump_sum"
    return Entered(component, basis, *ESTHER, **given)  # type: ignore[arg-type]


@pytest.mark.req("FR-CST-06")
class TestBuildUp:
    ENTERED = (
        entered("labour", amount=money("2500.00"), note="placeholder until P2-05"),
        entered("supervision", amount=money("400.00")),
        entered("testing_commissioning", amount=money("300.00")),
        entered("site_overheads", percent=Decimal(5), base="direct"),
        entered("preliminaries", percent=Decimal(3), base="direct"),
        entered("contingency", percent=Decimal("2.5"), base="cost"),
        entered("margin", percent=Decimal(10), base="cost_with_contingency"),
    )

    def test_every_component_is_a_separate_line_with_its_basis(self) -> None:
        found = buildup.build(BILL, list(self.ENTERED), TODAY)

        assert [line.component for line in found.lines] == list(buildup.KEYS)
        assert len(found.lines) == 17
        assert {line.component: line.basis for line in found.lines} == {
            "materials": "calculated",
            "fittings": "calculated",
            "valves": "calculated",
            "equipment": "calculated",
            "wastage": "calculated",
            "labour": "lump_sum",
            "supervision": "lump_sum",
            "access_equipment": "not set",
            "testing_commissioning": "lump_sum",
            "transport": "not set",
            "subcontract": "not set",
            "site_overheads": "percentage",
            "preliminaries": "percentage",
            "insurance": "not set",
            "bonds": "not set",
            "contingency": "percentage",
            "margin": "percentage",
        }

    def test_the_totals_match_a_hand_calculation(self) -> None:
        found = buildup.build(BILL, list(self.ENTERED), TODAY)
        amounts = found.by_component()

        # Materials 156.80 + 1,324.80; wastage 72 m x 5% x 18.40 = 66.24.
        assert amounts["materials"] == money("1481.60")
        assert amounts["fittings"] == money("90.00")
        assert amounts["valves"] == money("146.65")
        assert amounts["equipment"] == money("5000.00")
        assert amounts["wastage"] == money("66.24")
        # Direct: 1,481.60 + 90.00 + 146.65 + 5,000.00 + 66.24 + 2,500 + 400 + 300.
        assert found.direct == money("9984.49")
        assert amounts["site_overheads"] == money("499.22")  # 5% of 9,984.49 = 499.2245
        assert amounts["preliminaries"] == money("299.53")  # 3% = 299.5347
        assert found.cost == money("10783.24")
        assert amounts["contingency"] == money("269.58")  # 2.5% of 10,783.24 = 269.581
        assert found.cost_with_contingency == money("11052.82")
        assert amounts["margin"] == money("1105.28")  # 10% = 1,105.282
        assert found.total == money("12158.10")
        assert (found.gst.gst, found.gst.inclusive) == (money("1094.23"), money("13252.33"))
        assert found.unpriced_lines == 1
        assert found.not_set == [
            "access_equipment",
            "transport",
            "subcontract",
            "insurance",
            "bonds",
        ]

    def test_each_line_says_where_its_figure_came_from(self) -> None:
        lines = {
            line.component: line for line in buildup.build(BILL, list(self.ENTERED), TODAY).lines
        }

        assert lines["labour"].source == (
            "entered by Esther Tan on 2026-10-03: placeholder until P2-05"
        )
        assert lines["margin"].detail == "10% of cost with contingency 11052.82"
        assert (
            lines["equipment"].detail == "1 priced bill line(s), 1 of them an estimator's allowance"
        )
        assert lines["materials"].source == "the priced bill of quantities"
        assert lines["transport"].amount is None and lines["transport"].source == "not set"

    def test_nothing_is_filled_in_for_a_component_nobody_entered(self) -> None:
        found = buildup.build(BILL, [], TODAY)

        assert found.total == found.direct == money("6784.49")
        assert all(line.amount is None for line in found.lines if line.component in buildup.ENTERED)

    @pytest.mark.parametrize(
        ("given", "message"),
        [
            (
                Entered("margin", "lump_sum", "", TODAY, amount=money("1.00")),
                "name of the estimator",
            ),
            (Entered("materials", "lump_sum", *ESTHER, amount=money("1.00")), "not a component"),
            (entered("margin", percent=Decimal(150), base="cost"), "between 0 and 100"),
            (
                entered("site_overheads", percent=Decimal(5), base="cost"),
                "percentage of direct cost",
            ),
            (entered("labour", percent=Decimal(5), base="direct"), "enter it as a lump sum"),
            (Entered("bonds", "lump_sum", *ESTHER), "an amount of money"),
        ],
    )
    def test_an_entry_that_makes_no_sense_is_refused(self, given: Entered, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            buildup.build(BILL, [given], TODAY)


def past(price: str, on: date, reference: str, kind: str = "purchase_order") -> history.Past:
    return history.Past(fixture.GATE_150, "no", Decimal(price), on, kind, reference)


@pytest.mark.req("FR-CST-07")
class TestHistory:
    HISTORY = (
        past("118.00", date(2025, 4, 12), "PO-25-0412"),
        past("122.50", date(2025, 9, 3), "PO-25-0903"),
        past("120.00", date(2025, 11, 30), "Tampines Hub", "project"),
    )

    def test_an_outlier_is_flagged_with_the_comparison_shown(self) -> None:
        found = history.compare(
            fixture.GATE_150, "no", Decimal("146.65"), list(self.HISTORY), Decimal(15)
        )

        # The median of 118.00, 120.00 and 122.50 is 120.00; 146.65 is 22.2% above it.
        assert found.outlier is True
        assert (found.count, found.low, found.median, found.high) == (
            3,
            Decimal("118.00"),
            Decimal("120.00"),
            Decimal("122.50"),
        )
        assert found.deviation_percent == Decimal("22.2")
        assert found.latest is not None and found.latest.reference == "Tampines Hub"
        assert found.as_json()["latest"]["kind"] == "project"

    def test_a_price_within_tolerance_is_not(self) -> None:
        found = history.compare(
            fixture.GATE_150, "no", Decimal("125.00"), list(self.HISTORY), Decimal(15)
        )

        assert (found.outlier, found.deviation_percent) == (False, Decimal("4.2"))

    def test_a_price_far_below_its_history_is_flagged_too(self) -> None:
        found = history.compare(
            fixture.GATE_150, "no", Decimal("90.00"), list(self.HISTORY), Decimal(15)
        )

        assert found.outlier and found.deviation_percent == Decimal("-25.0")

    def test_with_no_history_there_is_nothing_to_compare(self) -> None:
        found = history.compare("check_valve|150||||", "no", Decimal("86.50"), list(self.HISTORY))

        assert (found.count, found.outlier, found.median) == (0, False, None)

    def test_the_tolerance_is_configuration(self) -> None:
        assert history.tolerance() == Decimal(15)
        assert history.tolerance({"history": {"outlier_tolerance_percent": 30}}) == Decimal(30)


@pytest.mark.req("FR-CST-08")
class TestErpFileImport:
    def test_the_file_adapter_loads_the_sample(self) -> None:
        adapter: erp.ErpAdapter = erp.FileErp(erp.read_json(fixture.erp_export()))

        assert [item.code for item in adapter.item_master()] == ["VLV-GV-150", "PIP-BS-050"]
        orders = adapter.purchase_orders()
        assert [(o.reference, o.on, o.unit_price) for o in orders] == [
            ("PO-25-0412", date(2025, 4, 12), Decimal("118.00")),
            ("PO-25-0903", date(2025, 9, 3), Decimal("122.50")),
            ("PO-26-0118", date(2026, 1, 18), Decimal("17.90")),
        ]
        assert {o.kind for o in orders} == {"purchase_order"}
        costs = adapter.historical_costs()
        assert [(c.kind, c.reference, c.item_key) for c in costs] == [
            ("project", "Tampines Hub", fixture.GATE_150),
            ("project", "Jurong Tower", fixture.PIPE_50),
        ]

    def test_a_row_that_cannot_be_read_is_reported_by_sheet_row_and_column(self) -> None:
        found = erp.read_json(fixture.erp_export(bad_row=True))

        assert found["problems"] == [
            {
                "sheet": "PurchaseOrders",
                "row": 5,
                "column": "unit_price_sgd",
                "problem": "not a price above zero",
            }
        ]
