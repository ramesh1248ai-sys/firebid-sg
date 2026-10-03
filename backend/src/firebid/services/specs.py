"""A bid's specifications: clause trees, attributes with citations, and what takeoff reads
(FR-SPEC-01, FR-SPEC-05).

When a document is registered as a specification, `spec.read` (sandbox pool: it opens the
file) builds its clause tree, places its sections by rule, and reads attributes by rule.
What the rules cannot do goes to the model on the ordinary worker: `spec.sections` for
sections the rules could not place, `spec.attributes` for fire protection clauses the rules
read nothing from. Every attribute, however found, has its citation checked against its
clause; a failed check leaves it at a low confidence, flagged, with the reason.

Attributes are proposals until a person confirms, edits or rejects them; each decision is a
new version, audited under the bid. `attributes_for` is what the QTO engine reads: verified
attributes of Current specifications only, "not specified" for anything else.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from firebid.db.audit import record_event
from firebid.db.models.core import Bid
from firebid.db.models.documents import Document, DocumentRevision
from firebid.db.models.specs import SpecAttribute, SpecClause
from firebid.domain.actors import Actor, AuditContext
from firebid.specs import attributes as rules
from firebid.specs import citations, sections
from firebid.specs.clauses import Clause
from firebid.storage.object_store import ObjectStore

log = structlog.get_logger("firebid.specs")

NOT_SPECIFIED = "not specified"
# The attributes the QTO engine asks for, answered "not specified" when none is verified.
QTO_ATTRIBUTES = (
    "pipe_material",
    "pipe_standard",
    "pipe_class",
    "joining_method",
    "sprinkler_type",
    "response",
    "k_factor",
    "temperature_rating_c",
    "finish",
)
SPEC_KINDS = ("docx", "pdf")


class SpecError(ValueError):
    """A decision the attribute table refuses."""


# --- Reading ----------------------------------------------------------------------------------


def revision_of(session: Session, document: Document) -> DocumentRevision | None:
    return session.execute(
        select(DocumentRevision).where(DocumentRevision.document_id == document.id)
    ).scalar_one_or_none()


def queue_reading(session: Session, document: Document) -> None:
    """After registration: read a specification, once (the job skips a revision already read)."""
    if document.doc_type != "specification" or document.kind not in SPEC_KINDS:
        return
    from firebid.jobs.enqueue import enqueue
    from firebid.jobs.tasks import read_specification_job

    enqueue(
        session,
        read_specification_job,
        document_id=str(document.id),
        user_id=str(document.created_by_id) if document.created_by_id else "",
    )


def read_specification(
    session: Session, store: ObjectStore, document: Document, *, user_id: str = ""
) -> DocumentRevision | None:
    """Clause tree, sections and rule attributes for one specification. In the sandbox pool."""
    from firebid.sandbox.runner import run_sandboxed
    from firebid.specs.clauses import parse_json

    revision = revision_of(session, document)
    if revision is None or document.kind not in SPEC_KINDS:
        return None
    already = session.execute(
        select(func.count())
        .select_from(SpecClause)
        .where(SpecClause.document_revision_id == revision.id)
    ).scalar_one()
    if already:
        return revision
    parsed: list[dict[str, Any]] = run_sandboxed(
        parse_json, store.get(document.storage_key), document.kind
    )
    clauses = [_clause_of(item) for item in parsed]
    placed = sections.systems(clauses)
    for clause in clauses:
        session.add(
            SpecClause(
                bid_id=revision.bid_id,
                document_revision_id=revision.id,
                ordinal=clause.ordinal,
                number=clause.number,
                heading=clause.heading,
                text=clause.text,
                level=clause.level,
                parent=clause.parent,
                anchor=clause.anchor,
                system=placed[clause.number],
                system_source="rule",
            )
        )
    session.flush()
    found = rules.extract(clauses, placed)
    _store_all(session, revision, clauses, found, {"rule_version": rules.RULES_VERSION})
    _queue_model(session, revision, clauses, placed, found, user_id)
    # What the specification obliges beyond what is installed (FR-SPEC-02, P2-03).
    from firebid.services import spec_analysis

    spec_analysis.read_obligations(session, revision)
    log.info(
        "specification_read",
        revision_id=str(revision.id),
        clauses=len(clauses),
        attributes=len(found),
    )
    return revision


def _clause_of(item: dict[str, Any]) -> Clause:
    return Clause(
        number=str(item["number"]),
        heading=str(item["heading"]),
        text=str(item["text"]),
        ordinal=int(item["ordinal"]),
        anchor=dict(item.get("anchor") or {}),
    )


def clauses_of(session: Session, revision_id: uuid.UUID) -> list[Clause]:
    rows = session.execute(
        select(SpecClause)
        .where(SpecClause.document_revision_id == revision_id)
        .order_by(SpecClause.ordinal)
    ).scalars()
    return [Clause(r.number, r.heading, r.text, r.ordinal, dict(r.anchor)) for r in rows]


def systems_of(session: Session, revision_id: uuid.UUID) -> dict[str, str]:
    return {
        row.number: row.system
        for row in session.execute(
            select(SpecClause).where(SpecClause.document_revision_id == revision_id)
        ).scalars()
    }


def _queue_model(
    session: Session,
    revision: DocumentRevision,
    clauses: list[Clause],
    placed: dict[str, str],
    found: list[rules.Extracted],
    user_id: str,
) -> None:
    from firebid.jobs.enqueue import enqueue
    from firebid.jobs.tasks import find_spec_sections_job, read_spec_attributes_job

    if sections.unknown_sections(clauses):
        enqueue(session, find_spec_sections_job, revision_id=str(revision.id), user_id=user_id)
    left: dict[str, list[str]] = {}
    for clause in rules.unread(clauses, placed, found):
        left.setdefault(placed[clause.number], []).append(clause.number)
    for system, numbers in left.items():
        enqueue(
            session,
            read_spec_attributes_job,
            revision_id=str(revision.id),
            system=system,
            clause_numbers=numbers,
            user_id=user_id,
        )


def _store_all(
    session: Session,
    revision: DocumentRevision,
    clauses: list[Clause],
    found: list[rules.Extracted],
    provenance: dict[str, Any],
) -> list[SpecAttribute]:
    ids = {
        row.number: row.id
        for row in session.execute(
            select(SpecClause).where(SpecClause.document_revision_id == revision.id)
        ).scalars()
    }
    stored = []
    for item in found:
        checked = citations.check(clauses, item.clause, item.value)
        row = SpecAttribute(
            created_at=datetime.now(UTC),
            bid_id=revision.bid_id,
            lineage_id=uuid.uuid4(),
            version=1,
            document_revision_id=revision.id,
            clause_id=ids.get(item.clause),
            clause_number=item.clause,
            system=item.system,
            attribute=item.attribute,
            value=item.value[:200],
            dn_min=item.dn_min,
            dn_max=item.dn_max,
            condition=item.condition,
            quote=item.quote,
            method=item.method,
            confidence=round(citations.adjusted(item.confidence, checked), 4),
            citation_ok=checked.ok,
            citation_reason=checked.reason,
            state="proposed",
            provenance=dict(provenance),
        )
        session.add(row)
        stored.append(row)
    session.flush()
    return stored


# --- The model ----------------------------------------------------------------------------


def find_sections_with_model(
    session: Session, revision_id: uuid.UUID, router: Any, *, user_id: str = ""
) -> int:
    """Place the sections the rules could not, then read them by rule and queue the rest."""
    from firebid.agents.base import AgentInput
    from firebid.agents.runtime import Escalated, run_agent_with_result
    from firebid.agents.spec_reader import SectionAnswers, SectionsInput, SpecSectionFinder

    revision = session.get(DocumentRevision, revision_id)
    if revision is None:
        return 0
    clauses = clauses_of(session, revision_id)
    placed = systems_of(session, revision_id)
    unknown = [c for c in clauses if c.level == 1 and placed.get(c.number) == "unknown"]
    if not unknown:
        return 0
    request = AgentInput(
        bid_id=revision.bid_id,
        idempotency_key=f"spec_sections:{revision_id}:{len(clauses)}",
        payload=SectionsInput(headings=[(c.number, c.heading) for c in unknown]),
    )
    try:
        _, result = run_agent_with_result(session, SpecSectionFinder(router), request)
    except Escalated:
        return 0  # a person places them from the escalation
    if result is None or not isinstance(result.output, SectionAnswers):
        return 0
    decided = {answer.number: answer.system for answer in result.output.sections}
    rows = session.execute(
        select(SpecClause).where(SpecClause.document_revision_id == revision_id)
    ).scalars()
    for row in rows:
        top = row.number.split(".", 1)[0]
        if top in decided and row.system == "unknown":
            row.system, row.system_source = decided[top], "model"
    session.flush()
    placed = systems_of(session, revision_id)
    newly = [c for c in clauses if c.number.split(".", 1)[0] in decided]
    found = rules.extract(newly, placed)
    _store_all(session, revision, clauses, found, {"rule_version": rules.RULES_VERSION})
    _queue_model(session, revision, newly, placed, found, user_id)
    return len(decided)


def read_attributes_with_model(
    session: Session,
    revision_id: uuid.UUID,
    system: str,
    clause_numbers: list[str],
    router: Any,
) -> list[SpecAttribute]:
    """Ask the model about clauses the rules read nothing from; check what it cites."""
    from firebid.agents.base import AgentInput
    from firebid.agents.runtime import Escalated, run_agent_with_result
    from firebid.agents.spec_reader import SpecAttributeExtractor, SpecAttributes, SpecInput

    revision = session.get(DocumentRevision, revision_id)
    if revision is None:
        return []
    clauses = clauses_of(session, revision_id)
    request = AgentInput(
        bid_id=revision.bid_id,
        idempotency_key=f"spec_attributes:{revision_id}:{system}:{','.join(clause_numbers)}",
        payload=SpecInput(
            clauses=[(c.number, c.heading, c.text) for c in clauses],
            system=system,
            clause_numbers=clause_numbers,
        ),
    )
    try:
        run, result = run_agent_with_result(session, SpecAttributeExtractor(router), request)
    except Escalated:
        return []
    if result is None or not isinstance(result.output, SpecAttributes):
        return []
    found = [
        rules.Extracted(
            system=system,
            attribute=item.attribute,
            value=item.value,
            clause=item.clause,
            quote=item.quote,
            dn_min=item.dn_min,
            dn_max=item.dn_max,
            condition=item.condition,
            method="model",
            confidence=item.confidence,
        )
        for item in result.output.attributes
    ]
    return _store_all(
        session,
        revision,
        clauses,
        found,
        {
            "provider": run.provider,
            "model": run.model,
            "prompt_version": run.prompt_version,
            "agent_run_id": str(run.id),
        },
    )


# --- Decisions ------------------------------------------------------------------------------


def current(session: Session, lineage_id: uuid.UUID) -> SpecAttribute | None:
    return (
        session.execute(
            select(SpecAttribute)
            .where(SpecAttribute.lineage_id == lineage_id)
            .order_by(SpecAttribute.version.desc())
        )
        .scalars()
        .first()
    )


def history(session: Session, lineage_id: uuid.UUID) -> list[SpecAttribute]:
    return list(
        session.execute(
            select(SpecAttribute)
            .where(SpecAttribute.lineage_id == lineage_id)
            .order_by(SpecAttribute.version)
        ).scalars()
    )


def _next(previous: SpecAttribute, **changes: Any) -> SpecAttribute:
    names = (
        "bid_id",
        "lineage_id",
        "document_revision_id",
        "clause_id",
        "clause_number",
        "system",
        "attribute",
        "value",
        "dn_min",
        "dn_max",
        "condition",
        "quote",
        "method",
        "confidence",
        "citation_ok",
        "citation_reason",
        "state",
        "provenance",
        "verified_by",
        "verified_by_id",
        "verified_at",
    )
    values = {name: getattr(previous, name) for name in names}
    values.update(version=previous.version + 1, supersedes_id=previous.id, **changes)
    return SpecAttribute(created_at=datetime.now(UTC), **values)


def decide(
    session: Session,
    lineage_id: uuid.UUID,
    actor: Actor,
    *,
    verdict: str,
    value: str | None = None,
    dn_min: int | None = None,
    dn_max: int | None = None,
    condition: str | None = None,
    clause_number: str | None = None,
    note: str | None = None,
) -> SpecAttribute:
    """Confirm (as is), edit (with new value, range or clause) or reject an attribute.

    An edit is checked against its clause like any other answer; a person may confirm a
    value whose citation check failed, and the failure stays on record.
    """
    previous = current(session, lineage_id)
    if previous is None:
        raise SpecError("no such attribute")
    if verdict not in ("confirm", "edit", "reject"):
        raise SpecError(f"unknown decision {verdict!r}")
    now = datetime.now(UTC)
    if verdict == "reject":
        row = _next(
            previous,
            state="rejected",
            change_note=note,
            created_by_id=actor.id,
            verified_by=None,
            verified_by_id=None,
            verified_at=None,
        )
    else:
        changes: dict[str, Any] = {}
        if verdict == "edit":
            new_clause = clause_number or previous.clause_number
            new_value = value if value is not None else previous.value
            clauses = clauses_of(session, previous.document_revision_id)
            checked = citations.check(clauses, new_clause, new_value)
            changes = {
                "value": new_value[:200],
                "dn_min": dn_min,
                "dn_max": dn_max,
                "condition": condition,
                "clause_number": new_clause,
                "method": "person",
                "citation_ok": checked.ok,
                "citation_reason": checked.reason,
            }
        row = _next(
            previous,
            state="verified",
            verified_by=actor.label[:200],
            verified_by_id=actor.id,
            verified_at=now,
            change_note=note,
            created_by_id=actor.id,
            **changes,
        )
    session.add(row)
    session.flush()
    action = {"confirm": "confirmed", "edit": "edited", "reject": "rejected"}[verdict]
    _audit(session, actor, f"spec attribute: {action}", previous, row, note)
    return row


def _audit(
    session: Session,
    actor: Actor,
    action: str,
    before: SpecAttribute,
    after: SpecAttribute,
    note: str | None,
) -> None:
    def state(row: SpecAttribute) -> dict[str, Any]:
        return {
            "version": row.version,
            "state": row.state,
            "system": row.system,
            "attribute": row.attribute,
            "value": row.value,
            "dn_min": row.dn_min,
            "dn_max": row.dn_max,
            "clause": row.clause_number,
        }

    organisation_id = session.execute(
        select(Bid.organisation_id).where(Bid.id == after.bid_id)
    ).scalar_one()
    record_event(
        session,
        context=AuditContext(organisation_id=organisation_id, bid_id=after.bid_id),
        actor=actor,
        action=action,
        entity_type=SpecAttribute.__tablename__,
        entity_id=after.id,
        before=state(before),
        after=state(after),
        reason=note,
    )


# --- What takeoff reads -------------------------------------------------------------------


@dataclass(frozen=True)
class Citation:
    document_id: uuid.UUID
    document_revision_id: uuid.UUID
    revision_label: str | None
    title: str | None
    clause: str
    anchor: dict[str, int]
    quote: str


@dataclass
class Answer:
    attribute: str
    values: list[str] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    # Each verified attribute as it stands: its value and the clause it cites.
    rows: list[tuple[str, Citation]] = field(default_factory=list)

    @property
    def value(self) -> str:
        return ", ".join(self.values) if self.values else NOT_SPECIFIED


def current_attributes(session: Session, bid_id: uuid.UUID) -> list[SpecAttribute]:
    """The latest version of every attribute lineage on the bid."""
    newest = (
        select(SpecAttribute.lineage_id, func.max(SpecAttribute.version).label("version"))
        .where(SpecAttribute.bid_id == bid_id)
        .group_by(SpecAttribute.lineage_id)
        .subquery()
    )
    return list(
        session.execute(
            select(SpecAttribute).join(
                newest,
                (SpecAttribute.lineage_id == newest.c.lineage_id)
                & (SpecAttribute.version == newest.c.version),
            )
        ).scalars()
    )


def attributes_for(
    session: Session,
    bid_id: uuid.UUID,
    system: str,
    dn: int | None = None,
    *,
    condition: str | None = None,
) -> dict[str, Answer]:
    """What the specification says for one system and size, as takeoff may use it.

    Verified attributes only, from Current specification revisions only. An attribute
    limited to a place (a car park) applies only when that place is asked for. Anything
    else is "not specified": never a guess, never an unconfirmed proposal.
    """
    current_revisions = {
        row.id: row
        for row in session.execute(
            select(DocumentRevision).where(
                DocumentRevision.bid_id == bid_id, DocumentRevision.state == "current"
            )
        ).scalars()
    }
    anchors = {
        row.id: dict(row.anchor)
        for row in session.execute(select(SpecClause).where(SpecClause.bid_id == bid_id)).scalars()
    }
    answers = {name: Answer(name) for name in QTO_ATTRIBUTES}
    for row in current_attributes(session, bid_id):
        if row.state != "verified" or row.system != system:
            continue
        revision = current_revisions.get(row.document_revision_id)
        if revision is None:
            continue
        if row.condition and row.condition != condition:
            continue
        if dn is not None and (
            (row.dn_min is not None and dn < row.dn_min)
            or (row.dn_max is not None and dn > row.dn_max)
        ):
            continue
        answer = answers.setdefault(row.attribute, Answer(row.attribute))
        if row.value not in answer.values:
            answer.values.append(row.value)
        cited = Citation(
            document_id=revision.document_id,
            document_revision_id=revision.id,
            revision_label=revision.revision_label,
            title=revision.title,
            clause=row.clause_number,
            anchor=anchors.get(row.clause_id, {}) if row.clause_id else {},
            quote=row.quote,
        )
        answer.citations.append(cited)
        answer.rows.append((row.value, cited))
    return answers
