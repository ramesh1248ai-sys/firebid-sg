# ruff: noqa: F811  (the fixtures below are imported by name and requested by name)
"""Match lines and the layout export through the database and the API (FR-DSN-05, 06).

The synthetic design-intent sheet, drawn with a match line that leaves it 12 m of a 20 m
floor, goes through the parse pipeline as an uploaded drawing does.
"""

from __future__ import annotations

import io

import ezdxf
import pdfplumber
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from firebid.db.models.audit import AuditEvent
from firebid.db.models.core import Bid, Organisation
from firebid.db.models.design import SheetDesign
from firebid.design import export as layout_export
from firebid.domain.state_machines import Role
from firebid.evals import synthetic_design
from firebid.services import design
from firebid.services.detection import detect_bid
from firebid.storage.object_store import MemoryObjectStore
from tests.db.test_design import SENIOR, confirmed, designed
from tests.db.test_sheet_views import SignIn, app, member, sign_in  # noqa: F401
from tests.db.test_symbol_mapping import from_consultant, no_tiles, read, store  # noqa: F401


def lined_sheet(session: Session, bid: Bid, store: MemoryObjectStore) -> SheetDesign:
    from_consultant(session, bid, "SYNTHETIC CONSULTANTS PTE LTD")
    read(session, bid, store, "FP-L10-01", synthetic_design.design_intent_plan(match_line=True))
    # As the parse pipeline does before a person asks for the design basis: the main is found.
    detect_bid(session, store, bid.id)
    (row,) = design.read_basis(session, store, bid.id)
    session.execute(text("DELETE FROM procrastinate_jobs"))
    session.commit()
    return row


def laid_out(session: Session, bid: Bid, store: MemoryObjectStore, row: SheetDesign) -> list[float]:
    """Confirm and lay out; the proposed heads' x on the sheet."""
    if row.state != "confirmed":
        design.confirm(session, bid.id, [row.sheet_id], SENIOR, key="note_1")
    design.lay_out_bid(session, store, bid.id)
    session.commit()
    objects, _ = designed(session, bid)
    return [
        float(str(o.geometry_ref["x"])) for o in objects if o.kind == "object" and o.geometry_ref
    ]


def line_x(row: SheetDesign) -> float:
    (line,) = [entry for entry in row.match_lines if entry.get("line")]
    ends = line["line"]
    assert isinstance(ends, list)
    assert ends[0][0] == pytest.approx(ends[1][0], abs=0.5)  # it runs straight down the sheet
    return float(ends[0][0])


@pytest.mark.req("FR-DSN-06")
class TestMatchLines:
    def test_the_sheet_s_scope_is_proposed_from_its_match_line_with_the_words_it_rests_on(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        row = lined_sheet(session, bid, store)

        (line,) = row.match_lines
        assert line["label"] == synthetic_design.MATCH_LINE_NOTE
        assert line["other_sheet"] == "FP-L10-02"
        # This sheet has no legend, so no pipe run is found on it, and its pipes are coloured
        # by layer, which the geometry does not carry: the larger side is proposed, and says so.
        assert line["reason"] == "the larger side: no services are drawn on either"
        assert (row.scope_source, row.state) == ("match_lines", "proposed")
        assert row.scope is not None
        # The sheet's side is the 12 m of the 20 m floor: everything up to the line.
        assert max(point[0] for point in row.scope) == pytest.approx(line_x(row), abs=0.5)

    def test_the_layout_keeps_to_the_sheet_s_side_so_the_shared_floor_is_designed_once(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        row = lined_sheet(session, bid, store)
        cut = line_x(row)

        own = laid_out(session, bid, store, row)
        design.choose_sides(session, row, {0: -int(row.match_lines[0]["side"])}, SENIOR)  # type: ignore[call-overload]
        other = laid_out(session, bid, store, row)
        design.set_scope(session, row, None, SENIOR)
        whole = laid_out(session, bid, store, row)

        assert own and all(x <= cut + 0.5 for x in own)
        assert other and all(x >= cut - 0.5 for x in other)
        # 12 m of the 20 m floor is this sheet's: about three fifths of the heads, and the
        # two sides together are the whole floor, to a head or two at the line.
        assert len(own) > len(other)
        assert len(own) + len(other) == pytest.approx(len(whole), abs=4)
        assert len(own) < len(whole)

    def test_a_person_s_choice_of_side_is_kept_when_the_sheet_is_read_again(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        row = lined_sheet(session, bid, store)
        proposed = int(row.match_lines[0]["side"])  # type: ignore[call-overload]
        cut = line_x(row)

        design.choose_sides(session, row, {0: -proposed}, SENIOR)
        session.commit()
        design.read_basis(session, store, bid.id)
        session.commit()

        assert row.scope_source == "person"
        assert (row.match_lines[0]["side"], row.match_lines[0]["proposed_side"]) == (
            -proposed,
            proposed,
        )
        assert row.scope is not None
        assert min(point[0] for point in row.scope) == pytest.approx(cut, abs=0.5)
        event = session.execute(
            select(AuditEvent).where(AuditEvent.action == "design basis: match line side")
        ).scalar_one()
        assert event.actor_label == SENIOR.label
        assert event.after is not None and event.after["sides"] == {"0": -proposed}

    def test_the_whole_sheet_is_a_person_s_choice_too_and_the_proposal_can_be_taken_back(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        row = lined_sheet(session, bid, store)
        proposed = [list(point) for point in row.scope or []]

        design.set_scope(session, row, None, SENIOR)
        design.read_basis(session, store, bid.id)
        assert (row.scope, row.scope_source) == (None, "person")

        design.follow_match_lines(session, row, SENIOR)
        assert (row.scope, row.scope_source) == (proposed, "match_lines")

    def test_a_sheet_with_no_match_line_is_laid_out_whole_and_offers_no_side(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        row = confirmed(session, bid, store)

        assert (row.match_lines, row.scope, row.scope_source) == ([], None, None)
        with pytest.raises(design.DesignError, match="no match line was found"):
            design.choose_sides(session, row, {0: 1}, SENIOR)

    def test_the_side_is_chosen_through_the_api_and_shown_with_the_basis(
        self,
        session: Session,
        organisation: Organisation,
        bid: Bid,
        store: MemoryObjectStore,
        sign_in: SignIn,
    ) -> None:
        row = lined_sheet(session, bid, store)
        estimator = sign_in(member(session, organisation, bid, "esther", Role.ESTIMATOR))
        address = f"/bids/{bid.id}/design/sheets/{row.sheet_id}/scope"

        (shown,) = estimator.get(f"/bids/{bid.id}/design").json()["sheets"]
        assert shown["scope_source"] == "match_lines"
        (line,) = shown["match_lines"]
        assert (line["other_sheet"], line["side"]) == ("FP-L10-02", line["proposed_side"])

        flipped = estimator.put(address, json={"sides": {"0": -line["side"]}})
        assert flipped.status_code == 204, flipped.text
        (shown,) = estimator.get(f"/bids/{bid.id}/design").json()["sheets"]
        assert shown["scope_source"] == "person"
        assert shown["match_lines"][0]["side"] == -line["side"]

        assert estimator.put(address, json={"sides": {"3": 1}}).status_code == 409
        both = estimator.put(address, json={"sides": {"0": 1}, "follow_match_lines": True})
        assert both.status_code == 422
        assert estimator.put(address, json={"follow_match_lines": True}).status_code == 204
        (shown,) = estimator.get(f"/bids/{bid.id}/design").json()["sheets"]
        assert shown["scope_source"] == "match_lines"


@pytest.mark.req("FR-DSN-05")
class TestExport:
    def test_the_pdf_shows_the_layout_over_the_tender_drawing_and_is_stamped(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        row = confirmed(session, bid, store)
        heads = [o for o in designed(session, bid)[0] if o.kind == "object"]

        exported = design.export_layout(session, store, row, "pdf", SENIOR)
        session.commit()

        assert exported.media_type == "application/pdf"
        assert exported.filename == "FP-L10-01-proposed-layout.pdf"
        with pdfplumber.open(io.BytesIO(exported.content)) as document:
            (page,) = document.pages
            words = " ".join((page.extract_text() or "").split())
        assert layout_export.STAMP.upper() in words
        assert "Tender drawing FP-L10-01 revision R01, drawn at 1:100." in words
        assert f"{len(heads)} proposed heads" in words
        assert f"confirmed by {SENIOR.label}" in words
        assert "at most 12.0 m2 a head, 4000 x 3000 mm apart" in words
        # The tender drawing's own words are under the layout.
        assert "WARD A" in words and "DN150 SPR" in words
        event = session.execute(
            select(AuditEvent).where(AuditEvent.action == "design layout: exported")
        ).scalar_one()
        assert event.actor_label == SENIOR.label
        assert event.after is not None
        assert (event.after["format"], event.after["heads"]) == ("pdf", len(heads))

    def test_the_dxf_has_a_head_for_every_proposed_head_a_person_has_not_rejected(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        row = confirmed(session, bid, store)
        heads = [o for o in designed(session, bid)[0] if o.kind == "object"]
        heads[0].state = "rejected"
        session.commit()

        exported = design.export_layout(session, store, row, "dxf", SENIOR)

        document = ezdxf.read(io.StringIO(exported.content.decode("utf-8")))
        space = document.modelspace()
        drawn = [
            e for e in space if e.dxf.layer == layout_export.LAYER_HEADS and e.dxftype() == "CIRCLE"
        ]
        assert len(drawn) == len(heads) - 1
        assert any(e.dxf.layer == layout_export.LAYER_PIPE for e in space)
        assert any(e.dxf.layer == layout_export.LAYER_TENDER for e in space)
        stamped = [
            e.dxf.text
            for e in space
            if e.dxf.layer == layout_export.LAYER_STAMP and e.dxftype() == "TEXT"
        ]
        assert layout_export.STAMP.upper() in stamped
        assert f"{len(heads) - 1} proposed heads" in " ".join(stamped)

    def test_a_sheet_limited_by_its_match_line_says_so_and_draws_its_scope(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        row = lined_sheet(session, bid, store)
        laid_out(session, bid, store, row)

        exported = design.export_layout(session, store, row, "dxf", SENIOR)

        document = ezdxf.read(io.StringIO(exported.content.decode("utf-8")))
        layers = [e.dxf.layer for e in document.modelspace()]
        texts = [e.dxf.text for e in document.modelspace() if e.dxftype() == "TEXT"]
        assert layout_export.LAYER_SCOPE in layers
        assert "Limited to this sheet's side of its match lines (the dashed outline)." in texts

    def test_there_is_nothing_to_export_until_a_layout_is_made(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        row = lined_sheet(session, bid, store)

        with pytest.raises(design.DesignError, match="no proposed layout to export yet"):
            design.export_layout(session, store, row, "pdf", SENIOR)
        with pytest.raises(design.DesignError, match="PDF or DXF"):
            design.export_layout(session, store, row, "dwg", SENIOR)

    def test_the_file_is_asked_for_made_by_a_job_kept_and_then_downloaded(
        self,
        session: Session,
        organisation: Organisation,
        bid: Bid,
        second_bid: Bid,
        store: MemoryObjectStore,
        sign_in: SignIn,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from firebid.api import design as design_api

        row = confirmed(session, bid, store)
        monkeypatch.setattr(design_api, "get_object_store", lambda: store)
        address = f"/bids/{bid.id}/design/sheets/{row.sheet_id}/export"
        estimator = sign_in(member(session, organisation, bid, "esther", Role.ESTIMATOR))

        # Nothing is made until it is asked for, and asking queues one job.
        early = estimator.get(address)
        assert early.status_code == 409 and "not made yet" in early.json()["detail"]
        asked = estimator.post(address)
        assert asked.status_code == 202, asked.text
        assert (asked.json()["format"], asked.json()["state"]) == ("pdf", "queued")
        estimator.post(address)
        jobs = session.execute(
            text("SELECT count(*) FROM procrastinate_jobs WHERE task_name = 'design.export'")
        ).scalar_one()
        assert jobs == 1
        assert estimator.post(address, params={"format": "dwg"}).status_code == 409

        # The job, as the worker runs it.
        session.refresh(row)
        made = design.make_export(session, store, row, "pdf")
        design.request_export(session, row, "dxf", SENIOR)
        design.make_export(session, store, row, "dxf")
        session.commit()
        assert made["state"] == "ready" and made["bytes"] > 1000

        states = {e["format"]: e["state"] for e in estimator.get(f"{address}/status").json()}
        assert states == {"pdf": "ready", "dxf": "ready"}
        pdf = estimator.get(address)
        assert pdf.status_code == 200, pdf.text
        assert pdf.headers["content-type"] == "application/pdf"
        assert pdf.headers["content-disposition"] == (
            'attachment; filename="FP-L10-01-proposed-layout.pdf"'
        )
        assert pdf.content.startswith(b"%PDF")
        with pdfplumber.open(io.BytesIO(pdf.content)) as document:
            words = " ".join((document.pages[0].extract_text() or "").split())
        assert layout_export.STAMP.upper() in words and "for Esther" in words
        # A DXF is kept compressed and unpacked on the way to the person.
        dxf = estimator.get(address, params={"format": "dxf"})
        assert dxf.status_code == 200 and dxf.headers["content-type"] == "application/dxf"
        assert dxf.headers["content-encoding"] == "gzip"
        assert dxf.content.lstrip().startswith(b"0") and b"ESTIMATION-ONLY" in dxf.content
        kept = row.exports["dxf"]
        assert int(str(kept["stored_bytes"])) < int(str(kept["bytes"])) / 3
        # Asking again finds the file ready, and queues nothing.
        assert estimator.post(address).json()["state"] == "ready"

        outsider = sign_in(member(session, organisation, second_bid, "olive", Role.ESTIMATOR))
        assert outsider.get(address).status_code == 404
        assert outsider.post(address).status_code == 404
        exports = session.execute(
            select(AuditEvent).where(AuditEvent.action == "design layout: exported")
        ).scalars()
        # The two downloads, each in the name of the person who took it; not the making.
        assert [event.actor_label for event in exports] == ["Esther", "Esther"]

    def test_a_file_made_before_the_layout_changed_is_not_handed_out_as_the_layout(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        row = confirmed(session, bid, store)
        design.request_export(session, row, "pdf", SENIOR)
        design.make_export(session, store, row, "pdf")
        assert design.exports_of(session, row)["pdf"]["state"] == "ready"

        head = next(o for o in designed(session, bid)[0] if o.kind == "object")
        head.state = "rejected"
        session.flush()

        assert design.exports_of(session, row)["pdf"]["state"] == "stale"
        with pytest.raises(design.DesignError, match="has changed since"):
            design.export_file(session, store, row, "pdf", SENIOR)
        assert design.request_export(session, row, "pdf", SENIOR)["state"] == "queued"
        design.make_export(session, store, row, "pdf")
        assert design.export_file(session, store, row, "pdf", SENIOR).content.startswith(b"%PDF")

    def test_a_layout_with_nothing_proposed_is_not_queued(
        self, session: Session, bid: Bid, store: MemoryObjectStore
    ) -> None:
        row = lined_sheet(session, bid, store)

        with pytest.raises(design.DesignError, match="no proposed layout to export yet"):
            design.request_export(session, row, "pdf", SENIOR)
        assert design.exports_of(session, row)["pdf"]["state"] == "none"
