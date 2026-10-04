# ruff: noqa: F811  (the fixtures below are imported by name and requested by name)
"""The Phase 2 KPIs measured from a bid's records, and the retention of quotation lines
(requirements §14; NFR-07; P2-09)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from firebid.db.models.core import Bid, Organisation
from firebid.db.models.costing import Quotation
from firebid.db.models.submission import BidOutcome
from firebid.domain.state_machines import BidState
from firebid.services import boq, kpis, retention
from firebid.services import clarifications as clarification_service
from firebid.services import submission as submission_service
from tests.db.test_clarifications import Team as ClarificationTeam
from tests.db.test_clarifications import conflict_of, issued, spec_bid  # noqa: F401
from tests.db.test_risk import Team, risk_bid  # noqa: F401
from tests.db.test_sheet_views import SignIn, app, sign_in  # noqa: F401
from tests.db.test_submission import estimated, ready_for_g3  # noqa: F401
from tests.db.test_symbol_mapping import no_tiles, store  # noqa: F401

pytestmark = [pytest.mark.usefixtures("no_tiles"), pytest.mark.req("NFR-14")]


class TestTurnaroundAndProvenance:
    def test_turnaround_runs_from_the_day_the_bid_was_opened_to_its_g3_approval(
        self, session: Session, estimated: tuple[Bid, Team]
    ) -> None:
        bid, team = estimated
        # Two full weeks before today, whatever day today is: ten working days.
        bid.created_at = datetime.now(UTC) - timedelta(days=14)
        session.commit()

        before = kpis.bid_phase2(session, bid)
        ready_for_g3(session, bid, team)
        submission_service.approve_g3(session, bid, team.director, "approved as reviewed")
        session.commit()
        after = kpis.bid_phase2(session, bid)

        assert (before.ready_on, before.turnaround_working_days) == (None, None)
        assert after.ready_on == datetime.now(UTC).date()
        assert after.turnaround_working_days == 10

    def test_every_priced_line_has_a_source_and_the_database_refuses_one_without(
        self, session: Session, estimated: tuple[Bid, Team]
    ) -> None:
        bid, _ = estimated
        found = kpis.bid_phase2(session, bid)
        assert found.priced_lines > 0
        assert (found.sourced_lines, found.price_provenance) == (found.priced_lines, 1.0)

        # Zero unsourced prices is held by the table itself: a price cannot lose its source.
        current = boq.current_boq(session, bid.id)
        assert current is not None
        line = next(row for row in boq.lines_of(session, current) if row.rate_id is not None)
        line.rate_id = None
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_the_portfolio_compares_the_mean_turnaround_with_the_baseline(
        self,
        session: Session,
        estimated: tuple[Bid, Team],
        monkeypatch: pytest.MonkeyPatch,
        sign_in: SignIn,
    ) -> None:
        bid, team = estimated
        bid.created_at = datetime.now(UTC) - timedelta(days=14)
        ready_for_g3(session, bid, team)
        submission_service.approve_g3(session, bid, team.director, "approved as reviewed")
        session.commit()

        pending = kpis.phase2_kpis(session, [bid])
        monkeypatch.setattr(kpis, "baseline_turnaround", lambda: 20.0)
        measured = kpis.phase2_kpis(session, [bid])
        shown = sign_in(team.manager_principal).get("/kpis/phase2").json()

        # With no baseline the reduction is not measured, rather than zero.
        assert (pending.turnaround_working_days, pending.turnaround_reduction) == (10, None)
        assert measured.turnaround_reduction == pytest.approx(0.5)
        assert measured.price_provenance == 1.0
        assert measured.clarification_acceptance is None
        assert shown["turnaround_reduction"] == pytest.approx(0.5)
        assert shown["targets"]["price_provenance"] == {"at_least": 1.0}
        assert [row["human_id"] for row in shown["bids"]] == [bid.human_id]
        assert shown["bids"][0]["turnaround_working_days"] == 10


class TestClarificationAcceptance:
    def test_a_draft_issued_as_drafted_is_accepted_and_a_rewritten_one_is_not(
        self, session: Session, spec_bid: Bid, organisation: Organisation
    ) -> None:
        team = ClarificationTeam(session, organisation, spec_bid)
        kept = clarification_service.draft(
            session, spec_bid, [conflict_of(session, spec_bid)], team.estimator
        )
        other = next(
            (item.kind, item.ref) for item in clarification_service.candidates(session, spec_bid)
        )
        rewritten = clarification_service.draft(session, spec_bid, [other], team.estimator)
        clarification_service.edit(
            session,
            spec_bid,
            rewritten,
            {
                "subject": "Sprinkler coverage at the loading bay",
                "problem": "The tender drawings stop at grid line 7. Please issue the loading "
                "bay layout, or confirm that it is outside this subcontract.",
            },
            team.estimator,
        )
        session.commit()

        before = kpis.bid_phase2(session, spec_bid)
        issued(session, spec_bid, team, kept)
        issued(session, spec_bid, team, rewritten)
        after = kpis.bid_phase2(session, spec_bid)

        assert (before.clarifications_issued, before.clarification_acceptance) == (0, None)
        assert kept.drafting["issued_edit_ratio"] == 0.0
        assert kept.drafting["minor_edits"] is True
        assert rewritten.drafting["issued_edit_ratio"] > 0.2
        assert rewritten.drafting["minor_edits"] is False
        assert (after.clarifications_issued, after.clarifications_measured) == (2, 2)
        assert after.clarification_acceptance == 0.5

    def test_a_clarification_with_no_draft_on_record_is_issued_but_not_measured(
        self, session: Session, spec_bid: Bid, organisation: Organisation
    ) -> None:
        team = ClarificationTeam(session, organisation, spec_bid)
        row = clarification_service.draft(
            session, spec_bid, [conflict_of(session, spec_bid)], team.estimator
        )
        # As a clarification drafted before the measure existed.
        row.drafting = {key: value for key, value in row.drafting.items() if key != "drafted"}
        session.commit()

        issued(session, spec_bid, team, row)
        found = kpis.bid_phase2(session, spec_bid)

        assert (found.clarifications_issued, found.clarifications_measured) == (1, 0)
        assert found.clarification_acceptance is None


def quotation(session: Session, bid: Bid, name: str) -> Quotation:
    row = Quotation(
        bid_id=bid.id,
        filename=f"{name}.pdf",
        kind="pdf",
        sha256=(name * 64)[:64],
        storage_key=f"bids/{bid.id}/quotations/{name}.pdf",
        supplier="Synthetic Supplies Pte Ltd",
        extraction={
            "fields": {"supplier": {"value": "Synthetic Supplies Pte Ltd", "line": 1}},
            "source_lines": ["Synthetic Supplies Pte Ltd", "Attn: Mr Tan, 9123 4567"],
        },
    )
    session.add(row)
    session.flush()
    return row


@pytest.mark.req("NFR-07")
class TestQuotationLinesRetention:
    NOW = datetime(2027, 12, 1, 12, 0, tzinfo=UTC)

    def test_a_lost_bid_s_quotation_lines_are_cleared_after_a_year_and_the_fields_kept(
        self, session: Session, bid: Bid
    ) -> None:
        row = quotation(session, bid, "a")
        bid.state = str(BidState.LOST)
        session.add(
            BidOutcome(
                bid_id=bid.id,
                outcome="lost",
                reasons="price",
                recorded_by="Bella",
                recorded_at=self.NOW - timedelta(days=366),
            )
        )
        session.commit()

        run = retention.purge(session, self.NOW)
        session.commit()
        again = retention.purge(session, self.NOW)

        session.refresh(row)
        assert run.deleted["quotation_source_lines"] == 1
        assert again.deleted["quotation_source_lines"] == 0
        assert row.extraction["source_lines"] == []
        assert row.extraction["source_lines_cleared"] is True
        assert row.extraction["fields"]["supplier"]["value"] == "Synthetic Supplies Pte Ltd"
        assert row.supplier == "Synthetic Supplies Pte Ltd"

    def test_a_live_bid_and_a_recently_lost_one_keep_their_lines(
        self, session: Session, bid: Bid
    ) -> None:
        row = quotation(session, bid, "b")
        session.commit()

        live = retention.purge(session, self.NOW)
        bid.state = str(BidState.LOST)
        session.add(
            BidOutcome(
                bid_id=bid.id,
                outcome="lost",
                reasons="price",
                recorded_by="Bella",
                recorded_at=self.NOW - timedelta(days=30),
            )
        )
        session.commit()
        recent = retention.purge(session, self.NOW)

        kept = session.execute(select(Quotation).where(Quotation.id == row.id)).scalar_one()
        assert live.deleted["quotation_source_lines"] == 0
        assert recent.deleted["quotation_source_lines"] == 0
        assert len(kept.extraction["source_lines"]) == 2
