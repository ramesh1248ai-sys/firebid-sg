"""A bid's quantity takeoff: items, evidence, duplicates, rule inputs, manual items and G1.

Items show the net quantity and the allowance side by side, never folded together
(FR-QTO-10); a rule-derived item shows its rule, version, inputs and their sources
(FR-QTO-03). Measurement rules are the organisation's, versioned (FR-ADM-02).

Recomputing is queued (`qto.recompute`), as detection is; changing a rule input or deciding
a duplicate group queues it too.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from firebid.api.deps import CurrentBid, CurrentPrincipal, DbSession, require
from firebid.auth.permissions import Action
from firebid.auth.provisioning import Principal
from firebid.db.models.core import AppUser
from firebid.db.models.drawings import SheetView
from firebid.db.models.takeoff import (
    BidParameter,
    DuplicateGroup,
    Evidence,
    MeasurementRule,
    QtoItem,
)
from firebid.domain.state_machines import TransitionError
from firebid.qto import rules
from firebid.services import qto, review

router = APIRouter(prefix="/bids/{bid_id}/qto", tags=["qto"])
rules_router = APIRouter(prefix="/measurement-rules", tags=["qto"])

Point = Annotated[list[float], Field(min_length=2, max_length=2)]


class RuleInputOut(BaseModel):
    name: str
    value: float
    source: str


class DerivedByOut(BaseModel):
    rule_key: str
    rule_version: int
    rule_status: str
    inputs: list[dict[str, Any]]
    value: int | float


class ItemOut(BaseModel):
    id: uuid.UUID
    human_id: str
    version: int
    item_type: str
    classification: str | None
    description: str
    attributes: dict[str, Any]
    unit: str
    net_quantity: Decimal
    allowance_percent: Decimal | None
    allowance_quantity: Decimal
    quantity_with_allowance: Decimal
    length_mm: int | None
    level: str | None
    zone: str | None
    grid_from: str | None
    grid_to: str | None
    calculation_method: str
    rule_derived: bool
    rule: DerivedByOut | None
    manual: bool
    manual_by: str | None
    manual_at: str | None
    confidence: float | None
    state: str
    duplicate_group_id: uuid.UUID | None
    sources: list[dict[str, Any]]
    note: str | None
    supersedes_id: uuid.UUID | None
    evidence_missing: list[str]
    # Where its evidence is on each sheet, in sheet millimetres: what the viewer zooms to.
    evidence_boxes: list[dict[str, Any]]


class MarkOut(BaseModel):
    id: str
    kind: str
    object_type: str
    box: list[float]
    status: str
    band: str
    confidence: float | None
    item_id: str | None
    item_human_id: str | None
    x: float | None
    y: float | None
    points: list[list[float]]
    label: str | None


class GroupOut(BaseModel):
    id: uuid.UUID
    kind: str
    level: str | None
    status: str
    reason: str
    members: list[dict[str, Any]]
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None


class DecideGroupIn(BaseModel):
    decision: Literal["confirmed", "not_duplicate"]
    note: str | None = None


class ParameterIn(BaseModel):
    name: Literal[
        "ceiling_height_mm",
        "branch_elevation_mm",
        "sprinkler_setting_mm",
        "floor_to_floor_mm",
        "levels_served",
    ]
    value: Annotated[Decimal, Field(ge=0)]
    level: str | None = None
    source: str | None = None


class ParameterOut(BaseModel):
    id: uuid.UUID | None
    name: str
    level: str | None
    value: Decimal
    source: str
    entered: bool
    created_at: datetime | None


class MeasureIn(BaseModel):
    view_id: uuid.UUID
    points: Annotated[list[Point], Field(min_length=2, max_length=10_000)]


class MarksIn(BaseModel):
    view_id: uuid.UUID
    points: Annotated[list[Point], Field(min_length=1, max_length=10_000)]


class ManualIn(BaseModel):
    item_type: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1)
    unit: str = Field(default="no", max_length=16)
    quantity: Annotated[Decimal, Field(ge=0)] | None = None
    measure: MeasureIn | None = None
    # A count placed on the drawing: one mark per item, on a view of verified scale.
    marks: MarksIn | None = None
    classification: str | None = None
    attributes: dict[str, str] | None = None
    level: str | None = None
    zone: str | None = None
    grid_from: str | None = None
    grid_to: str | None = None
    allowance_percent: Annotated[Decimal, Field(ge=0, le=100)] | None = None


class ManualChange(BaseModel):
    description: str | None = None
    quantity: Annotated[Decimal, Field(ge=0)] | None = None
    measure: MeasureIn | None = None
    attributes: dict[str, str] | None = None
    level: str | None = None
    zone: str | None = None
    grid_from: str | None = None
    grid_to: str | None = None
    allowance_percent: Annotated[Decimal, Field(ge=0, le=100)] | None = None


class BlockersOut(BaseModel):
    clear: bool
    unresolved_groups: list[dict[str, Any]]
    incomplete_items: list[dict[str, Any]]
    pending_work: list[dict[str, Any]]
    coverage: dict[str, Any]
    unmapped_symbols: list[dict[str, Any]]
    untraced_lines: list[dict[str, Any]] = []


class ApproveIn(BaseModel):
    comment: str | None = None


class ApprovalOut(BaseModel):
    id: uuid.UUID
    gate: str
    decision: str
    decided_at: datetime
    snapshot_hash: str | None


class RuleOut(BaseModel):
    key: str
    version: int
    title: str
    definition: dict[str, Any]
    status: str
    effective_from: datetime
    retired_at: datetime | None
    source_note: str | None


class RuleChange(BaseModel):
    definition: dict[str, Any]
    title: str | None = None
    status: str = "confirmed"
    note: str | None = None


def _missing(session: DbSession, bid_id: uuid.UUID) -> dict[uuid.UUID, list[str]]:
    return {
        row.qto_item_id: list(row.missing_fields or [])
        for row in session.execute(select(Evidence).where(Evidence.bid_id == bid_id)).scalars()
    }


def item_out(item: QtoItem, missing: list[str], people: dict[uuid.UUID, str]) -> ItemOut:
    derivation: dict[str, Any] = dict(item.derivation or {})
    rule = derivation.get("rule")
    manual = derivation.get("manual") or {}
    allowance = (
        (item.net_quantity * item.allowance_percent / Decimal(100)).quantize(Decimal("0.001"))
        if item.allowance_percent
        else Decimal("0.000")
    )
    return ItemOut(
        id=item.id,
        human_id=item.human_id,
        version=item.version,
        item_type=item.item_type,
        classification=item.classification,
        description=item.description,
        attributes=dict(item.attributes or {}),
        unit=item.unit,
        net_quantity=item.net_quantity,
        allowance_percent=item.allowance_percent,
        allowance_quantity=allowance,
        quantity_with_allowance=rules.adjusted(item.net_quantity, item.allowance_percent),
        length_mm=item.length.mm if item.length is not None else None,
        level=item.level,
        zone=item.zone,
        grid_from=item.grid_from,
        grid_to=item.grid_to,
        calculation_method=item.calculation_method,
        rule_derived=item.calculation_method == "rule_derived",
        rule=DerivedByOut(**rule) if rule else None,
        manual=item.is_manual,
        manual_by=(people.get(item.created_by_id) if item.created_by_id else None)
        or manual.get("by")
        if item.is_manual
        else None,
        manual_at=manual.get("at") if item.is_manual else None,
        confidence=item.confidence,
        state=item.state,
        duplicate_group_id=item.duplicate_group_id,
        sources=list(derivation.get("sources") or []),
        note=derivation.get("note"),
        supersedes_id=item.supersedes_id,
        evidence_missing=missing,
        evidence_boxes=review.evidence_boxes(item),
    )


def _people(session: DbSession, items: list[QtoItem]) -> dict[uuid.UUID, str]:
    ids = {item.created_by_id for item in items if item.created_by_id}
    if not ids:
        return {}
    return {
        user.id: user.display_name
        for user in session.execute(select(AppUser).where(AppUser.id.in_(ids))).scalars()
    }


def _item(session: DbSession, context: CurrentBid, item_id: uuid.UUID) -> QtoItem:
    item = session.execute(
        select(QtoItem).where(QtoItem.bid_id == context.bid.id, QtoItem.id == item_id)
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such QTO item")
    return item


def _queue(session: DbSession, context: CurrentBid) -> None:
    qto.queue_recompute(session, context.bid.id, context.principal.user_id)


# --- Items ----------------------------------------------------------------------------------


@router.get("/items", response_model=list[ItemOut])
def list_items(context: CurrentBid, session: DbSession) -> list[ItemOut]:
    """The takeoff as it stands: every item not superseded."""
    items = qto.live_items(session, context.bid.id)
    missing = _missing(session, context.bid.id)
    people = _people(session, items)
    return [item_out(item, missing.get(item.id, []), people) for item in items]


class ViewSummary(BaseModel):
    id: str
    kind: str
    extent: list[float]
    scale_status: str
    denominator: float | None
    measurable: bool


class WorkbenchSheet(BaseModel):
    sheet_id: str
    sheet_number: str
    revision: str | None
    title: str | None
    level: str | None
    width_mm: float | None
    height_mm: float | None
    views: list[ViewSummary]


@router.get("/sheets", response_model=list[WorkbenchSheet])
def workbench_sheets(context: CurrentBid, session: DbSession) -> list[WorkbenchSheet]:
    """The Current sheets takeoff reads, with their views and whether each can be measured."""
    return [WorkbenchSheet(**sheet) for sheet in review.sheets(session, context.bid.id)]


@router.get("/overlay", response_model=list[MarkOut])
def overlay(context: CurrentBid, session: DbSession, sheet_id: uuid.UUID) -> list[MarkOut]:
    """Everything drawn over one sheet, with its item, status and confidence band."""
    return [MarkOut(**mark.as_json()) for mark in review.overlay(session, context.bid.id, sheet_id)]


@router.get("/items/{item_id}", response_model=ItemOut)
def get_item(item_id: uuid.UUID, context: CurrentBid, session: DbSession) -> ItemOut:
    item = _item(session, context, item_id)
    return item_out(
        item, _missing(session, context.bid.id).get(item.id, []), _people(session, [item])
    )


@router.get("/items/{item_id}/history", response_model=list[ItemOut])
def item_history(item_id: uuid.UUID, context: CurrentBid, session: DbSession) -> list[ItemOut]:
    """Every version of the item, oldest first; each keeps the rule version it used."""
    item = _item(session, context, item_id)
    versions = list(
        session.execute(
            select(QtoItem)
            .where(QtoItem.bid_id == context.bid.id, QtoItem.human_id == item.human_id)
            .order_by(QtoItem.version)
        ).scalars()
    )
    missing = _missing(session, context.bid.id)
    people = _people(session, versions)
    return [item_out(v, missing.get(v.id, []), people) for v in versions]


@router.get("/items/{item_id}/evidence")
def item_evidence(item_id: uuid.UUID, context: CurrentBid, session: DbSession) -> dict[str, Any]:
    """The item's Appendix B evidence record (FR-QTO-09)."""
    item = _item(session, context, item_id)
    return qto.evidence_for(session, context.bid, item).model_dump(mode="json")


@router.post("/recompute", status_code=202)
def recompute(
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.QTO_EDIT)],
) -> dict[str, str]:
    _queue(session, context)
    return {"status": "queued"}


@router.get("/export.csv", response_class=Response)
def export(context: CurrentBid, session: DbSession) -> Response:
    """The takeoff as a spreadsheet: net and allowance in their own columns (FR-QTO-10).

    A named user's explicit download (guardrail 7).
    """
    items = qto.live_items(session, context.bid.id)
    people = _people(session, items)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "QTO ID",
            "Version",
            "Description",
            "Classification",
            "Level",
            "Zone",
            "Grid from",
            "Grid to",
            "Unit",
            "Net quantity",
            "Allowance %",
            "Allowance quantity",
            "Quantity with allowance",
            "Calculation",
            "Rule",
            "Manual by",
            "State",
        ]
    )
    for item in items:
        out = item_out(item, [], people)
        writer.writerow(
            [
                out.human_id,
                out.version,
                out.description,
                out.classification or "",
                out.level or "",
                out.zone or "",
                out.grid_from or "",
                out.grid_to or "",
                out.unit,
                out.net_quantity,
                out.allowance_percent if out.allowance_percent is not None else "",
                out.allowance_quantity,
                out.quantity_with_allowance,
                out.calculation_method,
                f"{out.rule.rule_key} v{out.rule.rule_version}" if out.rule else "",
                out.manual_by or "",
                out.state,
            ]
        )
    filename = f"{context.bid.human_id} QTO.csv"
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- Manual items (FR-QTO-11) ---------------------------------------------------------------


def _measured(
    session: DbSession, context: CurrentBid, body: MeasureIn | MarksIn | None
) -> qto.Measured | None:
    if body is None:
        return None
    view = session.get(SheetView, body.view_id)
    if view is None or view.bid_id != context.bid.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such view")
    return qto.Measured(view, [(p[0], p[1]) for p in body.points])


@router.post("/items", response_model=ItemOut, status_code=201)
def create_manual(
    body: ManualIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.QTO_EDIT)],
) -> ItemOut:
    try:
        item = qto.create_manual(
            session,
            context.bid.id,
            principal.actor(),
            item_type=body.item_type,
            description=body.description,
            unit=body.unit,
            quantity=body.quantity,
            measured=_measured(session, context, body.measure),
            marked=_measured(session, context, body.marks),
            classification=body.classification,
            attributes=body.attributes,
            level=body.level,
            zone=body.zone,
            grid_from=body.grid_from,
            grid_to=body.grid_to,
            allowance_percent=body.allowance_percent,
        )
    except qto.QtoError as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from refusal
    return get_item(item.id, context, session)


@router.patch("/items/{item_id}", response_model=ItemOut)
def edit_manual(
    item_id: uuid.UUID,
    body: ManualChange,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.QTO_EDIT)],
) -> ItemOut:
    """A new version of a manual item; the old one stays, superseded."""
    item = _item(session, context, item_id)
    changes: dict[str, Any] = body.model_dump(exclude_unset=True, exclude={"measure"})
    if body.measure is not None:
        changes["measured"] = _measured(session, context, body.measure)
    try:
        fresh = qto.edit_manual(session, item, principal.actor(), **changes)
    except (qto.QtoError, TransitionError) as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from refusal
    return get_item(fresh.id, context, session)


@router.delete("/items/{item_id}", status_code=204)
def delete_manual(
    item_id: uuid.UUID,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.QTO_EDIT)],
) -> Response:
    item = _item(session, context, item_id)
    try:
        qto.delete_manual(session, item, principal.actor())
    except (qto.QtoError, TransitionError) as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from refusal
    return Response(status_code=204)


# --- Duplicates (FR-QTO-08) -----------------------------------------------------------------


def group_out(group: DuplicateGroup) -> GroupOut:
    return GroupOut(
        id=group.id,
        kind=group.kind,
        level=group.level,
        status=group.status,
        reason=group.reason,
        members=list(group.members or []),
        decided_by=group.decided_by,
        decided_at=group.decided_at,
        decision_note=group.decision_note,
    )


@router.get("/duplicates", response_model=list[GroupOut])
def list_groups(context: CurrentBid, session: DbSession) -> list[GroupOut]:
    """Unresolved first: each group lists every member with where it is drawn."""
    rows = session.execute(
        select(DuplicateGroup).where(DuplicateGroup.bid_id == context.bid.id)
    ).scalars()
    return [
        group_out(g)
        for g in sorted(rows, key=lambda g: (g.status != "unresolved", g.kind, str(g.level)))
    ]


@router.post("/duplicates/{group_id}", response_model=GroupOut)
def decide_group(
    group_id: uuid.UUID,
    body: DecideGroupIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.QTO_EDIT)],
) -> GroupOut:
    group = session.get(DuplicateGroup, group_id)
    if group is None or group.bid_id != context.bid.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such duplicate group")
    qto.decide_group(session, group, body.decision, principal.actor(), body.note)
    _queue(session, context)
    return group_out(group)


# --- Rule inputs ----------------------------------------------------------------------------


@router.get("/parameters", response_model=list[ParameterOut])
def list_parameters(context: CurrentBid, session: DbSession) -> list[ParameterOut]:
    """Every input the rules may use: read from sheet notes, or entered, oldest first."""
    sheets = qto.current_sheets(session, context.bid.id)
    noted = [
        ParameterOut(
            id=None,
            name=p.name,
            level=p.level,
            value=Decimal(str(p.value)),
            source=p.source,
            entered=False,
            created_at=None,
        )
        for p in qto.note_parameters(session, context.bid.id, sheets)
    ]
    entered = [
        ParameterOut(
            id=row.id,
            name=row.name,
            level=row.level,
            value=row.value,
            source=row.source,
            entered=True,
            created_at=row.created_at,
        )
        for row in session.execute(
            select(BidParameter)
            .where(BidParameter.bid_id == context.bid.id)
            .order_by(BidParameter.created_at)
        ).scalars()
    ]
    return noted + entered


@router.post("/parameters", response_model=ParameterOut, status_code=201)
def set_parameter(
    body: ParameterIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.QTO_EDIT)],
) -> ParameterOut:
    try:
        row = qto.set_parameter(
            session,
            context.bid.id,
            body.name,
            body.value,
            principal.actor(),
            level=body.level,
            source=body.source,
        )
    except qto.QtoError as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from refusal
    _queue(session, context)
    return ParameterOut(
        id=row.id,
        name=row.name,
        level=row.level,
        value=row.value,
        source=row.source,
        entered=True,
        created_at=row.created_at,
    )


# --- G1 -------------------------------------------------------------------------------------


@router.get("/g1", response_model=BlockersOut)
def g1_status(context: CurrentBid, session: DbSession) -> BlockersOut:
    """What stands between the takeoff and G1, checked now (FR-QTO-08, 09; FR-REV-04)."""
    found = qto.g1_blockers(session, context.bid.id)
    return BlockersOut(
        clear=found.clear,
        unresolved_groups=found.unresolved_groups,
        incomplete_items=found.incomplete_items,
        pending_work=found.pending_work,
        coverage=found.coverage,
        unmapped_symbols=found.unmapped_symbols,
        untraced_lines=found.untraced_lines,
    )


@router.post("/g1/approve", response_model=ApprovalOut, status_code=201)
def approve_g1(
    body: ApproveIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.GATE_G1_APPROVE)],
) -> ApprovalOut:
    try:
        approval = qto.approve_g1(
            session, context.bid, principal.actor(), "senior_estimator", body.comment
        )
    except qto.QtoError as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from refusal
    return ApprovalOut(
        id=approval.id,
        gate=approval.gate,
        decision=approval.decision,
        decided_at=approval.decided_at,
        snapshot_hash=approval.snapshot_hash,
    )


# --- Measurement rules (FR-ADM-02) ----------------------------------------------------------


def rule_out(row: MeasurementRule) -> RuleOut:
    return RuleOut(
        key=row.key,
        version=row.version,
        title=row.title,
        definition=dict(row.definition),
        status=row.status,
        effective_from=row.effective_from,
        retired_at=row.retired_at,
        source_note=row.source_note,
    )


@rules_router.get("", response_model=list[RuleOut])
def list_rules(principal: CurrentPrincipal, session: DbSession) -> list[RuleOut]:
    rows = qto.rule_rows(session, principal.organisation_id)
    return [rule_out(rows[key]) for key in sorted(rows)]


@rules_router.get("/{key}/history", response_model=list[RuleOut])
def rule_history(key: str, principal: CurrentPrincipal, session: DbSession) -> list[RuleOut]:
    rows = qto.rule_history(session, principal.organisation_id, key)
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such measurement rule")
    return [rule_out(row) for row in rows]


@rules_router.post("/{key}", response_model=RuleOut)
def change_rule(
    key: str,
    body: RuleChange,
    session: DbSession,
    principal: Annotated[Principal, require(Action.MEASUREMENT_RULES_CHANGE)],
) -> RuleOut:
    """A new version of the rule. Items already taken off keep the version they used."""
    try:
        row = qto.edit_rule(
            session,
            principal.organisation_id,
            key,
            definition=body.definition,
            actor=principal.actor(),
            title=body.title,
            status=body.status,
            note=body.note,
        )
    except qto.QtoError as refusal:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(refusal)) from refusal
    return rule_out(row)
