"""The tender clarification register (P2-06; FR-RFI-01, 02, 04, 05, 06, 07).

* **Candidates** are what the platform has flagged that only the client can settle: open
  specification issues, flagged bill variances and scope rows left unclear. Each comes with
  its evidence. A candidate already in a clarification is not offered again.
* **Drafting.** A person picks a candidate, or confirms a proposed group, and a draft is
  composed by rule with every field. A draft with nothing to cite is refused and not saved.
  The model may be asked to improve the wording; it may cite only evidence the draft has.
* **Lifecycle** is the clarification model of requirements §7 (`domain.state_machines`).
  The Bid Manager approves a clarification for issue; one with engineering, fire-safety or
  structural content needs the Design Manager first.
* **The platform sends nothing.** A person downloads the register and sends it, then records
  it as issued. A response is uploaded against its clarification and raises a task to assess
  its impact; the assessment may re-run takeoff and pricing, and records what changed.
* **At submission**, every clarification still unresolved becomes a proposed qualification
  or assumption, linked back to it, for a person to accept or reject.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from firebid.clarifications import drafting, export
from firebid.clarifications.drafting import Candidate, EvidenceRef, SheetRef
from firebid.db.audit import record_event
from firebid.db.models.clarifications import Clarification, ClarificationSource, Qualification
from firebid.db.models.core import Bid, Project
from firebid.db.models.workflow import HumanTask
from firebid.domain.actors import Actor, AuditContext
from firebid.domain.state_machines import ClarificationState, Role, TransitionError
from firebid.ingest.scanning import Scanner
from firebid.storage.object_store import ObjectStore

log = structlog.get_logger("firebid.clarifications")

OPEN_STATES = ("draft", "internal_review", "approved_to_issue", "issued", "responded")
EDITABLE = ("draft", "internal_review")
STATE_WORDS = {
    "draft": "Draft",
    "internal_review": "Internal review",
    "approved_to_issue": "Approved to issue",
    "issued": "Issued",
    "responded": "Responded",
    "closed_incorporated": "Closed: incorporated",
    "closed_no_change": "Closed: no change",
    "converted_to_qualification": "Converted to qualification",
}
IMPACT_TASK = "clarification.impact"


class ClarificationError(ValueError):
    """A clarification request that cannot be done, with the reason a person can act on."""


def _context(bid: Bid) -> AuditContext:
    return AuditContext(organisation_id=bid.organisation_id, bid_id=bid.id)


def label(row: Clarification) -> str:
    return f"TC-{row.number:03d}"


def project_of(session: Session, bid: Bid) -> str:
    project = session.get(Project, bid.project_id)
    return project.name if project is not None else bid.human_id


# --- Candidates -------------------------------------------------------------------------------


def _spec_candidates(session: Session, bid: Bid) -> list[Candidate]:
    from firebid.services import spec_analysis

    found = []
    for issue in spec_analysis.clarification_candidates(session, bid.id):
        spec: dict[str, Any] = dict(issue.spec_ref)
        drawing: dict[str, Any] = dict(issue.drawing_ref)
        detail: dict[str, Any] = dict(issue.detail)
        clause, sheet = spec.get("clause"), drawing.get("sheet_number")
        evidence = []
        if clause:
            evidence.append(
                EvidenceRef(
                    kind="clause",
                    label=f"Specification clause {clause}",
                    quote=str(spec.get("quote") or "")[:2000],
                    link=f"/bids/{bid.id}/spec/clauses/{spec['clause_id']}"
                    if spec.get("clause_id")
                    else None,
                    revision=str(spec["revision"]) if spec.get("revision") else None,
                )
            )
        # The drawing side cites one sheet, or the Current sheets that were looked through.
        cited = [drawing] if sheet else [dict(item) for item in drawing.get("sheets") or []]
        for one in cited[:6]:
            if not one.get("sheet_number"):
                continue
            evidence.append(
                EvidenceRef(
                    kind="sheet",
                    label=f"Drawing {one['sheet_number']}",
                    quote=str(one.get("note") or drawing.get("note") or "")[:2000],
                    link=f"/bids/{bid.id}/sheets/{one['sheet_id']}"
                    if one.get("sheet_id")
                    else None,
                    revision=str(one["revision"]) if one.get("revision") else None,
                )
            )
        if not clause and spec.get("revision"):
            evidence.append(
                EvidenceRef(
                    kind="clause",
                    label="Specification (no clause mentions it)",
                    quote=str(spec.get("quote") or "")[:2000],
                    revision=str(spec["revision"]),
                )
            )
        sheets = tuple(
            SheetRef(
                sheet_number=str(one["sheet_number"]),
                revision=str(one["revision"]) if one.get("revision") else None,
                sheet_id=str(one["sheet_id"]) if one.get("sheet_id") else None,
            )
            for one in cited[:6]
            if one.get("sheet_number")
        )
        only = sheets[0].sheet_number if len(sheets) == 1 else None
        what = str(detail.get("what") or "")
        if issue.category == "conflict":
            specified = str(detail.get("specified") or "").replace("_", " ")
            drawn = str(detail.get("drawn") or "").replace("_", " ")
            problem = (
                f'Specification clause {clause} states: "{spec.get("quote")}" '
                f"Drawing {sheet} revision {drawing.get('revision')} notes: "
                f'"{drawing.get("note")}" The two do not agree on the '
                f"{what.replace('_', ' ')}. Please confirm which governs."
            )
            options: tuple[str, ...] = (
                f"the specification governs ({specified}), and the drawing is to be revised",
                f"the drawing governs ({drawn}), and the specification is to be amended",
            )
            topic = what
        elif issue.category == "missing":
            problem = f"{issue.title}. Please confirm whether it is required and, if so, where."
            options = ()
            topic = (
                "required_item" if issue.rule == "missing_from_drawings" else "item_not_specified"
            )
        else:
            problem = (
                f'{issue.title}. Specification clause {clause} states: "{spec.get("quote")}" '
                "Please confirm what is required, so that it can be priced."
            )
            options = ()
            topic = "ambiguity"
        found.append(
            Candidate(
                kind="missing_information" if issue.category == "missing" else "spec_issue",
                ref=issue.key,
                subject=issue.title[:300],
                problem=problem,
                evidence=tuple(evidence),
                system=issue.system or "",
                level_grid=str(drawing.get("level") or ""),
                sheets=sheets,
                options=options,
                topic=topic,
                group_key=f"{issue.system}|{only}" if issue.system and only else "",
            )
        )
    return found


def _quantity(value: Decimal | None) -> str:
    return format(value.normalize(), "f") if value is not None else "none"


def _variance_candidates(session: Session, bid: Bid) -> list[Candidate]:
    from firebid.services import boq

    found = []
    for row in boq.clarification_candidates(session, bid.id):
        evidence = []
        if row.client_ref:
            evidence.append(
                EvidenceRef(
                    kind="client_boq_line",
                    label=f"Client bill {row.client_ref}",
                    quote=(
                        f"{row.client_item or ''} {row.client_description or ''}: "
                        f"{_quantity(row.client_quantity)} {row.client_unit or ''}"
                    ).strip(),
                )
            )
        for human_id, link in zip(row.qto_items, row.evidence_links, strict=False):
            evidence.append(EvidenceRef(kind="qto_item", label=f"Takeoff {human_id}", link=link))
        described = row.client_description or row.line_description or "the item"
        if row.kind == "mapped":
            subject = f"Quantity of {described}"
            problem = (
                f"The bill gives {_quantity(row.client_quantity)} {row.client_unit or ''} for "
                f'"{described}" ({row.client_ref}). The tender drawings measure '
                f"{_quantity(row.measured_quantity)} {row.unit or ''}, a difference of "
                f"{row.variance_percent}%. Please confirm the quantity to be priced."
            )
            options: tuple[str, ...] = (
                f"the quantity measured from the drawings "
                f"({_quantity(row.measured_quantity)} {row.unit or ''}) is priced",
            )
            cost = (
                f"{_quantity(row.variance)} {row.unit or ''} at the bill's rate"
                if row.variance is not None
                else ""
            )
        elif row.kind == "client_only":
            subject = f"Bill item not found on the drawings: {described}"
            problem = (
                f'The bill lists "{described}" ({row.client_ref}, '
                f"{_quantity(row.client_quantity)} {row.client_unit or ''}). The tender "
                "drawings show nothing it measures. Please confirm what it covers and where."
            )
            options, cost = (), ""
        else:
            subject = f"Measured but not in the bill: {described}"
            problem = (
                f"The tender drawings measure {_quantity(row.measured_quantity)} "
                f'{row.unit or ""} of "{described}", which the bill has no item for. '
                "Please confirm where it is to be priced."
            )
            options, cost = ("an item is added to the bill for it",), ""
        found.append(
            Candidate(
                kind="boq_variance",
                ref=f"client:{row.client_ref}" if row.client_ref else f"line:{row.line_item}",
                subject=subject[:300],
                problem=problem,
                evidence=tuple(evidence),
                options=options,
                cost_impact=cost,
                topic="quantity",
            )
        )
    return found


def _scope_candidates(session: Session, bid: Bid) -> list[Candidate]:
    from firebid.services import spec_analysis

    found = []
    for row in spec_analysis.matrix(session, bid.id):
        if row.status != "unclear":
            continue
        if row.clause_number:
            evidence = EvidenceRef(
                kind="clause",
                label=f"Specification clause {row.clause_number}",
                quote=(row.quote or "")[:2000],
                link=f"/bids/{bid.id}/spec/clauses/{row.clause_id}" if row.clause_id else None,
            )
        else:
            evidence = EvidenceRef(
                kind="scope_row",
                label=f"Scope matrix: {row.label}",
                quote=row.reason[:2000],
                link=f"/bids/{bid.id}/specification",
            )
        found.append(
            Candidate(
                kind="scope_row",
                ref=f"{row.system}:{row.kind}:{row.key}",
                subject=f"Scope: {row.label}"[:300],
                problem=(
                    f"For the {row.system.replace('_', ' ')} installation, the specification "
                    f'does not make clear whose scope "{row.label}" is ({row.reason}). '
                    "Please confirm whether it is in the fire protection contractor's scope."
                ),
                evidence=(evidence,),
                system=row.system,
                options=("it is by others, and is excluded from this offer",),
                topic="scope",
                group_key=f"{row.system}|scope",
            )
        )
    return found


def _used(session: Session, bid_id: uuid.UUID) -> set[tuple[str, str]]:
    return {
        (kind, ref)
        for kind, ref in session.execute(
            select(ClarificationSource.kind, ClarificationSource.ref).where(
                ClarificationSource.bid_id == bid_id
            )
        )
    }


def candidates(session: Session, bid: Bid) -> list[Candidate]:
    """Everything flagged that is not yet in a clarification."""
    used = _used(session, bid.id)
    found = [
        *_spec_candidates(session, bid),
        *_variance_candidates(session, bid),
        *_scope_candidates(session, bid),
    ]
    return [item for item in found if (item.kind, item.ref) not in used]


def proposed_groups(session: Session, bid: Bid) -> list[drafting.Group]:
    return drafting.propose_groups(candidates(session, bid))


# --- Drafting ---------------------------------------------------------------------------------


def due_date(bid: Bid) -> datetime | None:
    """When a clarification must be issued by: so many days before the cut-off."""
    if bid.clarification_cutoff is None:
        return None
    days = int(drafting.settings().get("issue_days_before_cutoff", 0))
    return bid.clarification_cutoff - timedelta(days=days)


def draft(
    session: Session,
    bid: Bid,
    chosen: list[tuple[str, str]],
    actor: Actor,
    *,
    kind: str = "tender_clarification",
) -> Clarification:
    """Draft one clarification from one candidate, or from a group a person confirmed."""
    if kind != "tender_clarification":
        raise ClarificationError(
            "only tender clarifications are raised here: construction RFIs are out of scope"
        )
    available = {(item.kind, item.ref): item for item in candidates(session, bid)}
    picked = []
    for key in dict.fromkeys(chosen):
        if key not in available:
            raise ClarificationError(
                f"{key[1]} is not an open issue, or is already in a clarification"
            )
        picked.append(available[key])
    try:
        composed = drafting.compose(picked, project_of(session, bid))
    except ValidationError as refusal:
        if any(error["loc"][:1] == ("evidence",) for error in refusal.errors()):
            raise ClarificationError(
                "the draft has no evidence reference, so it is not saved: a clarification "
                "cites at least one clause, sheet, bill line or takeoff item"
            ) from refusal
        raise ClarificationError(
            f"the draft is not complete: {refusal.errors()[0]['msg']}"
        ) from refusal
    except ValueError as refusal:
        raise ClarificationError(str(refusal)) from refusal
    number = (
        session.execute(
            select(func.max(Clarification.number)).where(Clarification.bid_id == bid.id)
        ).scalar_one()
        or 0
    ) + 1
    row = Clarification(
        bid_id=bid.id,
        kind=kind,
        number=number,
        state=str(ClarificationState.DRAFT),
        due_at=due_date(bid),
        drafting={"method": "rules", "candidates": len(picked)},
        created_by_id=actor.id,
        **_columns(composed),
    )
    session.add(row)
    session.flush()
    for item in picked:
        session.add(
            ClarificationSource(
                bid_id=bid.id,
                clarification_id=row.id,
                kind=item.kind,
                ref=item.ref,
                snapshot=item.as_json(),
            )
        )
    session.flush()
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action="clarification: drafted",
        entity_type=Clarification.__tablename__,
        entity_id=row.id,
        after={
            "number": label(row),
            "subject": row.subject,
            "from": [f"{item.kind}: {item.ref}" for item in picked],
            "evidence": len(row.evidence),
            "engineering_content": row.engineering_content,
        },
    )
    return row


def _columns(composed: drafting.Draft) -> dict[str, Any]:
    return {
        "subject": composed.subject,
        "project": composed.project,
        "level_grid": composed.level_grid,
        "sheets": [sheet.model_dump() for sheet in composed.sheets],
        "problem": composed.problem,
        "evidence": [item.model_dump() for item in composed.evidence],
        "options": [option.model_dump() for option in composed.options],
        "cost_impact": composed.cost_impact,
        "programme_impact": composed.programme_impact,
        "required_reviewer": composed.required_reviewer,
        "engineering_content": composed.engineering_content,
        "engineering_reason": composed.engineering_reason,
    }


def _as_draft(row: Clarification, changes: dict[str, Any]) -> drafting.Draft:
    """The row with changes applied, checked as a draft is: it must still cite evidence."""
    values: dict[str, Any] = {
        "subject": row.subject,
        "project": row.project,
        "level_grid": row.level_grid,
        "sheets": list(row.sheets or []),
        "problem": row.problem,
        "evidence": list(row.evidence or []),
        "options": list(row.options or []),
        "cost_impact": row.cost_impact,
        "programme_impact": row.programme_impact,
        "required_reviewer": row.required_reviewer,
        "engineering_content": row.engineering_content,
        "engineering_reason": row.engineering_reason,
    } | changes
    if "options" in changes:
        values["options"] = [
            {"text": drafting.as_recommendation(str(text)), "recommendation": True}
            for text in changes["options"]
            if str(text).strip()
        ]
    return drafting.Draft.model_validate(values)


def sources(session: Session, row: Clarification) -> list[ClarificationSource]:
    return list(
        session.execute(
            select(ClarificationSource)
            .where(ClarificationSource.clarification_id == row.id)
            .order_by(ClarificationSource.created_at)
        ).scalars()
    )


def _flag_engineering(session: Session, row: Clarification, composed: drafting.Draft) -> None:
    """Engineering content by subject stays flagged whatever the words become; words that
    name an engineering subject flag it too."""
    snapshots = [dict(source.snapshot) for source in sources(session, row)]
    stand_ins = [
        Candidate(
            kind=str(item.get("kind")),
            ref=str(item.get("ref")),
            subject="",
            problem="",
            evidence=(),
            topic=str(item.get("topic") or ""),
        )
        for item in snapshots
    ]
    words = " ".join(
        [composed.subject, composed.problem, *(option.text for option in composed.options)]
    )
    flagged, why = drafting.engineering(stand_ins, words)
    row.engineering_content = flagged
    row.engineering_reason = why
    row.required_reviewer = "design_manager" if flagged else "bid_manager"
    if not flagged:
        row.qp_input_needed = False


def edit(
    session: Session, bid: Bid, row: Clarification, changes: dict[str, Any], actor: Actor
) -> Clarification:
    """A person's changes to a draft. Evidence can be added to, never emptied."""
    if row.state not in EDITABLE:
        raise ClarificationError(
            f"{label(row)} is {STATE_WORDS[row.state].lower()}: only a draft is edited"
        )
    allowed = {
        "subject",
        "level_grid",
        "problem",
        "options",
        "cost_impact",
        "programme_impact",
        "evidence",
    }
    unknown = set(changes) - allowed
    if unknown:
        raise ClarificationError(f"{', '.join(sorted(unknown))} cannot be changed")
    try:
        composed = _as_draft(row, changes)
    except ValidationError as refusal:
        if any(error["loc"][:1] == ("evidence",) for error in refusal.errors()):
            raise ClarificationError(
                "a clarification cites at least one evidence reference"
            ) from refusal
        raise ClarificationError(str(refusal.errors()[0]["msg"])) from refusal
    before = {name: getattr(row, name) for name in changes}
    for name, value in _columns(composed).items():
        if name in changes:
            setattr(row, name, value)
    was_engineering = row.engineering_content
    _flag_engineering(session, row, composed)
    if row.engineering_content and not was_engineering:
        # New engineering content has not been seen by the Design Manager.
        row.design_approved_by = row.design_approved_by_id = row.design_approved_at = None
    session.flush()
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action="clarification: edited",
        entity_type=Clarification.__tablename__,
        entity_id=row.id,
        before=before,
        after={name: getattr(row, name) for name in changes},
    )
    return row


def draft_with_model(
    session: Session, bid: Bid, row: Clarification, router: Any, actor: Actor
) -> Clarification:
    """Ask the model to word the draft better. It is given the draft and its evidence, and
    must say which evidence it relied on: an answer that cites none is refused by the
    output's own validation and nothing is saved; evidence the draft does not have cannot
    be cited."""
    from firebid.agents.base import AgentInput
    from firebid.agents.clarification_drafter import (
        ClarificationDrafter,
        DraftInput,
        DraftWording,
    )
    from firebid.agents.runtime import Escalated, run_agent_with_result

    if row.state not in EDITABLE:
        raise ClarificationError("only a draft is reworded")
    options = [str(option.get("text")) for option in row.options or []]
    wording_of = {"subject": row.subject, "problem": row.problem, "options": options}
    request = AgentInput(
        bid_id=bid.id,
        idempotency_key=f"clarification:{row.id}:"
        + hashlib.sha256(repr(wording_of).encode()).hexdigest()[:24],
        payload=DraftInput(
            subject=row.subject,
            problem=row.problem,
            options=options,
            evidence=[f"{item.get('label')}: {item.get('quote')}" for item in row.evidence or []],
        ),
        actor_label=actor.label,
    )
    try:
        run, result = run_agent_with_result(session, ClarificationDrafter(router), request)
    except Escalated as refusal:
        raise ClarificationError(
            f"the model's draft was not accepted, and nothing was saved: {refusal}"
        ) from refusal
    if result is None or not isinstance(result.output, DraftWording):
        return row
    answer = result.output
    if any(not 0 <= index < len(row.evidence) for index in answer.evidence):
        raise ClarificationError(
            "the model cited evidence the draft does not have: nothing was saved"
        )
    edit(
        session,
        bid,
        row,
        {"subject": answer.subject, "problem": answer.problem, "options": answer.options},
        actor,
    )
    row.drafting = {
        **dict(row.drafting or {}),
        "method": "model",
        "provider": run.provider,
        "model": run.model,
        "prompt_version": run.prompt_version,
        "agent_run_id": str(run.id),
        "evidence_relied_on": list(answer.evidence),
    }
    session.flush()
    return row


# --- The register and its lifecycle -----------------------------------------------------------


def register(session: Session, bid_id: uuid.UUID) -> list[Clarification]:
    return list(
        session.execute(
            select(Clarification)
            .where(Clarification.bid_id == bid_id)
            .order_by(Clarification.number)
        ).scalars()
    )


def overdue(row: Clarification, now: datetime | None = None) -> bool:
    """Not yet issued, and past the day it was due to be."""
    now = now or datetime.now(UTC)
    return (
        row.due_at is not None
        and row.state in ("draft", "internal_review", "approved_to_issue")
        and row.due_at < now
    )


def transition(
    session: Session, bid: Bid, row: Clarification, target: str, actor: Actor
) -> Clarification:
    """Move a clarification along its lifecycle, or raise saying what stands in the way."""
    try:
        wanted = ClarificationState(target)
    except ValueError as refusal:
        raise ClarificationError(f"{target!r} is not a state of a clarification") from refusal
    if wanted in (
        ClarificationState.RESPONDED,
        ClarificationState.CLOSED_INCORPORATED,
        ClarificationState.CLOSED_NO_CHANGE,
        ClarificationState.CONVERTED_TO_QUALIFICATION,
    ):
        raise ClarificationError(
            "record the response, assess its impact, or prepare the submission: "
            "those set this state"
        )
    if actor.id is None:
        raise ClarificationError("a clarification is moved by a named person")
    _move(session, row, wanted, actor)
    now = datetime.now(UTC)
    if wanted is ClarificationState.APPROVED_TO_ISSUE:
        row.approved_by, row.approved_by_id, row.approved_at = actor.label, actor.id, now
    elif wanted is ClarificationState.ISSUED:
        row.issued_by, row.issued_at = actor.label, now
    elif wanted is ClarificationState.INTERNAL_REVIEW:
        row.approved_by = row.approved_by_id = row.approved_at = None
    session.flush()
    return row


def _move(session: Session, row: Clarification, target: ClarificationState, actor: Actor) -> None:
    from firebid.services.transitions import apply_transition

    try:
        apply_transition(session, row, target=target, actor=actor)
    except TransitionError as refusal:
        raise ClarificationError(str(refusal)) from refusal


def design_approval(
    session: Session,
    bid: Bid,
    row: Clarification,
    actor: Actor,
    *,
    qp_input_needed: bool = False,
    note: str | None = None,
) -> Clarification:
    """The Design Manager approves a clarification's engineering content, and says whether
    the QP's input is needed (FR-RFI-06)."""
    if str(Role.DESIGN_MANAGER) not in actor.roles or actor.id is None:
        raise ClarificationError("engineering content is approved by the Design Manager")
    if row.state != "internal_review":
        raise ClarificationError("a clarification is approved while it is in internal review")
    row.design_approved_by = actor.label
    row.design_approved_by_id = actor.id
    row.design_approved_at = datetime.now(UTC)
    row.design_note = note
    row.qp_input_needed = qp_input_needed
    session.flush()
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action="clarification: Design Manager approval",
        entity_type=Clarification.__tablename__,
        entity_id=row.id,
        after={"number": label(row), "qp_input_needed": qp_input_needed},
        reason=note,
    )
    return row


def record_response(
    session: Session,
    bid: Bid,
    row: Clarification,
    actor: Actor,
    *,
    summary: str,
    payload: bytes | None = None,
    filename: str | None = None,
    store: ObjectStore | None = None,
    scanner: Scanner | None = None,
) -> Clarification:
    """The client's response to an issued clarification: what it says, and the file it came
    in, kept as a tender document. Raises a task to assess its impact."""
    from firebid.services.ingestion import Ingestor

    if row.state != "issued":
        raise ClarificationError("a response is recorded against an issued clarification")
    if not summary.strip():
        raise ClarificationError("say what the response says")
    document_id: uuid.UUID | None = None
    if payload is not None:
        if store is None or scanner is None:
            raise ClarificationError("the response file cannot be stored")
        outcome = Ingestor(
            session, store, scanner, bid_id=bid.id, created_by_id=actor.id, created_by=actor.label
        ).ingest(filename or "response", payload)
        kept = [*outcome.stored, *outcome.duplicates]
        if not kept:
            reasons = [
                reason for _, reason in (*outcome.rejected, *outcome.quarantined, *outcome.held)
            ]
            raise ClarificationError(
                "the response file was not accepted: " + ("; ".join(reasons) or "it was refused")
            )
        document = kept[0]
        if document.doc_type is None:
            document.doc_type = "clarification_response"
        document_id = document.id
    _move(session, row, ClarificationState.RESPONDED, actor)
    row.responded_at = datetime.now(UTC)
    row.response_summary = summary.strip()
    row.response_document_id = document_id
    task = HumanTask(
        bid_id=bid.id,
        kind=IMPACT_TASK,
        title=f"Assess the impact of the response to {label(row)}: {row.subject}"[:300],
        state="open",
        required_role=str(Role.ESTIMATOR),
        due_at=bid.submission_deadline,
        payload={"clarification_id": str(row.id), "number": label(row)},
    )
    session.add(task)
    session.flush()
    row.impact_task_id = task.id
    session.flush()
    return row


def assess_impact(
    session: Session,
    bid: Bid,
    row: Clarification,
    actor: Actor,
    *,
    outcome: str,
    note: str,
    rerun: bool = False,
) -> Clarification:
    """Say what the response changed. With `rerun`, takeoff is recomputed and the bill
    priced again first, and what that changed is recorded with the outcome."""
    if row.state != "responded":
        raise ClarificationError("the impact is assessed once a response is recorded")
    if outcome not in ("incorporated", "no_change"):
        raise ClarificationError("the outcome is 'incorporated' or 'no_change'")
    if not note.strip():
        raise ClarificationError("say what the response changes, or why it changes nothing")
    if actor.id is None:
        raise ClarificationError("the impact is assessed by a named person")
    detail: dict[str, Any] = {"rerun": rerun}
    if rerun:
        from firebid.services import boq, pricing, qto

        takeoff = qto.recompute(session, bid.id)
        detail["takeoff"] = {
            "created": takeoff.created,
            "superseded": takeoff.superseded,
            "unchanged": takeoff.unchanged,
            "g1_reopened_for": len(takeoff.reopened),
        }
        if boq.current_boq(session, bid.id) is not None and pricing.current_rates(
            session, bid.organisation_id
        ):
            priced = pricing.price_boq(session, bid, actor)
            detail["pricing"] = {
                "priced": priced.priced,
                "proposed": priced.proposed,
                "unpriced": priced.unpriced,
            }
    row.impact_outcome = outcome
    row.impact_note = note.strip()
    row.impact_detail = detail
    row.impact_by = actor.label
    row.impact_at = datetime.now(UTC)
    if row.impact_task_id is not None:
        task = session.get(HumanTask, row.impact_task_id)
        if task is not None and task.state in ("open", "in_progress"):
            task.state = "done"
            task.completed_at = row.impact_at
            task.completed_by_id = actor.id
    session.flush()
    _move(
        session,
        row,
        ClarificationState.CLOSED_INCORPORATED
        if outcome == "incorporated"
        else ClarificationState.CLOSED_NO_CHANGE,
        actor,
    )
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action="clarification: impact assessed",
        entity_type=Clarification.__tablename__,
        entity_id=row.id,
        after={"number": label(row), "outcome": outcome, **detail},
        reason=note,
    )
    return row


# --- Export: the only way out -----------------------------------------------------------------


def _row_of(row: Clarification) -> dict[str, str]:
    def when(moment: datetime | None) -> str:
        return moment.date().isoformat() if moment else ""

    return {
        "number": label(row),
        "subject": row.subject,
        "project": row.project,
        "level_grid": row.level_grid,
        "sheets": "; ".join(
            f"{sheet.get('sheet_number')}"
            + (f" rev {sheet.get('revision')}" if sheet.get("revision") else "")
            for sheet in row.sheets or []
        ),
        "problem": row.problem,
        "evidence": "\n".join(
            f"{item.get('label')}"
            + (f" rev {item.get('revision')}" if item.get("revision") else "")
            + (f": {item.get('quote')}" if item.get("quote") else "")
            for item in row.evidence or []
        ),
        "options": "\n".join(str(option.get("text")) for option in row.options or []),
        "cost_impact": row.cost_impact,
        "programme_impact": row.programme_impact,
        "reviewer": row.required_reviewer.replace("_", " "),
        "status": STATE_WORDS[row.state],
        "due": when(row.due_at),
        "issued": when(row.issued_at),
        "response": row.response_summary or "",
    }


def export_register(
    session: Session,
    bid: Bid,
    actor: Actor,
    *,
    template: str = "company_default",
    kind: str = "xlsx",
    only: str | None = None,
) -> bytes:
    """The register as a file for the person asking, in the chosen template. `only` keeps
    one state's clarifications ("approved_to_issue", to send). The download is recorded."""
    rows = [row for row in register(session, bid.id) if only is None or row.state == only]
    try:
        content = export.build(template, kind, project_of(session, bid), [_row_of(r) for r in rows])
    except (KeyError, ValueError) as refusal:
        raise ClarificationError(str(refusal).strip("'\"")) from refusal
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action="clarifications: downloaded",
        entity_type=Clarification.__tablename__,
        entity_id=str(bid.id),
        after={
            "template": template,
            "format": kind,
            "clarifications": [label(row) for row in rows],
        },
    )
    return content


# --- At submission: what is unresolved becomes a qualification --------------------------------


def qualifications(session: Session, bid_id: uuid.UUID) -> list[Qualification]:
    return list(
        session.execute(
            select(Qualification)
            .where(Qualification.bid_id == bid_id)
            .order_by(Qualification.created_at)
        ).scalars()
    )


def _proposed_text(row: Clarification) -> tuple[str, str]:
    """A qualification where the client was asked and has not settled it; an assumption
    where the question was never put."""
    recommended = next((str(o.get("text")) for o in row.options or []), "")
    basis = recommended.removeprefix(drafting.RECOMMENDATION).strip()
    if row.state in ("issued", "responded"):
        return (
            "qualification",
            f"{row.subject}: tender clarification {label(row)} was raised and is not resolved. "
            + (f"Our offer is on the basis that {basis}." if basis else "Our offer excludes it.")
            + " Any change arising from its resolution is to be valued as a variation.",
        )
    return (
        "assumption",
        f"{row.subject}: "
        + (
            f"we have assumed that {basis}."
            if basis
            else "the tender documents do not settle it; our offer is as the drawings show."
        ),
    )


def prepare_submission(session: Session, bid: Bid, actor: Actor) -> list[Qualification]:
    """Every clarification still unresolved becomes a proposed qualification or assumption,
    linked back to it, for a person to review. Only the Bid Manager does this."""
    made = []
    for row in register(session, bid.id):
        if row.state not in OPEN_STATES:
            continue
        kind, words = _proposed_text(row)
        _move(session, row, ClarificationState.CONVERTED_TO_QUALIFICATION, actor)
        qualification = Qualification(
            bid_id=bid.id, kind=kind, text=words, clarification_id=row.id, state="proposed"
        )
        session.add(qualification)
        made.append(qualification)
    session.flush()
    if made:
        record_event(
            session,
            context=_context(bid),
            actor=actor,
            action="submission: unresolved clarifications proposed as qualifications",
            entity_type=Qualification.__tablename__,
            entity_id=str(bid.id),
            after={"proposed": len(made)},
        )
    return made


def decide_qualification(
    session: Session,
    bid: Bid,
    row: Qualification,
    actor: Actor,
    *,
    decision: str,
    text: str | None = None,
    note: str | None = None,
) -> Qualification:
    if decision not in ("accepted", "rejected"):
        raise ClarificationError("a qualification is accepted or rejected")
    if actor.id is None:
        raise ClarificationError("a qualification is decided by a named person")
    before = {"state": row.state, "text": row.text}
    if text is not None and text.strip():
        row.text = text.strip()
    row.state = decision
    row.decided_by, row.decided_by_id = actor.label, actor.id
    row.decided_at = datetime.now(UTC)
    row.note = note
    session.flush()
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action=f"qualification: {decision}",
        entity_type=Qualification.__tablename__,
        entity_id=row.id,
        before=before,
        after={"state": row.state, "text": row.text},
        reason=note,
    )
    return row
