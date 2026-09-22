"""Builders for database tests: the smallest valid rows, so tests show only what they care about."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from firebid.db.models.core import Bid
from firebid.db.models.documents import Document, Sheet, SheetRevision
from firebid.db.models.takeoff import QtoItem
from firebid.domain.state_machines import QtoItemState, SheetRevisionState


def make_document(session: Session, bid: Bid, filename: str = "FP-L05-201.pdf") -> Document:
    document = Document(
        bid_id=bid.id,
        filename=filename,
        media_type="application/pdf",
        sha256=uuid.uuid4().hex * 2,
        byte_size=1024,
        storage_key=f"bids/{bid.id}/{uuid.uuid4()}.pdf",
    )
    session.add(document)
    session.flush()
    return document


def make_sheet(session: Session, bid: Bid, document: Document | None = None) -> Sheet:
    document = document or make_document(session, bid)
    sheet = Sheet(bid_id=bid.id, document_id=document.id, index_in_document=0)
    session.add(sheet)
    session.flush()
    return sheet


def make_sheet_revision(
    session: Session,
    bid: Bid,
    sheet_number: str = "FP-L05-201",
    revision: str = "R04",
    state: SheetRevisionState = SheetRevisionState.REGISTERED,
) -> SheetRevision:
    sheet = make_sheet(session, bid)
    sheet_revision = SheetRevision(
        bid_id=bid.id,
        sheet_id=sheet.id,
        sheet_number=sheet_number,
        revision_label=revision,
        state=str(state),
    )
    session.add(sheet_revision)
    session.flush()
    return sheet_revision


def make_qto_item(
    session: Session,
    bid: Bid,
    state: QtoItemState = QtoItemState.PROPOSED,
    human_id: str = "QTO-000001",
) -> QtoItem:
    item = QtoItem(
        bid_id=bid.id,
        human_id=human_id,
        item_type="pipe",
        description="150 mm fire main",
        unit="m",
        net_quantity=Decimal("128.400"),
        calculation_method="centreline_length",
        state=str(state),
    )
    session.add(item)
    session.flush()
    return item
