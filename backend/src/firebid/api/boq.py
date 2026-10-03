"""A bid's bills of quantities: the company BOQ, the client's, the mapping between them,
reconciliation, exports, the trace check and G2 (FR-BOQ-01 to 06). BOQ templates are the
organisation's, versioned (FR-ADM-03).

Mapping proposals come from the rules at once; what they leave goes to the model on the
worker (`boq.map`). Every proposal waits for a person.
"""

from __future__ import annotations

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
from firebid.db.models.commercial import (
    BoqLine,
    BoqLineSource,
    BoqTemplate,
    ClientBoq,
    ClientBoqMapping,
)
from firebid.db.models.documents import Document
from firebid.db.models.takeoff import QtoItem
from firebid.services import boq
from firebid.storage.object_store import get_object_store

router = APIRouter(prefix="/bids/{bid_id}/boq", tags=["boq"])
templates_router = APIRouter(prefix="/boq-templates", tags=["boq"])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def refused(error: boq.BoqError, code: int = status.HTTP_409_CONFLICT) -> HTTPException:
    return HTTPException(code, str(error))


def download(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- The company BOQ ------------------------------------------------------------------------


class LineOut(BaseModel):
    id: uuid.UUID
    line_key: str | None
    section: str | None
    group_heading: str | None
    item_no: str | None
    description: str
    level: str | None
    unit: str
    quantity: Decimal
    allowance_percent: Decimal | None
    unit_rate: Decimal | None
    amount: Decimal | None
    is_provisional: bool
    is_lump_sum: bool
    marker_note: str | None
    qto_items: list[str]
    traced: bool


class BoqOut(BaseModel):
    id: uuid.UUID
    version: int
    template_key: str | None
    template_version: int | None
    created_at: datetime
    lines: list[LineOut]


class BuildIn(BaseModel):
    template_key: str | None = None


class MarkedLineIn(BaseModel):
    description: str = Field(min_length=1)
    unit: str = Field(min_length=1, max_length=16)
    quantity: Decimal = Decimal(1)
    marker: Literal["provisional", "lump_sum"]
    note: str = Field(min_length=1)
    amount: Decimal | None = None


class MarkIn(BaseModel):
    marker: Literal["provisional", "lump_sum"] | None
    note: str | None = None


def boq_out(session: DbSession, bid_id: uuid.UUID) -> BoqOut:
    current = boq.current_boq(session, bid_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no BOQ is built yet")
    lines = boq.lines_of(session, current)
    sources: dict[uuid.UUID, list[str]] = {}
    for line_id, human_id in session.execute(
        select(BoqLineSource.boq_line_id, QtoItem.human_id)
        .join(QtoItem, QtoItem.id == BoqLineSource.qto_item_id)
        .where(BoqLineSource.boq_line_id.in_([line.id for line in lines]))
    ).tuples():
        sources.setdefault(line_id, []).append(human_id)
    untraced = {row["id"] for row in boq.untraced_lines(session, bid_id)}
    return BoqOut(
        id=current.id,
        version=current.version,
        template_key=current.template_key,
        template_version=current.template_version,
        created_at=current.created_at,
        lines=[
            LineOut(
                id=line.id,
                line_key=line.line_key,
                section=line.section,
                group_heading=line.group_heading,
                item_no=line.item_no,
                description=line.description,
                level=line.level,
                unit=line.unit,
                quantity=line.quantity,
                allowance_percent=line.allowance_percent,
                unit_rate=line.unit_rate.amount if line.unit_rate else None,
                amount=line.amount.amount if line.amount else None,
                is_provisional=line.is_provisional,
                is_lump_sum=line.is_lump_sum,
                marker_note=line.marker_note,
                qto_items=sorted(sources.get(line.id, [])),
                traced=str(line.id) not in untraced,
            )
            for line in lines
        ],
    )


@router.get("", response_model=BoqOut)
def get_boq(context: CurrentBid, session: DbSession) -> BoqOut:
    """The current company BOQ, each line with the QTO items behind it (FR-BOQ-01, 05)."""
    return boq_out(session, context.bid.id)


@router.post("/build", response_model=BoqOut, status_code=201)
def build(
    body: BuildIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.BOQ_EDIT)],
) -> BoqOut:
    """A new version from the verified takeoff. Client mappings follow their lines."""
    try:
        boq.build_company_boq(session, context.bid.id, principal.actor(), body.template_key)
    except boq.BoqError as refusal:
        raise refused(refusal) from refusal
    return boq_out(session, context.bid.id)


@router.post("/lines", response_model=BoqOut, status_code=201)
def add_line(
    body: MarkedLineIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.BOQ_EDIT)],
) -> BoqOut:
    """A provisional sum or lump sum, which has no measured quantity behind it."""
    try:
        boq.add_marked_line(
            session,
            context.bid.id,
            principal.actor(),
            description=body.description,
            unit=body.unit,
            quantity=body.quantity,
            marker=body.marker,
            note=body.note,
            amount=body.amount,
        )
    except boq.BoqError as refusal:
        raise refused(refusal) from refusal
    return boq_out(session, context.bid.id)


@router.post("/lines/{line_id}/marker", response_model=BoqOut)
def mark(
    line_id: uuid.UUID,
    body: MarkIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.BOQ_EDIT)],
) -> BoqOut:
    line = session.get(BoqLine, line_id)
    if line is None or line.bid_id != context.bid.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such BOQ line")
    try:
        boq.mark_line(session, line, body.marker, principal.actor(), body.note)
    except boq.BoqError as refusal:
        raise refused(refusal, status.HTTP_422_UNPROCESSABLE_ENTITY) from refusal
    return boq_out(session, context.bid.id)


@router.get("/export.xlsx")
def export_company(context: CurrentBid, session: DbSession) -> Response:
    try:
        content = boq.company_workbook(session, context.bid.id)
    except boq.BoqError as refusal:
        raise refused(refusal, status.HTTP_404_NOT_FOUND) from refusal
    return download(content, f"{context.bid.human_id} BOQ.xlsx")


# --- The client's BOQ -----------------------------------------------------------------------


class ClientBoqOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    sheet_name: str | None
    status: str
    header_row: int | None
    column_map: dict[str, int] | None
    reason: str | None
    proposal: dict[str, Any] | None
    lines: int


class ColumnsIn(BaseModel):
    # sheet name -> {"header_row": 7, "columns": {"description": 2, "quantity": 4, ...}}
    sheets: dict[str, dict[str, Any]]


@router.get("/client", response_model=list[ClientBoqOut])
def client_boqs(context: CurrentBid, session: DbSession) -> list[ClientBoqOut]:
    """The client's bills as read, and any waiting for a person to confirm their columns."""
    from sqlalchemy import func

    from firebid.db.models.commercial import ClientBoqLine

    counts = dict(
        session.execute(
            select(ClientBoqLine.client_boq_id, func.count())
            .where(ClientBoqLine.bid_id == context.bid.id)
            .group_by(ClientBoqLine.client_boq_id)
        )
        .tuples()
        .all()
    )
    rows = session.execute(
        select(ClientBoq, Document.filename)
        .join(Document, Document.id == ClientBoq.document_id)
        .where(ClientBoq.bid_id == context.bid.id)
        .order_by(Document.filename, ClientBoq.sheet_name)
    ).all()
    return [
        ClientBoqOut(
            id=row.id,
            document_id=row.document_id,
            filename=filename,
            sheet_name=row.sheet_name,
            status=row.status,
            header_row=row.header_row,
            column_map=row.column_map,
            reason=row.reason,
            proposal=row.proposal,
            lines=counts.get(row.id, 0),
        )
        for row, filename in rows
    ]


@router.post("/client/{client_boq_id}/columns", response_model=list[ClientBoqOut])
def confirm_columns(
    client_boq_id: uuid.UUID,
    body: ColumnsIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.BOQ_EDIT)],
) -> list[ClientBoqOut]:
    """A person's header row and columns, per sheet; the bill is read again with them."""
    row = session.get(ClientBoq, client_boq_id)
    if row is None or row.bid_id != context.bid.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such client BOQ")
    try:
        boq.confirm_columns(session, get_object_store(), row, body.sheets, principal.actor())
    except boq.BoqError as refusal:
        raise refused(refusal, status.HTTP_422_UNPROCESSABLE_ENTITY) from refusal
    return client_boqs(context, session)


@router.get("/client/{document_id}/priced.xlsx")
def export_priced(document_id: uuid.UUID, context: CurrentBid, session: DbSession) -> Response:
    """The client's own workbook with our rates in its rate cells; nothing else changed."""
    try:
        content = boq.priced_client_workbook(
            session, get_object_store(), context.bid.id, document_id
        )
    except boq.BoqError as refusal:
        raise refused(refusal, status.HTTP_404_NOT_FOUND) from refusal
    document = session.get(Document, document_id)
    name = document.filename if document else "client BOQ.xlsx"
    stem = name.rsplit(".", 1)[0]
    return download(content, f"{stem} - priced.xlsx")


# --- Mapping --------------------------------------------------------------------------------


class ClientMappingOut(BaseModel):
    client_line_id: uuid.UUID
    client_ref: str
    client_item: str | None
    section: str | None
    description: str | None
    unit: str | None
    quantity: Decimal | None
    kind: str
    mapping_id: uuid.UUID | None
    state: str | None
    method: str | None
    maps_to: str | None
    confidence: float | None
    reason: str | None
    variance_percent: float | None
    flagged: bool
    # The rules found nothing and the model has not been asked yet (it is, on the worker).
    awaiting_model: bool = False


class MappingDecisionIn(BaseModel):
    decision: Literal["confirm", "correct", "reject"]
    maps_to: str | None = None
    note: str | None = None


@router.get("/mappings", response_model=list[ClientMappingOut])
def list_mappings(context: CurrentBid, session: DbSession) -> list[ClientMappingOut]:
    """Every client line, with the proposal or decision about which measured line it is."""
    existing = boq.mappings(session, context.bid.id)
    out = []
    for sheet, line in boq.client_lines(session, context.bid.id):
        row = existing.get(line.id)
        out.append(
            ClientMappingOut(
                client_line_id=line.id,
                client_ref=boq.reference(sheet, line),
                client_item=line.item_no,
                section=line.section,
                description=line.description,
                unit=line.unit,
                quantity=line.quantity,
                kind=line.kind,
                mapping_id=row.id if row else None,
                state=row.state if row else None,
                method=row.method if row else None,
                maps_to=row.boq_line_key if row else None,
                confidence=row.confidence if row else None,
                reason=row.reason if row else None,
                variance_percent=row.variance_percent if row else None,
                flagged=row.flagged if row else False,
                awaiting_model=bool(row and row.provenance.get("awaiting_model")),
            )
        )
    return out


@router.post("/mappings/propose", response_model=list[ClientMappingOut])
def propose(
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.BOQ_EDIT)],
) -> list[ClientMappingOut]:
    """The rules' proposals now; the model's for what they leave, on the worker."""
    try:
        boq.propose_mappings(session, context.bid.id)
    except boq.BoqError as refusal:
        raise refused(refusal) from refusal
    rows = list_mappings(context, session)
    if any(row.awaiting_model for row in rows):
        from firebid.jobs.enqueue import enqueue
        from firebid.jobs.tasks import propose_boq_mappings_job

        actor = principal.actor()
        enqueue(
            session,
            propose_boq_mappings_job,
            bid_id=str(context.bid.id),
            user_id=str(actor.id) if actor.id else "",
        )
    return rows


@router.post("/mappings/{mapping_id}", response_model=ClientMappingOut)
def decide(
    mapping_id: uuid.UUID,
    body: MappingDecisionIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.BOQ_EDIT)],
) -> ClientMappingOut:
    row = session.get(ClientBoqMapping, mapping_id)
    if row is None or row.bid_id != context.bid.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such mapping")
    try:
        boq.decide_mapping(
            session,
            row,
            principal.actor(),
            decision=body.decision,
            boq_line_key=body.maps_to,
            note=body.note,
        )
    except boq.BoqError as refusal:
        raise refused(refusal, status.HTTP_422_UNPROCESSABLE_ENTITY) from refusal
    [out] = [m for m in list_mappings(context, session) if m.mapping_id == row.id]
    return out


# --- Reconciliation -------------------------------------------------------------------------


class ReconciliationOut(BaseModel):
    kind: str
    client_ref: str | None
    client_item: str | None
    client_description: str | None
    client_unit: str | None
    client_quantity: Decimal | None
    line_item: str | None
    line_description: str | None
    unit: str | None
    measured_quantity: Decimal | None
    variance: Decimal | None
    variance_percent: Decimal | None
    flagged: bool
    state: str | None
    qto_items: list[str]
    evidence_links: list[str]


@router.get("/reconciliation", response_model=list[ReconciliationOut])
def reconciliation(context: CurrentBid, session: DbSession) -> list[ReconciliationOut]:
    """Client against measured; flagged rows are clarification candidates (FR-BOQ-03)."""
    return [ReconciliationOut(**vars(row)) for row in boq.reconciliation(session, context.bid.id)]


@router.get("/reconciliation.xlsx")
def export_reconciliation(context: CurrentBid, session: DbSession) -> Response:
    return download(
        boq.reconciliation_workbook(session, context.bid.id),
        f"{context.bid.human_id} BOQ reconciliation.xlsx",
    )


# --- Conventions ----------------------------------------------------------------------------


class ConventionOption(BaseModel):
    key: str
    text: str


class ConventionOut(BaseModel):
    name: str
    label: str
    chosen: str
    options: list[ConventionOption]


class ConventionsOut(BaseModel):
    version: int | None
    conventions: list[ConventionOut]
    qualification_text: str


class ConventionsIn(BaseModel):
    settings: dict[str, str]


def conventions_out(session: DbSession, bid_id: uuid.UUID) -> ConventionsOut:
    from firebid.boq import reconcile

    row = boq.conventions_of(session, bid_id)
    chosen = reconcile.validate(dict(row.settings) if row else {})
    return ConventionsOut(
        version=row.version if row else None,
        conventions=[
            ConventionOut(
                name=name,
                label=str(option.get("label") or name),
                chosen=chosen[name],
                options=[
                    ConventionOption(key=key, text=str(text))
                    for key, text in dict(option["options"]).items()
                ],
            )
            for name, option in reconcile.conventions().items()
        ],
        qualification_text=boq.qualification_text(session, bid_id),
    )


@router.get("/conventions", response_model=ConventionsOut)
def get_conventions(context: CurrentBid, session: DbSession) -> ConventionsOut:
    """The tender's measurement conventions, worded for the qualifications (FR-BOQ-06)."""
    return conventions_out(session, context.bid.id)


@router.put("/conventions", response_model=ConventionsOut)
def set_conventions(
    body: ConventionsIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.BOQ_EDIT)],
) -> ConventionsOut:
    try:
        boq.set_conventions(session, context.bid.id, body.settings, principal.actor())
    except boq.BoqError as refusal:
        raise refused(refusal, status.HTTP_422_UNPROCESSABLE_ENTITY) from refusal
    return conventions_out(session, context.bid.id)


# --- G2 -------------------------------------------------------------------------------------


class G2Out(BaseModel):
    clear: bool
    g1_approved: bool
    boq_built: bool
    untraced_lines: list[dict[str, Any]]
    unsourced_lines: list[dict[str, Any]] = []


class ApproveIn(BaseModel):
    comment: str | None = None


class ApprovalOut(BaseModel):
    id: uuid.UUID
    gate: str
    decision: str
    decided_at: datetime
    snapshot_hash: str | None


@router.get("/g2", response_model=G2Out)
def g2_status(context: CurrentBid, session: DbSession) -> G2Out:
    found = boq.g2_blockers(session, context.bid.id)
    return G2Out(
        clear=found.clear,
        g1_approved=found.g1_approved,
        boq_built=found.boq_built,
        untraced_lines=found.untraced_lines,
        unsourced_lines=found.unsourced_lines,
    )


@router.post("/g2/approve", response_model=ApprovalOut, status_code=201)
def approve_g2(
    body: ApproveIn,
    context: CurrentBid,
    session: DbSession,
    principal: Annotated[Principal, require(Action.GATE_G2_APPROVE)],
) -> ApprovalOut:
    try:
        approval = boq.approve_g2(
            session, context.bid, principal.actor(), "senior_estimator", body.comment
        )
    except boq.BoqError as refusal:
        raise refused(refusal) from refusal
    return ApprovalOut(
        id=approval.id,
        gate=approval.gate,
        decision=approval.decision,
        decided_at=approval.decided_at,
        snapshot_hash=approval.snapshot_hash,
    )


# --- Templates (FR-ADM-03) ------------------------------------------------------------------


class TemplateOut(BaseModel):
    key: str
    version: int
    title: str
    definition: dict[str, Any]
    status: str
    change_note: str | None
    retired_at: datetime | None


class TemplateChange(BaseModel):
    definition: dict[str, Any]
    title: str | None = None
    status: str = "confirmed"
    note: str | None = None


def template_out(row: BoqTemplate) -> TemplateOut:
    return TemplateOut(
        key=row.key,
        version=row.version,
        title=row.title,
        definition=dict(row.definition),
        status=row.status,
        change_note=row.change_note,
        retired_at=row.retired_at,
    )


@templates_router.get("", response_model=list[TemplateOut])
def list_templates(principal: CurrentPrincipal, session: DbSession) -> list[TemplateOut]:
    rows = boq.template_rows(session, principal.organisation_id)
    return [template_out(rows[key]) for key in sorted(rows)]


@templates_router.get("/{key}/history", response_model=list[TemplateOut])
def template_history(
    key: str, principal: CurrentPrincipal, session: DbSession
) -> list[TemplateOut]:
    rows = boq.template_history(session, principal.organisation_id, key)
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such BOQ template")
    return [template_out(row) for row in rows]


@templates_router.post("/{key}", response_model=TemplateOut)
def change_template(
    key: str,
    body: TemplateChange,
    session: DbSession,
    principal: Annotated[Principal, require(Action.BOQ_TEMPLATE_CHANGE)],
) -> TemplateOut:
    """A new version of the template. BOQs already built keep the version they used."""
    try:
        row = boq.edit_template(
            session,
            principal.organisation_id,
            key,
            definition=body.definition,
            actor=principal.actor(),
            title=body.title,
            status=body.status,
            note=body.note,
        )
    except boq.BoqError as refusal:
        raise refused(refusal, status.HTTP_422_UNPROCESSABLE_ENTITY) from refusal
    return template_out(row)
