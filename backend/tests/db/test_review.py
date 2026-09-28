# ruff: noqa: F811  (the fixtures below are imported by name and requested by name)
"""The verification workbench's data (P1-08): the overlay, the queue, actions and G1.

The tender is P1-07's: a general arrangement, an enlarged plan of its riser area and a
riser schematic, detected and taken off.
"""

from __future__ import annotations

from collections import Counter

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.db.models.core import Bid, Organisation
from firebid.db.models.documents import SheetRevision
from firebid.services import review
from tests.db.test_qto import PENDENT, estimator, items, tender  # noqa: F401
from tests.db.test_sheet_views import SignIn, app, sign_in  # noqa: F401
from tests.db.test_symbol_mapping import no_tiles, store  # noqa: F401


def sheet_id(session: Session, number: str) -> str:
    revision = session.execute(
        select(SheetRevision).where(SheetRevision.sheet_number == number)
    ).scalar_one()
    return str(revision.sheet_id)


@pytest.mark.req("FR-REV-01")
class TestOverlay:
    def test_every_mark_carries_its_item_status_and_band(
        self, session: Session, organisation: Organisation, tender: Bid, sign_in: SignIn
    ) -> None:
        _, principal = estimator(session, organisation, tender)
        general = sheet_id(session, "FP-L05-201")

        marks = (
            sign_in(principal)
            .get(f"/bids/{tender.id}/qto/overlay", params={"sheet_id": general})
            .json()
        )

        heads = [m for m in marks if m["object_type"] == "sprinkler_pendent"]
        assert len(heads) == 16
        pendent = items(session, tender)[PENDENT]
        assert {m["item_id"] for m in heads} == {str(pendent.id)}
        assert {m["status"] for m in heads} == {"proposed"}
        assert {m["band"] for m in marks} <= {"high", "medium", "low"}
        runs = [m for m in marks if m["kind"] == "run"]
        assert runs and all(len(m["points"]) >= 2 and m["item_id"] for m in runs)
        assert all(m["box"][0] <= m["box"][2] and m["box"][1] <= m["box"][3] for m in marks)

    def test_what_another_sheet_counts_is_shown_as_a_duplicate(
        self, session: Session, tender: Bid
    ) -> None:
        enlarged = sheet_id(session, "FP-L05-301")

        marks = review.overlay(session, tender.id, enlarged)  # type: ignore[arg-type]

        statuses = Counter(m.status for m in marks if m.kind == "detection")
        assert statuses["duplicate"] >= 4
        assert all(m.item_id is None for m in marks if m.status == "duplicate")

    def test_an_item_says_where_its_evidence_is(self, session: Session, tender: Bid) -> None:
        pendent = items(session, tender)[PENDENT]

        boxes = review.evidence_boxes(pendent)

        assert [b["sheet_id"] for b in boxes] == [sheet_id(session, "FP-L05-201")]
        x0, y0, x1, y1 = boxes[0]["box"]
        assert x1 > x0 and y1 > y0
