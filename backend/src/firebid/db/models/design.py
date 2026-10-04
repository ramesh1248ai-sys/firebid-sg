"""Design development: what the platform proposed for a design-intent sheet (P1-12)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from firebid.db.base import Base
from firebid.db.mixins import BidScoped, UuidPk, personal

DESIGN_STATES = ("proposed", "confirmed", "blocked")
SCOPE_SOURCES = ("match_lines", "person")


class SheetDesign(UuidPk, BidScoped, Base):
    """One sheet's design basis and, once a person confirms it, its proposed layout.

    * `proposed`: the criteria its notes state have been read, and one is suggested. Nothing
      is laid out, and nothing reaches the takeoff, until a person confirms the criterion.
    * `confirmed`: a named person chose the criterion. The layout made from it is stored as
      detections and pipe runs marked `designed`, which are proposals in their turn.
    * `blocked`: the sheet cannot be designed, and `note` says why (no plan view at a
      verified scale, no base plan under the services).
    """

    __tablename__ = "sheet_design"
    __table_args__ = (
        CheckConstraint(f"state IN {DESIGN_STATES}", name="state_known"),
        CheckConstraint(
            "state <> 'confirmed' OR (criterion IS NOT NULL AND confirmed_by IS NOT NULL)",
            name="confirmation_named",
        ),
        CheckConstraint("state <> 'blocked' OR note IS NOT NULL", name="blocked_says_why"),
        CheckConstraint(
            f"scope_source IS NULL OR scope_source IN {SCOPE_SOURCES}", name="scope_source_known"
        ),
    )

    sheet_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sheet.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    view_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed")
    note: Mapped[str | None] = mapped_column(Text)
    # The sheet says the design is the contractor's to develop, in these words.
    design_intent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    intent_quote: Mapped[str | None] = mapped_column(Text)
    # Sprinklers detected on the sheet as drawn: a layout on top of them would count twice.
    drawn_heads: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Every criterion the notes state, and the rules' default: what a person chooses from.
    criteria: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False, default=list)
    criterion: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    # The part of the plan this sheet answers for, where match lines share a floor: a
    # polygon in sheet millimetres. Empty means the whole view.
    scope: Mapped[list[list[float]] | None] = mapped_column(JSONB)
    # Where the scope came from: the sheet's match lines (FR-DSN-06), or a person, whose
    # choice a later reading of the sheet leaves alone. None: nothing has set one.
    scope_source: Mapped[str | None] = mapped_column(String(16))
    # The match lines found across the plan: each label's words, the sheet it names, where
    # the line runs, the side proposed for this sheet and why, and the side now taken.
    match_lines: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    rule_version: Mapped[int | None] = mapped_column(Integer)
    # The layout as made: every space with its heads, and each omission with its rule.
    spaces: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False, default=list)
    totals: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    laid_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="SET NULL")
    )
    confirmed_by: Mapped[str | None] = mapped_column(
        String(200), info=personal("the name of the person who confirmed the design basis")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
