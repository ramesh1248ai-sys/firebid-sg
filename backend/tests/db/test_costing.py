# ruff: noqa: F811  (the fixtures below are imported by name and requested by name)
"""Supplier quotations and the cost build-up through the database and the API (P2-04).

Three synthetic quotations (a USD PDF, an SGD workbook and an email with an attached
workbook) are captured, read, confirmed and made into rates; the priced synthetic tender's
bill is built up, compared with history loaded from the ERP's export, and taken to G2.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from firebid.ai_gateway.config import load_config
from firebid.ai_gateway.metering import PostgresMeter
from firebid.ai_gateway.providers.fake import FakeAdapter
from firebid.ai_gateway.router import Router
from firebid.api import costing as costing_api
from firebid.db.models.audit import AuditEvent
from firebid.db.models.commercial import BoqLine, Rate
from firebid.db.models.core import Bid, Organisation
from firebid.db.models.costing import ErpItem, PriceHistory, Quotation
from firebid.db.models.workflow import Approval
from firebid.domain.actors import Actor
from firebid.domain.state_machines import Role
from firebid.domain.values import Money
from firebid.evals import synthetic_quotes as quotes
from firebid.ingest.scanning import AlwaysCleanScanner, Scanner, ScanResult, Verdict
from firebid.pricing import buildup, erp
from firebid.pricing import landed as landing
from firebid.services import boq, costing, pricing
from firebid.services import quotations as service
from firebid.storage.object_store import MemoryObjectStore
from tests.db.test_boq import built, line_for, people, verify_takeoff
from tests.db.test_pricing import priced  # noqa: F401
from tests.db.test_qto import tender  # noqa: F401
from tests.db.test_sheet_views import SignIn, app, member, sign_in  # noqa: F401
from tests.db.test_symbol_mapping import no_tiles, store  # noqa: F401

pytestmark = pytest.mark.usefixtures("no_tiles")

TODAY = date(2026, 10, 3)
CHECK_VALVE = "Check valve, DN150"
MODEL_CONFIG = """
version: 1
providers:
  primary:
    kind: fake
    approved_data_classes: [internal, confidential, commercial]
models:
  first:
    provider: primary
    model_id: reader-1
    capabilities: [structured_output]
    prices:
      - {effective_from: 2026-01-01, input_per_mtok: 5.0, output_per_mtok: 25.0}
routes:
  quotation_extract:
    requires: [structured_output]
    data_class: commercial
    models: [first]
    reasoning: low
    prompt: quotation_extract
"""


class Infected(Scanner):
    def scan(self, payload: bytes) -> ScanResult:
        return ScanResult(Verdict.INFECTED, signature="Eicar-Test-Signature")


@pytest.fixture
def open_bid(session: Session, bid: Bid) -> Bid:
    """A bid whose tender is valid for 90 days from 15 October 2026: to 13 January 2027."""
    bid.submission_deadline = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    bid.tender_validity_days = 90
    session.commit()
    return bid


@pytest.fixture
def staff(session: Session, organisation: Organisation, open_bid: Bid) -> tuple[Actor, Actor]:
    return people(session, organisation, open_bid)


def captured(session: Session, bid: Bid, actor: Actor, payload: bytes, filename: str) -> Quotation:
    row = service.capture(
        session,
        bid,
        payload,
        filename,
        actor,
        store=MemoryObjectStore(),
        scanner=AlwaysCleanScanner(),
        today=TODAY,
    )
    session.commit()
    return row


def usd(session: Session, organisation: Organisation, senior: Actor) -> None:
    costing.record_fx(
        session,
        organisation.id,
        senior,
        currency="USD",
        rate=Decimal("1.35"),
        source="MAS reference rate",
        as_of=date(2026, 10, 1),
    )
    session.commit()


FIXTURES = (
    ("pdf", quotes.PDF, quotes.pdf_quote, "PV-Q-2026-1042.pdf"),
    ("xlsx", quotes.WORKBOOK, quotes.xlsx_quote, "LCS quotation.xlsx"),
    ("eml", quotes.EMAIL, quotes.email_quote, "Quotation AFP-7731.eml"),
)


@pytest.mark.req("FR-CST-02")
class TestCapture:
    @pytest.mark.parametrize(("kind", "expected", "make", "filename"), FIXTURES)
    def test_a_quotation_is_read_with_every_field_and_confirmed(
        self,
        session: Session,
        open_bid: Bid,
        organisation: Organisation,
        staff: tuple[Actor, Actor],
        kind: str,
        expected: quotes.Expected,
        make: Any,
        filename: str,
    ) -> None:
        estimator, senior = staff
        usd(session, organisation, senior)

        row = captured(session, open_bid, estimator, make(), filename)
        lines = service.lines_of(session, row)

        assert (row.kind, row.state) == (kind, "extracted")
        assert (
            row.supplier,
            row.quote_number,
            row.quote_date,
            row.valid_until,
            row.currency,
            row.delivery_terms,
            row.incoterm,
        ) == (
            expected.supplier,
            expected.quote_number,
            expected.quote_date,
            expected.valid_until,
            expected.currency,
            expected.delivery_terms,
            expected.incoterm,
        )
        assert tuple(row.exclusions) == expected.exclusions
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
            for line in lines
        ] == [
            (
                line.description,
                line.brand,
                line.model,
                line.unit,
                line.unit_price,
                line.moq,
                line.lead_time,
            )
            for line in expected.lines
        ]
        assert row.extraction["missing"] == []
        # Every field says which line of the file it was read from.
        assert all(read["source"] for read in row.extraction["fields"].values())
        assert all(line.source for line in lines)

        for line in lines:
            service.link_line(
                session, open_bid, line, estimator, item_key=f"quoted_item|{line.ordinal}||||"
            )
        service.confirm(session, open_bid, row, senior, TODAY)
        session.commit()

        assert (row.state, row.decided_by) == ("confirmed", senior.label)
        rates = list(
            session.execute(
                select(Rate).where(
                    Rate.organisation_id == organisation.id, Rate.source_type == "quotation"
                )
            ).scalars()
        )
        assert {rate.quotation_line_id for rate in rates} == {line.id for line in lines}
        assert {rate.source_reference for rate in rates} == {
            f"{expected.supplier} {expected.quote_number}"
        }

    def test_a_file_that_fails_the_scan_is_not_opened(
        self, session: Session, open_bid: Bid, staff: tuple[Actor, Actor]
    ) -> None:
        estimator, _ = staff

        with pytest.raises(service.QuotationError, match="did not pass the malware scan"):
            service.capture(
                session,
                open_bid,
                quotes.pdf_quote(),
                "quote.pdf",
                estimator,
                store=MemoryObjectStore(),
                scanner=Infected(),
            )

        assert service.quotations(session, open_bid.id) == []

    def test_an_outlook_message_is_refused_with_what_to_do(
        self, session: Session, open_bid: Bid, staff: tuple[Actor, Actor]
    ) -> None:
        estimator, _ = staff

        with pytest.raises(service.QuotationError, match=r"save an Outlook \.msg as \.eml"):
            captured(session, open_bid, estimator, b"\xd0\xcf\x11\xe0" + b"\0" * 64, "quote.msg")

    def test_the_same_file_twice_is_one_quotation(
        self, session: Session, open_bid: Bid, staff: tuple[Actor, Actor]
    ) -> None:
        estimator, _ = staff
        payload = quotes.xlsx_quote()

        first = captured(session, open_bid, estimator, payload, "a.xlsx")
        again = captured(session, open_bid, estimator, payload, "b.xlsx")

        assert first.id == again.id

    def test_a_quotation_is_confirmed_by_a_named_person_with_its_lines_linked(
        self, session: Session, open_bid: Bid, staff: tuple[Actor, Actor]
    ) -> None:
        estimator, senior = staff
        row = captured(session, open_bid, estimator, quotes.xlsx_quote(), "q.xlsx")

        with pytest.raises(service.QuotationError, match="link at least one line"):
            service.confirm(session, open_bid, row, senior, TODAY)
        [first, _] = service.lines_of(session, row)
        service.link_line(session, open_bid, first, estimator, item_key=quotes.PIPE_50)
        with pytest.raises(service.QuotationError, match="a named person"):
            service.confirm(session, open_bid, row, Actor.system(), TODAY)

    def test_a_person_s_correction_replaces_what_was_read_and_says_whose_it_is(
        self, session: Session, open_bid: Bid, staff: tuple[Actor, Actor]
    ) -> None:
        estimator, _ = staff
        row = captured(session, open_bid, estimator, quotes.xlsx_quote(), "q.xlsx")

        service.correct(
            session,
            open_bid,
            row,
            {"valid_until": date(2026, 11, 30), "exclusions": ["Delivery after 5pm"]},
            estimator,
        )
        session.commit()

        assert row.valid_until == date(2026, 11, 30)
        assert row.exclusions == ["Delivery after 5pm"]
        assert row.extraction["fields"]["valid_until"]["by"] == estimator.label
        event = session.execute(
            select(AuditEvent).where(
                AuditEvent.entity_id == str(row.id), AuditEvent.action == "quotation: corrected"
            )
        ).scalar_one()
        assert event.before is not None and event.before["valid_until"] == "2026-10-20"

    def test_the_model_reads_what_the_rules_could_not_and_only_what_the_file_bears_out(
        self, session: Session, open_bid: Bid, staff: tuple[Actor, Actor], tmp_path: Path
    ) -> None:
        estimator, _ = staff
        row = captured(session, open_bid, estimator, letter_quote(), "letter.xlsx")
        assert set(row.extraction["missing"]) == {
            "supplier",
            "quote_number",
            "quote_date",
            "valid_until",
            "currency",
        }
        texts = [" | ".join(line["cells"]) for line in row.extraction["source_lines"]]
        letterhead = next(i for i, words in enumerate(texts) if "Harbour" in words)
        ref = next(i for i, words in enumerate(texts) if "HP-5521" in words)
        terms = next(i for i, words in enumerate(texts) if "firm for acceptance" in words)
        reply = {
            "fields": [
                answer("supplier", "Harbour Pumps Pte Ltd", letterhead),
                answer("quote_number", "HP-5521", ref),
                answer("quote_date", "2026-10-01", ref),
                answer("currency", "SGD", terms),
                # Not what the file says: the line cited gives another date.
                answer("valid_until", "2027-03-31", terms),
                # Not in the file at all.
                answer("exclusion", "Installation and commissioning", letterhead),
            ],
            "lines": [],
        }

        service.read_with_model(session, open_bid, row, router(session, tmp_path, reply), estimator)
        session.commit()

        assert (row.supplier, row.quote_number, row.quote_date, row.currency) == (
            "Harbour Pumps Pte Ltd",
            "HP-5521",
            date(2026, 10, 1),
            "SGD",
        )
        assert row.valid_until is None and row.exclusions == []
        assert row.extraction["missing"] == ["valid_until"]
        assert row.extraction["fields"]["supplier"]["method"] == "model"
        assert row.extraction["model"]["model"] == "reader-1"
        assert {item["name"] for item in row.extraction["model"]["dropped"]} == {
            "valid_until",
            "exclusion",
        }
        # The price is the supplier's own, as the rules read it from the table.
        [line] = service.lines_of(session, row)
        assert line.unit_price == Decimal("4250.0000")


def answer(name: str, value: str, line: int) -> dict[str, Any]:
    return {"name": name, "value": value, "line": line, "confidence": 0.9}


def letter_quote() -> bytes:
    """A quotation written as a letter: its terms are in sentences the rules do not read."""
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(("Messrs Harbour Pumps Pte Ltd",))
    sheet.append(("Our ref HP-5521 dated 1 Oct 2026",))
    sheet.append(("Prices in SGD, firm for acceptance to 31 Dec 2026",))
    sheet.append(())
    sheet.append(quotes.COLUMNS)
    sheet.append(("Jockey pump 1.5 kW", "Harbour", "JP-15", "no", 4250.0, "1", "10 weeks"))
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def router(session: Session, tmp_path: Path, reply: dict[str, Any]) -> Router:
    path = tmp_path / "llm.yaml"
    path.write_text(MODEL_CONFIG, encoding="utf-8")
    return Router(
        config=load_config(path),
        adapters={"primary": FakeAdapter("primary").reply(json.dumps(reply))},
        backoff_base_seconds=0,
        sleep=lambda _s: None,
        meter=PostgresMeter(session),
    )


@pytest.mark.req("FR-CST-03")
class TestFlags:
    def test_expiry_short_validity_and_exclusions_are_flagged(
        self, session: Session, open_bid: Bid, staff: tuple[Actor, Actor]
    ) -> None:
        estimator, _ = staff
        local = captured(session, open_bid, estimator, quotes.xlsx_quote(), "local.xlsx")
        overseas = captured(session, open_bid, estimator, quotes.pdf_quote(), "overseas.pdf")

        def codes(row: Quotation, today: date) -> list[str]:
            return [flag.code for flag in service.flags_of(open_bid, row, today)]

        # Valid to 20 October 2026; the tender's validity runs to 13 January 2027.
        assert codes(local, TODAY) == ["ends_before_tender"]
        assert codes(local, date(2026, 11, 1)) == ["expired"]
        assert codes(overseas, TODAY) == ["ends_before_tender", "exclusions"]
        [_, excluded] = service.flags_of(open_bid, overseas, TODAY)
        assert "Import duties and GST" in excluded.message

    def test_a_line_priced_from_a_quotation_references_its_line(
        self, session: Session, priced: tuple[Bid, Actor, Actor], organisation: Organisation
    ) -> None:
        bid, estimator, senior = priced
        usd(session, organisation, senior)
        target = line_for(session, bid, CHECK_VALVE)
        assert target.rate_id is None
        row = captured(session, bid, estimator, quotes.pdf_quote(), "overseas.pdf")
        check = service.lines_of(session, row)[1]

        service.link_line(session, bid, check, estimator, boq_line_id=target.id)
        service.confirm(session, bid, row, senior, TODAY)
        session.commit()

        session.refresh(target)
        rate = session.get(Rate, target.rate_id)
        assert rate is not None
        assert (rate.source_type, rate.quotation_line_id) == ("quotation", check.id)
        assert rate.source_reference == "Pacific Valve Co. Ltd PV-Q-2026-1042"
        assert target.unit_rate == rate.unit_rate
        assert target.price_provenance["quotation_line_id"] == str(check.id)
        # The rate carries the quotation's validity, so the bill warns as for any rate.
        current = boq.current_boq(session, bid.id)
        assert current is not None
        found = pricing.line_prices(session, bid, current, TODAY)[target.id]
        assert found.status == "priced" and found.warnings


@pytest.mark.req("FR-CST-04")
class TestLandedCost:
    def test_a_usd_quotation_lands_in_sgd_as_a_hand_calculation_does(
        self,
        session: Session,
        open_bid: Bid,
        organisation: Organisation,
        staff: tuple[Actor, Actor],
    ) -> None:
        estimator, senior = staff
        row = captured(session, open_bid, estimator, quotes.pdf_quote(), "overseas.pdf")
        gate = service.lines_of(session, row)[0]
        service.link_line(session, open_bid, gate, estimator, item_key=quotes.GATE_150)

        with pytest.raises(service.QuotationError, match="no FX rate is recorded for USD"):
            service.confirm(session, open_bid, row, senior, TODAY)
        usd(session, organisation, senior)
        service.confirm(session, open_bid, row, senior, TODAY)
        session.commit()

        rate = session.execute(select(Rate).where(Rate.quotation_line_id == gate.id)).scalar_one()
        # USD 100.00 FOB at 1.35 = 135.00; 2% buffer 2.70 -> 137.70; freight 5% 6.885,
        # insurance 0.5% 0.6885, import charges 1% 1.377 -> 146.6505.
        assert rate.unit_rate == Money.of(Decimal("146.65")) and rate.currency == "SGD"
        assert rate.landed is not None
        assert rate.landed["fx"] == {
            "rate": "1.350000",
            "source": "MAS reference rate",
            "as_of": "2026-10-01",
        }
        steps = {line["key"]: Decimal(line["amount"]) for line in rate.landed["lines"]}
        assert steps == {
            "price": Decimal("135.0000"),
            "fx_buffer": Decimal("2.7000"),
            "freight": Decimal("6.8850"),
            "insurance": Decimal("0.6885"),
            "import_charges": Decimal("1.3770"),
        }

    def test_a_rate_is_recorded_once_with_its_source_and_is_never_changed(
        self, session: Session, organisation: Organisation, staff: tuple[Actor, Actor]
    ) -> None:
        _, senior = staff
        usd(session, organisation, senior)

        with pytest.raises(costing.CostingError, match="already recorded"):
            usd(session, organisation, senior)
        costing.record_fx(
            session,
            organisation.id,
            senior,
            currency="usd",
            rate=Decimal("1.36"),
            source="MAS reference rate",
            as_of=date(2026, 10, 2),
        )

        table = costing.fx_table(session, organisation.id)
        assert [(row.currency, row.rate, row.as_of) for row in table] == [
            ("USD", Decimal("1.360000"), date(2026, 10, 2)),
            ("USD", Decimal("1.350000"), date(2026, 10, 1)),
        ]
        latest = landing.latest_rate(service.fx_rates(session, organisation.id), "USD", TODAY)
        assert latest is not None and latest.rate == Decimal("1.360000")


@pytest.mark.req("FR-CST-05")
class TestGst:
    def test_a_changed_rate_changes_only_a_bid_priced_from_its_date(
        self, session: Session, priced: tuple[Bid, Actor, Actor]
    ) -> None:
        bid, estimator, _ = priced
        costing.set_priced_on(session, bid, date(2026, 9, 28), estimator)
        before = costing.build_up(session, bid)
        changed = [*landing.gst_rates(), landing.GstRate(date(2027, 1, 1), Decimal(10))]

        after = costing.build_up(session, bid, gst_rates=changed)

        assert before.gst.rate.percent == after.gst.rate.percent == Decimal(9)
        assert after.gst.gst == before.gst.gst
        assert before.gst.exclusive == before.total
        assert before.gst.inclusive.amount == before.total.amount + before.gst.gst.amount

        costing.set_priced_on(session, bid, date(2027, 1, 2), estimator)
        later = costing.build_up(session, bid, gst_rates=changed)

        assert later.gst.rate.percent == Decimal(10)
        assert later.total == before.total, "prices are held exclusive of GST"
        assert later.gst.gst.amount == (later.total.amount * Decimal("0.10")).quantize(
            Decimal("0.01")
        )


@pytest.mark.req("FR-CST-06")
class TestBuildUp:
    def test_every_component_is_its_own_line_with_its_basis_and_source(
        self, session: Session, priced: tuple[Bid, Actor, Actor]
    ) -> None:
        bid, estimator, senior = priced
        costing.enter(
            session,
            bid,
            estimator,
            component="labour",
            basis="lump_sum",
            amount=Decimal(18000),
            note="placeholder until the labour model",
        )
        costing.enter(
            session,
            bid,
            estimator,
            component="preliminaries",
            basis="percentage",
            percent=Decimal(5),
            base="direct",
        )
        costing.enter(
            session,
            bid,
            senior,
            component="margin",
            basis="percentage",
            percent=Decimal(8),
            base="cost",
        )
        session.commit()

        found = costing.build_up(session, bid, TODAY)

        by_key = {line.component: line for line in found.lines}
        assert tuple(by_key) == buildup.KEYS and len(found.lines) == 17
        assert all(line.basis and line.source for line in found.lines)
        assert by_key["materials"].basis == "calculated"
        assert by_key["materials"].source == "the priced bill of quantities"
        assert by_key["labour"].basis == "lump_sum"
        assert by_key["labour"].source.startswith(f"entered by {estimator.label} on ")
        assert by_key["preliminaries"].basis == "percentage"
        assert by_key["margin"].source.startswith(f"entered by {senior.label} on ")
        assert by_key["bonds"].basis == "not set" and by_key["bonds"].amount is None
        assert "bonds" in found.not_set and "labour" not in found.not_set
        direct = found.direct.amount
        assert by_key["preliminaries"].amount == Money.of(
            (direct * Decimal("0.05")).quantize(Decimal("0.01"))
        )
        assert found.total.amount == sum(
            (line.amount.amount for line in found.lines if line.amount is not None), Decimal(0)
        )
        assert found.unpriced_lines == 6

    def test_a_new_figure_replaces_the_one_before_it_which_is_kept(
        self, session: Session, priced: tuple[Bid, Actor, Actor]
    ) -> None:
        bid, estimator, senior = priced
        costing.enter(
            session, bid, estimator, component="transport", basis="lump_sum", amount=Decimal(900)
        )
        costing.enter(
            session, bid, senior, component="transport", basis="lump_sum", amount=Decimal(1200)
        )
        session.commit()

        [inforce] = costing.entered(session, bid.id)
        kept = session.execute(
            text("SELECT count(*) FROM cost_buildup_line WHERE bid_id = :b"), {"b": bid.id}
        ).scalar_one()

        assert (inforce.amount, inforce.entered_by) == (Money.of(Decimal(1200)), senior.label)
        assert kept == 2

    def test_a_direct_cost_is_not_a_percentage_and_nothing_is_entered_for_the_bill_s_lines(
        self, session: Session, priced: tuple[Bid, Actor, Actor]
    ) -> None:
        bid, estimator, _ = priced

        with pytest.raises(costing.CostingError, match="enter it as a lump sum"):
            costing.enter(
                session,
                bid,
                estimator,
                component="labour",
                basis="percentage",
                percent=Decimal(20),
                base="direct",
            )
        with pytest.raises(costing.CostingError, match="not a component an estimator enters"):
            costing.enter(
                session, bid, estimator, component="materials", basis="lump_sum", amount=Decimal(1)
            )


class PastChecks:
    """An ERP adapter that is not a file: past check valve prices around SGD 90."""

    def __init__(self, item_key: str) -> None:
        self._key = item_key

    def item_master(self) -> list[erp.Item]:
        return []

    def purchase_orders(self) -> list[erp.PastPrice]:
        return [
            erp.PastPrice("purchase_order", ref, on, self._key, "no", Decimal(price))
            for ref, on, price in (
                ("PO-25-0101", date(2025, 1, 10), "88.00"),
                ("PO-25-0707", date(2025, 7, 7), "90.00"),
            )
        ]

    def historical_costs(self) -> list[erp.PastPrice]:
        return [
            erp.PastPrice("project", "Punggol Mall", date(2026, 2, 1), self._key, "no", Decimal(92))
        ]


@pytest.mark.req("FR-CST-07")
class TestHistory:
    def test_a_price_far_from_its_history_is_flagged_with_the_comparison(
        self, session: Session, priced: tuple[Bid, Actor, Actor], organisation: Organisation
    ) -> None:
        bid, estimator, senior = priced
        usd(session, organisation, senior)
        target = line_for(session, bid, CHECK_VALVE)
        row = captured(session, bid, estimator, quotes.pdf_quote(), "overseas.pdf")
        check = service.lines_of(session, row)[1]
        service.link_line(session, bid, check, estimator, boq_line_id=target.id)
        service.confirm(session, bid, row, senior, TODAY)
        assert target.item_key is not None
        costing.load_erp(session, organisation.id, PastChecks(target.item_key), senior, "test")
        session.commit()

        found = {item.line_id: item.comparison for item in costing.comparisons(session, bid)}

        # USD 86.50 FOB lands at SGD 126.85; history's median is 90.00.
        compared = found[target.id]
        assert compared.price == Decimal("126.85")
        assert (compared.count, compared.low, compared.median, compared.high) == (
            3,
            Decimal("88.0000"),
            Decimal("90.0000"),
            Decimal("92.0000"),
        )
        assert compared.deviation_percent == Decimal("40.9") and compared.outlier
        assert compared.latest is not None and compared.latest.reference == "Punggol Mall"
        others = [item for line_id, item in found.items() if line_id != target.id]
        assert others and not any(item.outlier for item in others), "no history, no flag"


@pytest.mark.req("FR-CST-08")
class TestErp:
    def test_the_file_import_loads_the_sample_and_loads_it_once(
        self, session: Session, organisation: Organisation, staff: tuple[Actor, Actor]
    ) -> None:
        _, senior = staff

        first = costing.import_erp_file(
            session, organisation.id, quotes.erp_export(), senior, "erp.xlsx"
        )
        again = costing.import_erp_file(
            session, organisation.id, quotes.erp_export(), senior, "erp.xlsx"
        )
        session.commit()

        assert (first.imported, first.items, first.purchase_orders, first.historical_costs) == (
            True,
            len(quotes.ERP_ITEMS),
            len(quotes.ERP_PURCHASE_ORDERS),
            len(quotes.ERP_HISTORICAL),
        )
        assert (again.items, again.purchase_orders, again.historical_costs) == (0, 0, 0)
        assert again.unchanged == 7
        items = session.execute(
            select(ErpItem.code).where(ErpItem.organisation_id == organisation.id)
        ).scalars()
        assert sorted(items) == ["PIP-BS-050", "VLV-GV-150"]
        past = costing.past_prices(session, organisation.id)
        assert sorted(p.reference for p in past if p.item_key == quotes.GATE_150) == [
            "PO-25-0412",
            "PO-25-0903",
            "Tampines Hub",
        ]
        # History is for comparison: it makes no rate, so it prices nothing.
        rates = session.execute(
            select(func.count()).select_from(Rate).where(Rate.organisation_id == organisation.id)
        ).scalar_one()
        assert rates == 0

    def test_an_export_with_a_bad_row_loads_nothing(
        self, session: Session, organisation: Organisation, staff: tuple[Actor, Actor]
    ) -> None:
        _, senior = staff

        report = costing.import_erp_file(
            session, organisation.id, quotes.erp_export(bad_row=True), senior, "erp.xlsx"
        )

        assert report.imported is False and report.problems
        held = session.execute(
            select(func.count())
            .select_from(PriceHistory)
            .where(PriceHistory.organisation_id == organisation.id)
        ).scalar_one()
        assert held == 0


@pytest.mark.req("FR-CST-09")
class TestNoGeneratedPrices:
    def test_g2_fails_on_an_unsourced_priced_line_until_it_is_sourced_or_left_unpriced(
        self, session: Session, tender: Bid, organisation: Organisation
    ) -> None:
        estimator, senior = people(session, organisation, tender)
        verify_takeoff(session, tender)
        assert senior.id is not None
        session.add(
            Approval(
                bid_id=tender.id,
                gate="G1",
                decision="approved",
                approver_id=senior.id,
                approver_role="senior_estimator",
                decided_at=datetime.now(UTC),
            )
        )
        built(session, tender, estimator)
        current = boq.current_boq(session, tender.id)
        assert current is not None
        # An amount nobody's name is on: as a row written before this step, or around it.
        line = BoqLine(
            bid_id=tender.id,
            boq_id=current.id,
            section="LUMP SUMS",
            item_no="LS9",
            description="Allow for builder's work",
            unit="sum",
            quantity=Decimal(1),
            is_lump_sum=True,
            marker_note="not measured",
            amount=Money.of(Decimal(7500)),
            sort_order=999,
        )
        session.add(line)
        session.commit()

        blockers = boq.g2_blockers(session, tender.id)
        assert [found["id"] for found in blockers.unsourced_lines] == [str(line.id)]
        with pytest.raises(boq.BoqError, match=r"1 priced line\(s\) with no source"):
            boq.approve_g2(session, tender, senior, "senior_estimator")

        # Left unpriced, it no longer stands in the way...
        boq.set_allowance(session, line, None, estimator)
        assert line.amount is None and boq.g2_blockers(session, tender.id).clear
        # ...and neither does an allowance that says whose it is.
        boq.set_allowance(session, line, Decimal(7500), estimator)
        session.commit()

        assert line.allowance_by == estimator.label
        assert boq.g2_blockers(session, tender.id).clear
        approval = boq.approve_g2(session, tender, senior, "senior_estimator")
        assert approval.gate == "G2"

    def test_an_allowance_keeps_its_estimator_s_name_through_a_rebuild(
        self, session: Session, priced: tuple[Bid, Actor, Actor]
    ) -> None:
        bid, estimator, _ = priced
        boq.add_marked_line(
            session,
            bid.id,
            estimator,
            description="Allow for testing and commissioning",
            unit="sum",
            quantity=Decimal(1),
            marker="provisional",
            note="client's provisional sum",
            amount=Decimal(5000),
        )
        built(session, bid, estimator)
        session.commit()

        current = boq.current_boq(session, bid.id)
        assert current is not None
        [allowance] = [line for line in boq.lines_of(session, current) if line.is_provisional]
        assert allowance.allowance_by == estimator.label
        assert boq.unsourced_lines(session, bid.id) == []

    def test_only_a_provisional_or_lump_sum_line_carries_an_allowance(
        self, session: Session, priced: tuple[Bid, Actor, Actor]
    ) -> None:
        bid, estimator, _ = priced
        measured = line_for(session, bid, CHECK_VALVE)

        with pytest.raises(boq.BoqError, match="only a provisional sum or a lump sum"):
            boq.set_allowance(session, measured, Decimal(100), estimator)

    def test_the_database_refuses_an_entered_cost_with_nobody_s_name(
        self, session: Session, open_bid: Bid
    ) -> None:
        with pytest.raises(IntegrityError, match="estimator_named"):
            session.execute(
                text(
                    "INSERT INTO cost_buildup_line (id, bid_id, component, basis, amount, "
                    "entered_by) VALUES (gen_random_uuid(), :b, 'labour', 'lump_sum', 100, '')"
                ),
                {"b": open_bid.id},
            )
        session.rollback()


class TestApi:
    @pytest.mark.req("FR-CST-02")
    @pytest.mark.req("FR-CST-04")
    def test_a_quotation_is_uploaded_checked_against_its_file_and_confirmed_by_a_senior(
        self,
        session: Session,
        priced: tuple[Bid, Actor, Actor],
        organisation: Organisation,
        sign_in: SignIn,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bid, _, _ = priced
        memory = MemoryObjectStore()
        monkeypatch.setattr(costing_api, "get_object_store", lambda: memory)
        monkeypatch.setattr(costing_api, "get_scanner", AlwaysCleanScanner)
        target = line_for(session, bid, CHECK_VALVE)
        files = {"file": ("overseas.pdf", quotes.pdf_quote(), "application/pdf")}
        estimator = sign_in(member(session, organisation, bid, "eve", Role.ESTIMATOR))

        uploaded = estimator.post(f"/bids/{bid.id}/quotations", files=files)

        assert uploaded.status_code == 201, uploaded.text
        body = uploaded.json()
        assert body["supplier"] == "Pacific Valve Co. Ltd" and body["missing"] == []
        assert "exclusions" in [flag["code"] for flag in body["flags"]]
        # The field and the line of the file it was read from, to show side by side.
        place = body["fields"]["quote_number"]["source"]
        shown = next(
            line
            for line in body["source_lines"]
            if all(line.get(key) == value for key, value in place.items() if key != "text")
        )
        assert "PV-Q-2026-1042" in shown["cells"]
        check = body["lines"][1]
        linked = estimator.post(
            f"/bids/{bid.id}/quotations/{body['id']}/lines/{check['id']}/link",
            json={"boq_line_id": str(target.id)},
        )
        assert linked.status_code == 200, linked.text
        assert linked.json()["lines"][1]["item_key"] == target.item_key
        refused = estimator.post(f"/bids/{bid.id}/quotations/{body['id']}/confirm")
        assert refused.status_code == 403

        senior = sign_in(member(session, organisation, bid, "sid", Role.SENIOR_ESTIMATOR))
        without_fx = senior.post(f"/bids/{bid.id}/quotations/{body['id']}/confirm")
        fx = senior.post(
            "/costing/fx-rates",
            json={"currency": "USD", "rate": "1.35", "source": "MAS", "as_of": "2026-01-02"},
        )
        confirmed = senior.post(f"/bids/{bid.id}/quotations/{body['id']}/confirm")

        assert without_fx.status_code == 409 and "no FX rate" in without_fx.json()["detail"]
        assert fx.status_code == 201, fx.text
        assert fx.json()["buffer_percent"] == "2.0" and len(fx.json()["rates"]) == 1
        assert confirmed.status_code == 200, confirmed.text
        line = confirmed.json()["lines"][1]
        assert confirmed.json()["state"] == "confirmed" and line["rate_id"]
        assert line["landed"]["unit_cost_sgd"] == "126.85"
        listed = senior.get(f"/bids/{bid.id}/quotations").json()
        assert [item["state"] for item in listed] == ["confirmed"]

    @pytest.mark.req("FR-CST-06")
    @pytest.mark.req("FR-CST-09")
    def test_the_build_up_is_shown_entered_and_checked_at_g2(
        self,
        session: Session,
        priced: tuple[Bid, Actor, Actor],
        organisation: Organisation,
        sign_in: SignIn,
    ) -> None:
        bid, _, _ = priced
        client = sign_in(member(session, organisation, bid, "eve", Role.ESTIMATOR))

        empty = client.get(f"/bids/{bid.id}/cost/build-up").json()
        entered = client.put(
            f"/bids/{bid.id}/cost/build-up/labour",
            json={"basis": "lump_sum", "amount": "18000"},
        )
        wrong = client.put(
            f"/bids/{bid.id}/cost/build-up/labour",
            json={"basis": "percentage", "percent": "20", "base": "direct"},
        )
        dated = client.put(f"/bids/{bid.id}/cost/priced-on", json={"priced_on": "2023-06-01"})
        history = client.get(f"/bids/{bid.id}/cost/history")
        g2 = client.get(f"/bids/{bid.id}/boq/g2").json()

        assert len(empty["lines"]) == 17 and "labour" in empty["not_set"]
        assert entered.status_code == 200, entered.text
        labour = next(line for line in entered.json()["lines"] if line["component"] == "labour")
        assert labour["amount"] == "18000.00" and labour["source"].startswith("entered by Eve")
        assert Decimal(entered.json()["total"]) == Decimal(empty["total"]) + Decimal(18000)
        assert wrong.status_code == 422
        assert Decimal(dated.json()["gst_percent"]) == Decimal(8)
        assert dated.json()["priced_on_set"] is True
        assert Decimal(dated.json()["total_with_gst"]) == Decimal(dated.json()["total"]) + Decimal(
            dated.json()["gst"]
        )
        assert history.status_code == 200 and history.json()
        assert g2["unsourced_lines"] == []

    @pytest.mark.req("FR-CST-08")
    def test_only_a_senior_estimator_loads_the_erp_export(
        self, session: Session, bid: Bid, organisation: Organisation, sign_in: SignIn
    ) -> None:
        files = {"file": ("erp.xlsx", quotes.erp_export(), "application/octet-stream")}
        estimator = sign_in(member(session, organisation, bid, "eve", Role.ESTIMATOR))
        refused = estimator.post("/costing/erp/import", files=files)
        senior = sign_in(member(session, organisation, bid, "sid", Role.SENIOR_ESTIMATOR))

        loaded = senior.post("/costing/erp/import", files=files)

        assert refused.status_code == 403
        assert loaded.status_code == 200, loaded.text
        assert (loaded.json()["imported"], loaded.json()["purchase_orders"]) == (True, 3)
