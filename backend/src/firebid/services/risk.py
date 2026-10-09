"""Bid risk and qualifications (P2-07; FR-RSK-01 to 06).

* **The scope-gap checklist** is built for every system the bid has, each item with the
  status the scope matrix and the takeoff propose. A person resolves each one as included,
  excluded, by others or clarified. Rebuilding refreshes the proposals and keeps what a
  person decided.
* **Risks** are found by rule, each with its evidence: what the specification puts on the
  contractor by way of design, and the execution conditions the drawings, the bid's
  parameters and the documents' words show. A risk no longer found is marked so, not
  deleted. A person gives each risk a treatment (price, qualify, clarify, accept), an owner
  and a status.
* **Impact.** An execution risk a labour multiplier describes is valued with the labour
  estimate. The estimator accepts the figure, or adjusts it and says why.
* **Qualifications** are proposed from risks, the checklist, the scope matrix and the
  measurement conventions; those from unresolved clarifications come from P2-06. Each
  links to its source, is edited with a history, and is accepted or rejected by a person.
* **G3 readiness:** every checklist item resolved and every risk treated. The bid cannot be
  approved at G3 before then.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.db.audit import record_event
from firebid.db.models.audit import AuditEvent
from firebid.db.models.clarifications import QUALIFICATION_KINDS, Qualification
from firebid.db.models.core import Bid
from firebid.db.models.risk import Risk, ScopeCheck
from firebid.db.models.takeoff import BidParameter
from firebid.domain.actors import Actor, AuditContext
from firebid.domain.values import Money
from firebid.labour import multipliers
from firebid.risk import impact as impacts
from firebid.risk import rules

SOURCE_KINDS = ("clarification", "risk", "scope_check", "scope_row", "measurement_convention")


class RiskError(ValueError):
    """A risk request that cannot be done, with the reason a person can act on."""


def _context(bid: Bid) -> AuditContext:
    return AuditContext(organisation_id=bid.organisation_id, bid_id=bid.id)


# --- The scope-gap checklist (FR-RSK-01) ------------------------------------------------------


def checks(session: Session, bid_id: uuid.UUID) -> list[ScopeCheck]:
    order = {str(item["key"]): index for index, item in enumerate(rules.settings()["checklist"])}
    return sorted(
        session.execute(select(ScopeCheck).where(ScopeCheck.bid_id == bid_id)).scalars(),
        key=lambda row: (row.system, order.get(row.item_key, len(order))),
    )


def _scope(session: Session, bid_id: uuid.UUID) -> list[rules.ScopeFact]:
    from firebid.services import spec_analysis

    return [
        rules.ScopeFact(
            system=row.system,
            kind=row.kind,
            key=row.key,
            status=row.status,
            clause=row.clause_number,
            quote=row.quote,
        )
        for row in spec_analysis.matrix(session, bid_id)
    ]


def _takeoff(session: Session, bid_id: uuid.UUID) -> dict[str, Decimal]:
    from firebid.services import qto

    counted: dict[str, Decimal] = {}
    for item in qto.live_items(session, bid_id):
        if item.state == "rejected":
            continue
        counted[item.item_type] = counted.get(item.item_type, Decimal(0)) + item.net_quantity
    return counted


def build_checklist(session: Session, bid: Bid, actor: Actor) -> list[ScopeCheck]:
    """Build the checklist, or refresh what the rules propose. A status a person set stays."""
    scope = _scope(session, bid.id)
    systems = sorted({row.system for row in scope})
    if not systems:
        raise RiskError(
            "the scope matrix is empty: read the specification and run its analysis first"
        )
    found = rules.checklist(systems, scope, _takeoff(session, bid.id))
    held = {(row.system, row.item_key): row for row in checks(session, bid.id)}
    made = 0
    for check in found:
        row = held.get((check.system, check.key))
        if row is None:
            session.add(
                ScopeCheck(
                    bid_id=bid.id,
                    system=check.system,
                    item_key=check.key,
                    label=check.label,
                    proposed_status=check.proposed,
                    status=check.proposed,
                    basis=check.basis,
                    evidence=list(check.evidence),
                )
            )
            made += 1
            continue
        row.label = check.label
        row.proposed_status = check.proposed
        row.basis = check.basis
        row.evidence = list(check.evidence)
        if row.decided_by is None:
            row.status = check.proposed
    session.flush()
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action="scope checklist: built",
        entity_type=ScopeCheck.__tablename__,
        entity_id=str(bid.id),
        after={"items": len(found), "new": made, "systems": systems},
    )
    return checks(session, bid.id)


def resolve_check(
    session: Session, bid: Bid, row: ScopeCheck, actor: Actor, *, status: str, note: str | None
) -> ScopeCheck:
    """A person resolves a checklist item, or confirms what was proposed."""
    if status not in rules.RESOLVED:
        raise RiskError("a checklist item is included, excluded, by others or clarified")
    if actor.id is None:
        raise RiskError("a checklist item is resolved by a named person")
    if status != row.proposed_status and not (note or "").strip():
        raise RiskError("say why, when the status is not the one proposed")
    before = {"status": row.status}
    row.status = status
    row.decided_by, row.decided_by_id = actor.label, actor.id
    row.decided_at = datetime.now(UTC)
    row.note = note
    session.flush()
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action="scope checklist: item resolved",
        entity_type=ScopeCheck.__tablename__,
        entity_id=row.id,
        before=before,
        after={"system": row.system, "item": row.item_key, "status": status},
        reason=note,
    )
    return row


# --- Finding risks (FR-RSK-02, 03) ------------------------------------------------------------


def risks(session: Session, bid_id: uuid.UUID, *, live: bool = True) -> list[Risk]:
    rows = session.execute(select(Risk).where(Risk.bid_id == bid_id)).scalars()
    return sorted(
        (row for row in rows if not live or row.status != "no_longer_found"),
        key=lambda row: (row.category, row.kind, row.level or ""),
    )


def _parameters(
    session: Session, bid_id: uuid.UUID, name: str
) -> dict[str | None, tuple[Decimal, str]]:
    rows = session.execute(
        select(BidParameter)
        .where(BidParameter.bid_id == bid_id, BidParameter.name == name)
        .order_by(BidParameter.created_at, BidParameter.id)
    ).scalars()
    return {row.level: (row.value, row.source) for row in rows}


def _clauses(session: Session, bid_id: uuid.UUID) -> list[tuple[str, str, str]]:
    from firebid.services import spec_analysis, specs

    found: list[tuple[str, str, str]] = []
    for revision in spec_analysis.current_specifications(session, bid_id):
        found.extend((c.number, c.heading, c.text) for c in specs.clauses_of(session, revision.id))
    return found


def _drawing_words(session: Session, bid_id: uuid.UUID) -> tuple[list[rules.Words], list[str]]:
    """The notes on Current sheets, and the levels those sheets and the takeoff show."""
    from firebid.services import qto

    sheets = qto.current_sheets(session, bid_id)
    words = [
        rules.Words(
            kind="sheet",
            label=f"Drawing {sheets[sheet_id].number} "
            f"rev {sheets[sheet_id].revision.revision_label}",
            text=str(span["text"]),
            level=sheets[sheet_id].level,
        )
        for sheet_id, spans in qto.sheet_texts(session, bid_id, sheets).items()
        for span in spans
    ]
    levels = {info.level for info in sheets.values() if info.level}
    levels |= {item.level for item in qto.live_items(session, bid_id) if item.level}
    return words, sorted(levels)


def find(session: Session, bid: Bid, actor: Actor) -> list[Risk]:
    """Find the bid's risks again. What a person decided about a risk is kept; a risk the
    rules no longer find is marked so."""
    clauses = _clauses(session, bid.id)
    notes, levels = _drawing_words(session, bid.id)
    words = [
        *(
            rules.Words("clause", f"Specification clause {number}", text)
            for number, _, text in clauses
            if text
        ),
        *notes,
    ]
    found = [
        *rules.design_risks(clauses),
        *rules.execution_risks(
            levels,
            _parameters(session, bid.id, "ceiling_height_mm"),
            _parameters(session, bid.id, "levels_served").get(None),
            words,
        ),
    ]
    now = datetime.now(UTC)
    held = {row.key: row for row in risks(session, bid.id, live=False)}
    new = 0
    for finding in found:
        row = held.pop(finding.key, None)
        if row is None:
            row = Risk(
                bid_id=bid.id,
                key=finding.key,
                category=finding.category,
                kind=finding.kind,
                proposed_treatment=finding.treatment,
                status="open",
                impact_state="computed",
                rules_version=rules.RULES_VERSION,
                title=finding.title,
                description=finding.description,
                evidence=list(finding.evidence),
            )
            session.add(row)
            new += 1
        elif row.status == "no_longer_found":
            row.status = "treated" if row.treatment else "open"
        row.title = finding.title
        row.description = finding.description
        row.evidence = list(finding.evidence)
        row.level = finding.level
        row.multiplier = finding.multiplier
        row.detail = dict(finding.detail)
        row.rules_version = rules.RULES_VERSION
        row.last_found_at = now
    gone = [row for row in held.values() if row.status != "no_longer_found"]
    for row in gone:
        row.status = "no_longer_found"
    session.flush()
    live = risks(session, bid.id)
    for row in live:
        if row.impact_state == "computed":
            _compute(session, bid, row)
    session.flush()
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action="risks: found",
        entity_type=Risk.__tablename__,
        entity_id=str(bid.id),
        after={"risks": len(live), "new": new, "no_longer_found": len(gone)},
    )
    return live


# --- Impact (FR-RSK-04) -----------------------------------------------------------------------


def _multiplier_of(row: Risk) -> multipliers.Multiplier | None:
    catalogue = multipliers.catalogue()
    if row.multiplier == "height":
        height = row.detail.get("height_mm")
        return multipliers.height_band(Decimal(str(height)), catalogue) if height else None
    return next((item for item in catalogue if item.key == row.multiplier), None)


def computed_impact(
    session: Session, bid: Bid, row: Risk, today: date | None = None
) -> impacts.Impact:
    """What the engines make of a risk now: the labour its multiplier would add."""
    from firebid.services import labour

    if row.category == "design_responsibility":
        return impacts.not_computed(
            "design work is not priced by an engine: the estimator enters an allowance, "
            "from a consultant's fee quotation or the company's own rate"
        )
    item = _multiplier_of(row)
    if item is None:
        return impacts.not_computed("no labour multiplier describes this condition")
    levels = [str(level) for level in row.detail.get("levels") or []] or (
        [row.level] if row.level else []
    )
    estimate = labour.estimate(session, bid, today)
    # What the risk reaches of each line: (the line, its baseline hours there, whether the
    # multiplier is already on them). A line rolled up over the building is reached for the
    # part of it that is on the risk's levels.
    reached: list[tuple[Any, Decimal, bool]] = []
    for line in estimate.lines:
        if line.baseline_hours is None:
            continue
        if levels and line.portions:
            parts = [part for part in line.portions if part.level in levels]
            if parts:
                reached.append(
                    (
                        line,
                        sum((part.baseline_hours for part in parts), Decimal(0)),
                        all(any(m.key == item.key for m in part.multipliers) for part in parts),
                    )
                )
        elif not levels or line.line.level in levels:
            reached.append(
                (line, line.baseline_hours, any(m.key == item.key for m in line.multipliers))
            )
    hours = sum((there for _, there, _ in reached), Decimal(0))
    cost = sum((there * line.rate.hourly for line, there, _ in reached if line.rate), Decimal(0))
    applied = bool(reached) and all(carried for _, _, carried in reached)
    return impacts.labour_impact(
        impacts.LabourReach(len(reached), hours, cost),
        item.key,
        item.label,
        item.value,
        already_applied=applied,
        scope=", ".join(levels) if levels else "the whole bid",
    )


def _compute(session: Session, bid: Bid, row: Risk) -> None:
    found = computed_impact(session, bid, row)
    row.impact = found.as_json()
    row.cost_allowance = Money.of(found.cost) if found.cost is not None else None
    row.programme_hours = found.hours


def decide_impact(
    session: Session,
    bid: Bid,
    row: Risk,
    actor: Actor,
    *,
    cost: Decimal | None = None,
    hours: Decimal | None = None,
    reason: str | None = None,
    accept_computed: bool = False,
) -> Risk:
    """The estimator accepts the computed impact, or sets their own figures and says why."""
    if actor.id is None:
        raise RiskError("an impact is accepted by a named person")
    before = {
        "state": row.impact_state,
        "cost": str(row.cost_allowance.amount) if row.cost_allowance is not None else None,
        "hours": str(row.programme_hours) if row.programme_hours is not None else None,
    }
    if accept_computed:
        _compute(session, bid, row)
        if row.impact.get("method") == "not_computed":
            raise RiskError(
                "nothing is computed for this risk: enter an allowance, or none, with a reason"
            )
        row.impact_state = "accepted"
        row.impact_reason = reason
    else:
        if not (reason or "").strip():
            raise RiskError("say why the impact is what you set it to")
        if cost is not None and cost < 0:
            raise RiskError("an allowance is not negative")
        if hours is not None and hours < 0:
            raise RiskError("programme hours are not negative")
        row.cost_allowance = Money.of(cost) if cost is not None else None
        row.programme_hours = hours
        row.impact_state = "adjusted"
        row.impact_reason = (reason or "").strip()
    row.impact_by = actor.label
    row.impact_at = datetime.now(UTC)
    session.flush()
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action=f"risk: impact {row.impact_state}",
        entity_type=Risk.__tablename__,
        entity_id=row.id,
        before=before,
        after={
            "state": row.impact_state,
            "cost": str(row.cost_allowance.amount) if row.cost_allowance is not None else None,
            "hours": str(row.programme_hours) if row.programme_hours is not None else None,
            "computed": row.impact,
        },
        reason=row.impact_reason,
    )
    return row


# --- The register (FR-RSK-06) -----------------------------------------------------------------


def treat(
    session: Session,
    bid: Bid,
    row: Risk,
    actor: Actor,
    *,
    treatment: str | None = None,
    owner: str | None = None,
    owner_id: uuid.UUID | None = None,
    status: str | None = None,
    note: str | None = None,
) -> Risk:
    """Set a risk's treatment, owner or status. Every change is in the audit trail."""
    if actor.id is None:
        raise RiskError("a risk is treated by a named person")
    before = {"treatment": row.treatment, "owner": row.owner, "status": row.status}
    if treatment is not None:
        if treatment not in rules.TREATMENTS:
            raise RiskError("a treatment is price, qualify, clarify or accept")
        row.treatment = treatment
        if row.status == "open":
            row.status = "treated"
    if owner is not None:
        row.owner = owner.strip() or None
        row.owner_id = owner_id if row.owner else None
    if status is not None:
        if status not in ("open", "treated", "closed"):
            raise RiskError("a risk is open, treated or closed")
        if status != "open" and row.treatment is None:
            raise RiskError("give the risk a treatment before it is treated or closed")
        row.status = status
    if note is not None:
        row.note = note
    session.flush()
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action="risk: register updated",
        entity_type=Risk.__tablename__,
        entity_id=row.id,
        before=before,
        after={"treatment": row.treatment, "owner": row.owner, "status": row.status},
        reason=note,
    )
    return row


def history(session: Session, entity_type: str, entity_id: uuid.UUID) -> list[AuditEvent]:
    """What happened to a risk or a qualification, oldest first."""
    return list(
        session.execute(
            select(AuditEvent)
            .where(AuditEvent.entity_type == entity_type, AuditEvent.entity_id == str(entity_id))
            .order_by(AuditEvent.occurred_at, AuditEvent.id)
        ).scalars()
    )


# --- Qualifications (FR-RSK-05) ---------------------------------------------------------------


@dataclass(frozen=True)
class Proposed:
    kind: str
    text: str
    source_kind: str
    source_ref: str
    source_label: str


def _from_risks(session: Session, bid: Bid) -> list[Proposed]:
    found = []
    for row in risks(session, bid.id):
        cited = ", ".join(dict.fromkeys(str(item.get("label")) for item in row.evidence))
        if row.treatment == "qualify":
            found.append(
                Proposed(
                    "qualification",
                    f"{row.title}. {row.description} Our offer makes no allowance for it "
                    f"({cited}).",
                    "risk",
                    str(row.id),
                    row.title,
                )
            )
        elif row.treatment == "price" and row.cost_allowance is not None:
            found.append(
                Proposed(
                    "assumption",
                    f"{row.title}: we have allowed for it as the documents state ({cited}). "
                    "A change to these conditions is to be valued as a variation.",
                    "risk",
                    str(row.id),
                    row.title,
                )
            )
    return found


def _from_scope(session: Session, bid: Bid) -> list[Proposed]:
    from firebid.services import spec_analysis

    found = []
    covered = set()
    interfaces = {
        str(item["scope_interface"])
        for item in rules.settings()["checklist"]
        if item.get("scope_interface")
    }
    for check in checks(session, bid.id):
        if check.status not in ("excluded", "by_others"):
            continue
        covered.add((check.system, check.item_key))
        system = check.system.replace("_", " ")
        found.append(
            Proposed(
                "exclusion",
                f"{check.label} ({system}) "
                + (
                    "is excluded from our offer."
                    if check.status == "excluded"
                    else "is by others and is not included in our offer."
                ),
                "scope_check",
                str(check.id),
                f"{check.label} ({system})",
            )
        )
    for row in spec_analysis.matrix(session, bid.id):
        if row.kind != "interface" or row.status not in ("excluded", "by_others"):
            continue
        if row.key in interfaces:
            continue  # the checklist item for this interface says it
        system = row.system.replace("_", " ")
        found.append(
            Proposed(
                "exclusion",
                f"{row.label} ({system}) "
                + (
                    "is excluded from our offer"
                    if row.status == "excluded"
                    else "is by others and is not included in our offer"
                )
                + (f" (specification clause {row.clause_number})." if row.clause_number else "."),
                "scope_row",
                str(row.id),
                f"{row.label} ({system})",
            )
        )
    return found


def _from_conventions(session: Session, bid: Bid) -> list[Proposed]:
    from firebid.boq import reconcile
    from firebid.services import boq

    row = boq.conventions_of(session, bid.id)
    if row is None:
        return []
    known = reconcile.conventions()
    chosen = reconcile.validate(dict(row.settings))
    return [
        Proposed(
            "assumption",
            str(known[name]["options"][value]),
            "measurement_convention",
            name,
            f"Measurement convention: {known[name].get('label', name)}",
        )
        for name, value in chosen.items()
    ]


def qualifications(session: Session, bid_id: uuid.UUID) -> list[Qualification]:
    order = {kind: index for index, kind in enumerate(QUALIFICATION_KINDS)}
    return sorted(
        session.execute(select(Qualification).where(Qualification.bid_id == bid_id)).scalars(),
        key=lambda row: (order.get(row.kind, 9), row.created_at),
    )


def propose_qualifications(session: Session, bid: Bid, actor: Actor) -> list[Qualification]:
    """Propose entries from the risks, the checklist, the scope matrix and the measurement
    conventions. An entry already proposed from a source is left as it is: a person may
    have edited it."""
    held = {(row.source_kind, row.source_ref) for row in qualifications(session, bid.id)}
    made = []
    for item in (
        *_from_risks(session, bid),
        *_from_scope(session, bid),
        *_from_conventions(session, bid),
    ):
        if (item.source_kind, item.source_ref) in held:
            continue
        held.add((item.source_kind, item.source_ref))
        row = Qualification(
            bid_id=bid.id,
            kind=item.kind,
            text=item.text,
            source_kind=item.source_kind,
            source_ref=item.source_ref,
            source_label=item.source_label[:300],
            state="proposed",
        )
        session.add(row)
        made.append(row)
    session.flush()
    if made:
        record_event(
            session,
            context=_context(bid),
            actor=actor,
            action="qualifications: proposed",
            entity_type=Qualification.__tablename__,
            entity_id=str(bid.id),
            after={"proposed": len(made), "kinds": sorted({row.kind for row in made})},
        )
    return made


def add_qualification(
    session: Session,
    bid: Bid,
    actor: Actor,
    *,
    kind: str,
    text: str,
    source_kind: str,
    source_ref: str,
) -> Qualification:
    """An entry a person writes. It names its source like any other."""
    if kind not in QUALIFICATION_KINDS:
        raise RiskError("an entry is a qualification, assumption, exclusion or deviation")
    if not text.strip():
        raise RiskError("say what the entry says")
    label = source_label(session, bid, source_kind, source_ref)
    if label is None:
        raise RiskError(
            "an entry links to its source: a risk, a checklist item, a scope-matrix row, a "
            "clarification or a measurement convention of this bid"
        )
    taken = session.execute(
        select(Qualification.id).where(
            Qualification.bid_id == bid.id,
            Qualification.source_kind == source_kind,
            Qualification.source_ref == source_ref,
        )
    ).first()
    if taken is not None:
        raise RiskError("there is already an entry for that source: edit it")
    row = Qualification(
        bid_id=bid.id,
        kind=kind,
        text=text.strip(),
        source_kind=source_kind,
        source_ref=source_ref,
        source_label=label[:300],
        state="proposed",
    )
    session.add(row)
    session.flush()
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action="qualification: added",
        entity_type=Qualification.__tablename__,
        entity_id=row.id,
        after={"kind": kind, "text": row.text, "source": f"{source_kind}: {label}"},
    )
    return row


def source_label(session: Session, bid: Bid, kind: str, ref: str) -> str | None:
    """What a source is called, or None when the bid has no such item."""
    from firebid.boq import reconcile
    from firebid.db.models.clarifications import Clarification
    from firebid.db.models.specs import ScopeRow

    if kind == "measurement_convention":
        known = reconcile.conventions()
        return f"Measurement convention: {known[ref].get('label', ref)}" if ref in known else None
    try:
        key = uuid.UUID(ref)
    except ValueError:
        return None
    if kind == "risk":
        risk = session.get(Risk, key)
        return risk.title if risk is not None and risk.bid_id == bid.id else None
    if kind == "scope_check":
        check = session.get(ScopeCheck, key)
        if check is None or check.bid_id != bid.id:
            return None
        return f"{check.label} ({check.system.replace('_', ' ')})"
    if kind == "scope_row":
        row = session.get(ScopeRow, key)
        if row is None or row.bid_id != bid.id:
            return None
        return f"{row.label} ({row.system.replace('_', ' ')})"
    if kind == "clarification":
        asked = session.get(Clarification, key)
        if asked is None or asked.bid_id != bid.id:
            return None
        return f"TC-{asked.number:03d}: {asked.subject}"
    return None


def edit_qualification(
    session: Session,
    bid: Bid,
    row: Qualification,
    actor: Actor,
    *,
    text: str | None = None,
    kind: str | None = None,
    note: str | None = None,
) -> Qualification:
    """Change an entry's words or kind. Each change is one event of its history."""
    if actor.id is None:
        raise RiskError("an entry is edited by a named person")
    before = {"kind": row.kind, "text": row.text}
    if kind is not None:
        if kind not in QUALIFICATION_KINDS:
            raise RiskError("an entry is a qualification, assumption, exclusion or deviation")
        row.kind = kind
    if text is not None:
        if not text.strip():
            raise RiskError("say what the entry says")
        row.text = text.strip()
    if {"kind": row.kind, "text": row.text} == before:
        return row
    session.flush()
    record_event(
        session,
        context=_context(bid),
        actor=actor,
        action="qualification: edited",
        entity_type=Qualification.__tablename__,
        entity_id=row.id,
        before=before,
        after={"kind": row.kind, "text": row.text},
        reason=note,
    )
    return row


# --- G3 readiness -----------------------------------------------------------------------------


@dataclass
class Readiness:
    checklist_built: bool
    open_checks: list[dict[str, Any]] = field(default_factory=list)
    untreated_risks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.checklist_built and not self.open_checks and not self.untreated_risks

    def describe(self) -> str:
        parts = []
        if not self.checklist_built:
            parts.append("the scope-gap checklist is not built")
        if self.open_checks:
            parts.append(f"{len(self.open_checks)} scope checklist item(s) not resolved")
        if self.untreated_risks:
            parts.append(f"{len(self.untreated_risks)} risk(s) with no treatment")
        return "; ".join(parts)


def g3_readiness(session: Session, bid_id: uuid.UUID) -> Readiness:
    """What stands between the bid and G3 from the risk side: every checklist item resolved
    by status, and every risk given a treatment."""
    items = checks(session, bid_id)
    return Readiness(
        checklist_built=bool(items),
        open_checks=[
            {"id": str(row.id), "system": row.system, "item": row.item_key, "label": row.label}
            for row in items
            if row.status not in rules.RESOLVED
        ],
        untreated_risks=[
            {"id": str(row.id), "title": row.title, "category": row.category}
            for row in risks(session, bid_id)
            if row.treatment is None
        ],
    )
