"""Gates G3 and G4, the frozen submission, and the tender's outcome (P2-08).

* **G3** (Commercial Director) rests on G2, a resolved scope checklist and treated risks.
  It records the approver, the time, a comment and the hash of the estimate approved, and
  moves the bid to "approved for submission".
* **G4** (Commercial Director) freezes the submission. A manifest lists every record the
  submission rests on (takeoff, evidence, bill, prices and their sources, clarifications,
  qualifications, risks, approvals) with a content hash each, and the files exported with
  it. The manifest and the files are written once to the snapshot store, the snapshot row
  is append-only, and the G4 approval records the manifest's hash. The bid moves to
  "submitted".
* **Nothing is sent.** After G4 the files can be downloaded by a person on the bid. There
  is no path here, or anywhere in the platform, that transmits a bid to a client
  (FR-PKG-02; autonomy level L3 is not permitted).
* **`verify_snapshot`** reads the manifest and files back and checks every hash.
* **The outcome** (awarded, lost, withdrawn) is recorded by a person, with the price and
  the reasons, and moves the bid's lifecycle.
"""

from __future__ import annotations

import hashlib
import io
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

import structlog
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from firebid.db.audit import record_event
from firebid.db.models.clarifications import Clarification, ClarificationSource, Qualification
from firebid.db.models.commercial import (
    BoqLineSource,
    MeasurementConvention,
    Rate,
)
from firebid.db.models.core import Bid
from firebid.db.models.costing import (
    BidPriceBasis,
    CostBuildupLine,
    Quotation,
    QuotationLine,
)
from firebid.db.models.labour import LabourCondition
from firebid.db.models.risk import Risk, ScopeCheck
from firebid.db.models.submission import OUTCOMES, BidOutcome, SubmissionSnapshot
from firebid.db.models.takeoff import Evidence
from firebid.db.models.workflow import Approval
from firebid.domain.actors import Actor, AuditContext
from firebid.domain.state_machines import BidState, Role, TransitionError
from firebid.domain.values import Money
from firebid.storage.object_store import ObjectStore

log = structlog.get_logger("firebid.submission")

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
GATE_ROLES = {
    "G1": str(Role.SENIOR_ESTIMATOR),
    "G2": str(Role.SENIOR_ESTIMATOR),
    "G3": str(Role.COMMERCIAL_DIRECTOR),
    "G4": str(Role.COMMERCIAL_DIRECTOR),
}


class GateError(ValueError):
    """A gate that cannot be approved, or a submission request that cannot be done."""


def _context(bid: Bid) -> AuditContext:
    return AuditContext(organisation_id=bid.organisation_id, bid_id=bid.id)


# --- Gates ------------------------------------------------------------------------------------


def approval_of(session: Session, bid_id: uuid.UUID, gate: str) -> Approval | None:
    """The approval in force for a gate: the latest not reopened by a later change."""
    return (
        session.execute(
            select(Approval)
            .where(
                Approval.bid_id == bid_id,
                Approval.gate == gate,
                Approval.decision == "approved",
                Approval.reopened_at.is_(None),
            )
            .order_by(Approval.decided_at.desc())
        )
        .scalars()
        .first()
    )


def g3_blockers(session: Session, bid: Bid) -> list[str]:
    from firebid.services import risk

    found = []
    if approval_of(session, bid.id, "G2") is None:
        found.append("G2 is not approved")
    readiness = risk.g3_readiness(session, bid.id)
    if not readiness.ready:
        found.append(readiness.describe())
    if bid.state != str(BidState.UNDER_REVIEW) and approval_of(session, bid.id, "G3") is None:
        found.append(f"the bid is {bid.state.replace('_', ' ')}, not under review")
    return found


def g4_blockers(session: Session, bid: Bid) -> list[str]:
    from firebid.services import clarifications

    found = []
    if approval_of(session, bid.id, "G3") is None:
        found.append("G3 is not approved")
    elif bid.state != str(BidState.APPROVED_FOR_SUBMISSION):
        found.append(f"the bid is {bid.state.replace('_', ' ')}, not approved for submission")
    unresolved = sum(
        1
        for row in clarifications.register(session, bid.id)
        if row.state in clarifications.OPEN_STATES
    )
    if unresolved:
        found.append(
            f"{unresolved} clarification(s) unresolved: prepare the submission, so each "
            "becomes a qualification"
        )
    undecided = session.execute(
        select(func.count())
        .select_from(Qualification)
        .where(Qualification.bid_id == bid.id, Qualification.state == "proposed")
    ).scalar_one()
    if undecided:
        found.append(f"{undecided} qualification(s) neither accepted nor rejected")
    return found


@dataclass
class GateStatus:
    gate: str
    role: str
    approved: bool
    blockers: list[str]
    approver_role: str | None = None
    approver_id: uuid.UUID | None = None
    decided_at: datetime | None = None
    comment: str | None = None
    snapshot_hash: str | None = None


def gate_status(session: Session, bid: Bid) -> list[GateStatus]:
    """Each gate: whether it is approved, by whom and on what, or what stands in the way."""
    from firebid.services import boq, qto

    def blockers_of(gate: str) -> list[str]:
        if gate == "G1":
            found = qto.g1_blockers(session, bid.id)
            return [] if found.clear else ["the takeoff is not ready: see the workbench"]
        if gate == "G2":
            described = boq.g2_blockers(session, bid.id).describe()
            return [described] if described else []
        return g3_blockers(session, bid) if gate == "G3" else g4_blockers(session, bid)

    out = []
    for gate in ("G1", "G2", "G3", "G4"):
        approval = approval_of(session, bid.id, gate)
        out.append(
            GateStatus(
                gate=gate,
                role=GATE_ROLES[gate],
                approved=approval is not None,
                blockers=[] if approval is not None else blockers_of(gate),
                approver_role=approval.approver_role if approval else None,
                approver_id=approval.approver_id if approval else None,
                decided_at=approval.decided_at if approval else None,
                comment=approval.comment if approval else None,
                snapshot_hash=approval.snapshot_hash if approval else None,
            )
        )
    return out


def _named_approver(actor: Actor, gate: str) -> uuid.UUID:
    if actor.id is None:
        raise GateError("a gate is approved by a named person")
    if GATE_ROLES[gate] not in actor.roles:
        raise GateError(f"{gate} is approved by the {GATE_ROLES[gate].replace('_', ' ')}")
    return actor.id


def estimate_hash(session: Session, bid: Bid) -> str:
    """What G3 approves, as one hash: the estimate's figures, the bill, and what the offer
    is qualified by."""
    from firebid.services import boq, review_pack, risk

    current = boq.current_boq(session, bid.id)
    body = {
        "figures": review_pack.build(session, bid).figures,
        "boq": boq.boq_snapshot_hash(boq.lines_of(session, current)) if current else None,
        "qualifications": sorted(
            f"{row.kind}|{row.state}|{row.text}" for row in risk.qualifications(session, bid.id)
        ),
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def approve_g3(session: Session, bid: Bid, actor: Actor, comment: str | None = None) -> Approval:
    """Record G3 on the estimate as it stands, or raise saying what blocks it."""
    from firebid.services.transitions import apply_transition

    approver = _named_approver(actor, "G3")
    blockers = g3_blockers(session, bid)
    if blockers:
        raise GateError("G3 is blocked: " + "; ".join(blockers))
    approval = Approval(
        bid_id=bid.id,
        gate="G3",
        decision="approved",
        approver_id=approver,
        approver_role=GATE_ROLES["G3"],
        decided_at=datetime.now(UTC),
        comment=comment,
        snapshot_hash=estimate_hash(session, bid),
    )
    session.add(approval)
    session.flush()
    try:
        apply_transition(
            session, bid, target=BidState.APPROVED_FOR_SUBMISSION, actor=actor, reason=comment
        )
    except TransitionError as refusal:
        raise GateError(str(refusal)) from refusal
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action="gate G3: approve",
        entity_type=Approval.__tablename__,
        entity_id=approval.id,
        after={"gate": "G3", "decision": "approved", "snapshot_hash": approval.snapshot_hash},
        reason=comment,
    )
    return approval


def approve_g4(
    session: Session, bid: Bid, actor: Actor, store: ObjectStore, comment: str | None = None
) -> tuple[Approval, SubmissionSnapshot]:
    """Record G4: freeze the submission, record the approval on its hash, and move the bid
    to submitted. The platform sends nothing; the files are then offered for download."""
    from firebid.services.transitions import apply_transition

    approver = _named_approver(actor, "G4")
    blockers = g4_blockers(session, bid)
    if blockers:
        raise GateError("G4 is blocked: " + "; ".join(blockers))
    decided = datetime.now(UTC)
    snapshot = freeze(session, bid, actor, store, decided)
    approval = Approval(
        bid_id=bid.id,
        gate="G4",
        decision="approved",
        approver_id=approver,
        approver_role=GATE_ROLES["G4"],
        decided_at=decided,
        comment=comment,
        snapshot_hash=snapshot.manifest_sha256,
    )
    session.add(approval)
    session.flush()
    try:
        apply_transition(session, bid, target=BidState.SUBMITTED, actor=actor, reason=comment)
    except TransitionError as refusal:
        raise GateError(str(refusal)) from refusal
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action="gate G4: approve",
        entity_type=Approval.__tablename__,
        entity_id=approval.id,
        after={
            "gate": "G4",
            "decision": "approved",
            "snapshot_hash": approval.snapshot_hash,
            "snapshot_id": str(snapshot.id),
            "files": [str(item["name"]) for item in snapshot.files],
        },
        reason=comment,
    )
    return approval, snapshot


# --- The frozen submission (FR-PKG-03) --------------------------------------------------------


def _plain(value: Any) -> Any:
    if isinstance(value, Money):
        return str(value.amount)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    return value


def canonical(value: Any) -> bytes:
    return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), default=str).encode()


def record_hash(row: Any) -> str:
    """A record's content, as one hash over every column."""
    columns = {attr.key: getattr(row, attr.key) for attr in inspect(row).mapper.column_attrs}
    return hashlib.sha256(canonical(columns)).hexdigest()


def _records(session: Session, bid: Bid) -> dict[str, list[Any]]:
    """Every record the submission rests on."""
    from firebid.services import boq, qto

    current = boq.current_boq(session, bid.id)
    lines = boq.lines_of(session, current) if current is not None else []
    items = qto.live_items(session, bid.id)
    rate_ids = {line.rate_id for line in lines if line.rate_id}

    def of(model: Any, *where: Any) -> list[Any]:
        return list(session.execute(select(model).where(model.bid_id == bid.id, *where)).scalars())

    return {
        "qto_item": items,
        "evidence": of(Evidence),
        "boq": [current] if current is not None else [],
        "boq_line": lines,
        "boq_line_source": of(
            BoqLineSource, BoqLineSource.boq_line_id.in_([line.id for line in lines])
        ),
        "rate": list(session.execute(select(Rate).where(Rate.id.in_(rate_ids))).scalars())
        if rate_ids
        else [],
        "quotation": of(Quotation),
        "quotation_line": of(QuotationLine),
        "cost_buildup_line": of(CostBuildupLine, CostBuildupLine.retired_at.is_(None)),
        "bid_price_basis": of(BidPriceBasis),
        "measurement_convention": of(MeasurementConvention),
        "labour_condition": of(LabourCondition),
        "clarification": of(Clarification),
        "clarification_source": of(ClarificationSource),
        "qualification": of(Qualification),
        "risk": of(Risk),
        "scope_check": of(ScopeCheck),
        "approval": of(Approval),
    }


def _identity(row: Any) -> str:
    key = inspect(row).identity
    return "|".join(str(part) for part in key) if key else ""


def _qualifications_workbook(session: Session, bid: Bid) -> bytes:
    from openpyxl import Workbook

    from firebid.services import risk

    book = Workbook()
    sheet = book.active
    assert sheet is not None  # noqa: S101 - a new workbook has one
    sheet.title = "Qualifications"
    sheet.append(["Kind", "Text", "Source", "State", "Decided by"])
    for row in risk.qualifications(session, bid.id):
        if row.state == "rejected":
            continue
        sheet.append(
            [
                row.kind,
                row.text,
                f"{row.source_kind.replace('_', ' ')}: {row.source_label}",
                row.state,
                row.decided_by or "",
            ]
        )
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _files(session: Session, bid: Bid, store: ObjectStore) -> list[tuple[str, bytes, str]]:
    """The files exported with the submission: (name, content, media type). Where the client
    issued a bill of their own, it is among them as it goes back to them: their workbook with
    our rates in its rate cells."""
    from firebid.clarifications import export
    from firebid.services import boq, clarifications, review_pack

    pack = review_pack.build(session, bid)
    files = [
        ("review-pack.xlsx", review_pack.as_workbook(pack), XLSX),
        ("review-pack.pdf", review_pack.as_pdf(pack), "application/pdf"),
        ("qualifications.xlsx", _qualifications_workbook(session, bid), XLSX),
        (
            "clarifications.xlsx",
            export.build(
                "company_default",
                "xlsx",
                clarifications.project_of(session, bid),
                [clarifications._row_of(row) for row in clarifications.register(session, bid.id)],
            ),
            XLSX,
        ),
    ]
    if boq.current_boq(session, bid.id) is not None:
        files.insert(0, ("company-boq.xlsx", boq.company_workbook(session, bid.id), XLSX))
    workbooks = sorted({sheet.document_id for sheet, _ in boq.client_lines(session, bid.id)})
    for number, document_id in enumerate(workbooks, start=1):
        try:
            priced = boq.priced_client_workbook(session, store, bid.id, document_id)
        except boq.BoqError as refusal:
            raise GateError(f"the client's bill could not be priced: {refusal}") from refusal
        name = "client-boq-priced.xlsx" if number == 1 else f"client-boq-priced-{number}.xlsx"
        files.append((name, priced, XLSX))
    return files


def snapshot_of(session: Session, bid_id: uuid.UUID) -> SubmissionSnapshot | None:
    return session.execute(
        select(SubmissionSnapshot).where(SubmissionSnapshot.bid_id == bid_id)
    ).scalar_one_or_none()


def freeze(
    session: Session, bid: Bid, actor: Actor, store: ObjectStore, at: datetime | None = None
) -> SubmissionSnapshot:
    """Write the immutable snapshot: the files, then the manifest that lists them and every
    record behind the submission. Each object is written once."""
    if actor.id is None:
        raise GateError("a submission is frozen by a named person")
    if snapshot_of(session, bid.id) is not None:
        raise GateError("the submission is already frozen")
    snapshot_id = uuid.uuid4()
    prefix = f"snapshots/{bid.id}/{snapshot_id}"
    files = []
    for name, content, media in _files(session, bid, store):
        key = f"{prefix}/{name}"
        store.put_once(key, content, content_type=media)
        files.append(
            {
                "name": name,
                "key": key,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
                "content_type": media,
            }
        )
    records = _records(session, bid)
    manifest = {
        "snapshot_id": str(snapshot_id),
        "bid": {
            "id": str(bid.id),
            "human_id": bid.human_id,
            "client": bid.client_name,
            "tender_reference": bid.tender_reference,
        },
        "frozen_at": (at or datetime.now(UTC)).isoformat(),
        "frozen_by": actor.label,
        "files": files,
        "records": {
            name: sorted(
                (
                    {
                        "id": _identity(row),
                        "version": getattr(row, "version", None),
                        "sha256": record_hash(row),
                    }
                    for row in rows
                ),
                key=lambda item: str(item["id"]),
            )
            for name, rows in records.items()
        },
    }
    body = canonical(manifest)
    manifest_key = f"{prefix}/manifest.json"
    store.put_once(manifest_key, body, content_type="application/json")
    row = SubmissionSnapshot(
        id=snapshot_id,
        bid_id=bid.id,
        manifest_key=manifest_key,
        manifest_sha256=hashlib.sha256(body).hexdigest(),
        files=files,
        record_counts={name: len(rows) for name, rows in records.items()},
        frozen_by=actor.label,
        frozen_by_id=actor.id,
    )
    session.add(row)
    session.flush()
    log.info(
        "submission_frozen",
        bid_id=str(bid.id),
        snapshot_id=str(snapshot_id),
        manifest_sha256=row.manifest_sha256,
        files=len(files),
    )
    return row


@dataclass
class Verification:
    ok: bool
    manifest_sha256: str
    problems: list[str] = field(default_factory=list)
    files_checked: int = 0
    records_listed: int = 0
    # Records changed in the database since the freeze. The snapshot is what was submitted;
    # this says where the live data has moved on from it.
    changed_since: list[str] = field(default_factory=list)


def verify_snapshot(
    session: Session, store: ObjectStore, snapshot: SubmissionSnapshot, bid: Bid | None = None
) -> Verification:
    """Read the manifest and every file back and check each hash. A manifest or file that
    is missing, or whose content is not what was frozen, fails. With `bid`, also lists the
    records the live database no longer holds as they were frozen."""
    found = Verification(ok=True, manifest_sha256=snapshot.manifest_sha256)

    def fail(problem: str) -> None:
        found.ok = False
        found.problems.append(problem)

    try:
        body = store.get(snapshot.manifest_key)
    except Exception:
        fail("the manifest is missing from the snapshot store")
        return found
    if hashlib.sha256(body).hexdigest() != snapshot.manifest_sha256:
        fail("the manifest is not the one that was frozen: its hash differs")
        return found
    manifest: dict[str, Any] = json.loads(body)
    if manifest.get("files") != list(snapshot.files):
        fail("the manifest's file list is not the one recorded when it was frozen")
    for item in manifest.get("files") or []:
        try:
            content = store.get(str(item["key"]))
        except Exception:
            fail(f"{item['name']} is missing from the snapshot store")
            continue
        found.files_checked += 1
        if hashlib.sha256(content).hexdigest() != item["sha256"]:
            fail(f"{item['name']} is not the file that was frozen: its hash differs")
    listed: dict[str, list[dict[str, Any]]] = manifest.get("records") or {}
    found.records_listed = sum(len(rows) for rows in listed.values())
    if bid is not None:
        live = {
            name: {_identity(row): record_hash(row) for row in rows}
            for name, rows in _records(session, bid).items()
        }
        for name, rows in listed.items():
            for item in rows:
                # Approvals gain G4 itself after the freeze; that is not a change to one.
                if live.get(name, {}).get(str(item["id"])) != item["sha256"]:
                    found.changed_since.append(f"{name} {item['id']}")
    return found


def submission_file(
    session: Session, bid: Bid, store: ObjectStore, name: str, actor: Actor
) -> tuple[bytes, str]:
    """A file of the frozen submission, for the person asking. Before G4 there is none."""
    snapshot = snapshot_of(session, bid.id)
    if snapshot is None or approval_of(session, bid.id, "G4") is None:
        raise GateError("submission files are available once G4 is approved")
    item = next((f for f in snapshot.files if f["name"] == name), None)
    if item is None:
        raise GateError(f"the submission has no file {name!r}")
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action="submission: file downloaded",
        entity_type=SubmissionSnapshot.__tablename__,
        entity_id=snapshot.id,
        after={"file": name, "sha256": item["sha256"]},
    )
    return store.get(str(item["key"])), str(item["content_type"])


# --- The outcome (FR-LRN-02) ------------------------------------------------------------------


def outcome_of(session: Session, bid_id: uuid.UUID) -> BidOutcome | None:
    return session.execute(
        select(BidOutcome).where(BidOutcome.bid_id == bid_id)
    ).scalar_one_or_none()


def record_outcome(
    session: Session,
    bid: Bid,
    actor: Actor,
    *,
    outcome: str,
    awarded_price: Decimal | None = None,
    reasons: str = "",
    competitor_feedback: str = "",
) -> BidOutcome:
    """Record how the tender ended, and move the bid's lifecycle to match. Once recorded,
    the reasons and feedback can be added to; the outcome itself stands."""
    from firebid.services.transitions import apply_transition

    if outcome not in OUTCOMES:
        raise GateError("an outcome is awarded, lost or withdrawn")
    if actor.id is None:
        raise GateError("an outcome is recorded by a named person")
    if awarded_price is not None and awarded_price <= 0:
        raise GateError("an awarded price is more than zero")
    if outcome != "awarded" and not reasons.strip():
        raise GateError("say why the tender was lost or withdrawn, as far as is known")
    row = outcome_of(session, bid.id)
    target = {
        "awarded": BidState.AWARDED,
        "lost": BidState.LOST,
        "withdrawn": BidState.WITHDRAWN,
    }[outcome]
    before = None
    if row is None:
        try:
            apply_transition(session, bid, target=target, actor=actor, reason=reasons or None)
        except TransitionError as refusal:
            raise GateError(str(refusal)) from refusal
        row = BidOutcome(bid_id=bid.id, outcome=outcome, recorded_by=actor.label)
        session.add(row)
    else:
        if row.outcome != outcome:
            raise GateError(f"the tender is recorded as {row.outcome}: that stands")
        before = {
            "awarded_price": str(row.awarded_price.amount) if row.awarded_price else None,
            "reasons": row.reasons,
            "competitor_feedback": row.competitor_feedback,
        }
    row.awarded_price = Money.of(awarded_price) if awarded_price is not None else None
    row.reasons = reasons.strip()
    row.competitor_feedback = competitor_feedback.strip()
    row.recorded_by, row.recorded_by_id = actor.label, actor.id
    row.recorded_at = datetime.now(UTC)
    session.flush()
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action="outcome: recorded",
        entity_type=BidOutcome.__tablename__,
        entity_id=row.id,
        before=before,
        after={
            "outcome": outcome,
            "awarded_price": str(row.awarded_price.amount) if row.awarded_price else None,
            "reasons": row.reasons,
            "competitor_feedback": row.competitor_feedback,
        },
    )
    return row


@dataclass
class OutcomeReport:
    submitted: int
    awarded: int
    lost: int
    withdrawn: int
    awaiting: int
    win_rate_percent: float | None
    awarded_value: Decimal
    rows: list[dict[str, Any]]


def _price(row: BidOutcome | None) -> str | None:
    if row is None or row.awarded_price is None:
        return None
    return str(row.awarded_price.amount)


def outcome_report(session: Session, organisation_id: uuid.UUID) -> OutcomeReport:
    """The organisation's tenders by how they ended: what was won, lost and is still out."""
    ended = {
        row.bid_id: row
        for row in session.execute(
            select(BidOutcome)
            .join(Bid, Bid.id == BidOutcome.bid_id)
            .where(Bid.organisation_id == organisation_id)
        ).scalars()
    }
    bids = list(
        session.execute(
            select(Bid).where(
                Bid.organisation_id == organisation_id,
                Bid.state.in_(
                    [
                        str(BidState.SUBMITTED),
                        str(BidState.POST_SUBMISSION_CLARIFICATION),
                        str(BidState.AWARDED),
                        str(BidState.LOST),
                    ]
                )
                | Bid.id.in_(list(ended)),
            )
        ).scalars()
    )
    counts = {name: sum(1 for row in ended.values() if row.outcome == name) for name in OUTCOMES}
    decided = counts["awarded"] + counts["lost"]
    return OutcomeReport(
        submitted=len(bids),
        awarded=counts["awarded"],
        lost=counts["lost"],
        withdrawn=counts["withdrawn"],
        awaiting=sum(1 for bid in bids if bid.id not in ended),
        win_rate_percent=round(100.0 * counts["awarded"] / decided, 1) if decided else None,
        awarded_value=sum(
            (row.awarded_price.amount for row in ended.values() if row.awarded_price), Decimal(0)
        ),
        rows=[
            {
                "bid_id": str(bid.id),
                "human_id": bid.human_id,
                "client": bid.client_name,
                "state": bid.state,
                "outcome": ended[bid.id].outcome if bid.id in ended else None,
                "awarded_price": _price(ended.get(bid.id)),
                "reasons": ended[bid.id].reasons if bid.id in ended else "",
            }
            for bid in sorted(bids, key=lambda bid: bid.human_id)
        ],
    )
