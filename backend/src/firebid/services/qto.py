"""Takeoff for a bid, through the database (P1-07).

Reads P1-05's detections and runs from Current sheets only (guardrail 6), P1-06's verified
specification attributes, the organisation's measurement rules and the bid's parameters;
runs the engine (`firebid.qto`); and stores QTO items, each with its Appendix B evidence
record, and the duplicate groups it found.

* **Rules** are versioned rows (FR-ADM-02), seeded from `config/measurement_rules.yaml` the
  first time an organisation takes off; a rule a later release adds to that file is seeded
  when it is first missed. An edit retires the rule's current version and adds the next; an
  item keeps the version it was calculated with.
* **What sheets say in words** is read from their stored text: ceiling heights in notes,
  the level schedule a schematic or section gives (floor-to-floor heights for the riser
  rule), and equipment schedules (what each tagged pump or tank is).
* **Recompute** is idempotent. Items are matched by key (what and where). The same key and
  inputs hash leave the stored item alone, verification and all. Changed inputs supersede it
  with a new proposal that keeps its human ID; an item no longer found is superseded.
* **Duplicate groups** keep a person's decision for as long as the same group is found.
* **Manual items** (FR-QTO-11) are created, edited and deleted by a person, measured only on
  a view whose scale is verified or calibrated.
* **G1** is refused while a duplicate group is unresolved or an item's evidence is
  incomplete.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from firebid.db.audit import record_event
from firebid.db.ids import next_qto_id
from firebid.db.models.core import AppUser, Bid, Project
from firebid.db.models.documents import Sheet, SheetRevision
from firebid.db.models.drawings import GeometryFeature, SheetView
from firebid.db.models.takeoff import (
    BidParameter,
    DetectedObject,
    DuplicateGroup,
    Evidence,
    MeasurementRule,
    PipeRun,
    QtoItem,
)
from firebid.db.models.workflow import Approval
from firebid.domain.actors import SYSTEM_ACTOR, Actor, AuditContext
from firebid.domain.evidence import EvidenceRecord, Location, RunMetadata, SourceRef
from firebid.domain.state_machines import QtoItemState
from firebid.domain.values import CalculationMethod, ExtractionMethod, LengthMm
from firebid.drawings import equipment
from firebid.drawings.grids import GridSystem
from firebid.qto import dedup, generate, rules
from firebid.qto.model import (
    Detection,
    ItemDraft,
    Placement,
    Run,
    ScheduleRow,
    SpecValue,
    level_of,
)
from firebid.services import object_library
from firebid.services import specs as spec_service
from firebid.services.transitions import apply_transition

log = structlog.get_logger("firebid.qto")

MEASURABLE = ("verified", "calibrated")
# How a detection was found, in the evidence record's terms.
EXTRACTION = {
    "cad_block": ExtractionMethod.CAD_ENTITY,
    "pdf_shape": ExtractionMethod.PDF_VECTOR,
    "vision": ExtractionMethod.VISION,
    "topology": ExtractionMethod.RULE,
    "rule": ExtractionMethod.RULE,
    "manual": ExtractionMethod.MANUAL,
    # Proposed by the design rules for a design-intent sheet (P1-12): not on the drawing.
    "designed": ExtractionMethod.RULE,
}
# Items no longer in play: every other state is a live item of the bid's takeoff.
GONE = (str(QtoItemState.SUPERSEDED),)
PARAMETERS = (
    "ceiling_height_mm",
    "branch_elevation_mm",
    "sprinkler_setting_mm",
    "floor_to_floor_mm",
    "levels_served",
)
# "CEILING HEIGHT 2750", "CLG HT: 2800MM", as architects' notes state it.
CEILING_NOTE = re.compile(
    r"\b(?:CEILING\s+HEIGHT|CEILING\s+HT|CLG\.?\s*HT\.?)\s*[:=]?\s*(\d{3,5})\s*(?:MM)?\b",
    re.IGNORECASE,
)


class QtoError(ValueError):
    """A takeoff request that cannot be done, with the reason a person can act on."""


# --- Measurement rules (FR-ADM-02) ----------------------------------------------------------


def _organisation_of(session: Session, bid_id: uuid.UUID) -> uuid.UUID:
    return session.execute(select(Bid.organisation_id).where(Bid.id == bid_id)).scalar_one()


def rule_rows(session: Session, organisation_id: uuid.UUID) -> dict[str, MeasurementRule]:
    """Each rule's version in force; the seed's, marked "to be confirmed", the first time."""
    rows = list(
        session.execute(
            select(MeasurementRule).where(
                MeasurementRule.organisation_id == organisation_id,
                MeasurementRule.retired_at.is_(None),
            )
        ).scalars()
    )
    # The seed's rules the organisation has never had: all of them the first time, and
    # afterwards any a later release added (hangers and seismic restraint, P2-01). A rule
    # in force is never touched, and a retired one always has a successor in force.
    missing = [seed for seed in rules.seed_rules() if seed.key not in {row.key for row in rows}]
    if missing:
        now = datetime.now(UTC)
        for seed in missing:
            row = MeasurementRule(
                organisation_id=organisation_id,
                key=seed.key,
                version=1,
                title=seed.title,
                definition=seed.definition,
                status=seed.status,
                source_note="seeded default (config/measurement_rules.yaml)",
                effective_from=now,
            )
            session.add(row)
            rows.append(row)
        session.flush()
    return {row.key: row for row in rows}


def _rule(row: MeasurementRule) -> rules.Rule:
    return rules.Rule(row.key, row.version, row.title, dict(row.definition), row.status)


def rule_set(session: Session, organisation_id: uuid.UUID) -> dict[str, rules.Rule]:
    return {key: _rule(row) for key, row in rule_rows(session, organisation_id).items()}


def rule_history(session: Session, organisation_id: uuid.UUID, key: str) -> list[MeasurementRule]:
    rule_rows(session, organisation_id)
    return list(
        session.execute(
            select(MeasurementRule)
            .where(MeasurementRule.organisation_id == organisation_id, MeasurementRule.key == key)
            .order_by(MeasurementRule.version)
        ).scalars()
    )


def edit_rule(
    session: Session,
    organisation_id: uuid.UUID,
    key: str,
    *,
    definition: dict[str, Any],
    actor: Actor,
    title: str | None = None,
    status: str = "confirmed",
    note: str | None = None,
) -> MeasurementRule:
    """A new version of a rule. The old one is retired, never changed."""
    current = rule_rows(session, organisation_id).get(key)
    if current is None:
        raise QtoError(f"no measurement rule {key!r}")
    now = datetime.now(UTC)
    current.retired_at = now
    session.flush()  # the retirement first, so the new version is the only one in force
    row = MeasurementRule(
        organisation_id=organisation_id,
        key=key,
        version=current.version + 1,
        title=title or current.title,
        definition=definition,
        status=status,
        source_note=note,
        effective_from=now,
        created_by_id=actor.id,
    )
    session.add(row)
    session.flush()
    record_event(
        session,
        context=AuditContext(organisation_id=organisation_id),
        actor=actor,
        action="measurement rule: new version",
        entity_type=MeasurementRule.__tablename__,
        entity_id=row.id,
        before={"key": key, "version": current.version, "definition": current.definition},
        after={"key": key, "version": row.version, "definition": definition, "status": status},
        reason=note,
    )
    return row


# --- Rule inputs: bid parameters and sheet notes --------------------------------------------


def set_parameter(
    session: Session,
    bid_id: uuid.UUID,
    name: str,
    value: Decimal,
    actor: Actor,
    *,
    level: str | None = None,
    source: str | None = None,
) -> BidParameter:
    if name not in PARAMETERS:
        raise QtoError(f"unknown parameter {name!r}; expected one of {', '.join(PARAMETERS)}")
    if value < 0:
        raise QtoError("a parameter cannot be negative")
    row = BidParameter(
        bid_id=bid_id,
        name=name,
        level=level,
        value=value,
        source=source or f"entered by {actor.label}",
        created_by_id=actor.id,
    )
    session.add(row)
    session.flush()
    record_event(
        session,
        context=AuditContext(organisation_id=_organisation_of(session, bid_id), bid_id=bid_id),
        actor=actor,
        action="bid parameter: set",
        entity_type=BidParameter.__tablename__,
        entity_id=row.id,
        after={"name": name, "level": level, "value": str(value), "source": row.source},
    )
    return row


Texts = dict[uuid.UUID, list[dict[str, Any]]]


def sheet_texts(session: Session, bid_id: uuid.UUID, sheets: dict[uuid.UUID, SheetInfo]) -> Texts:
    """The text on each Current sheet, as spans with their boxes: read once a recompute,
    as plain columns, for everything that is read from words."""
    out: Texts = {}
    for sheet_id, label, box in session.execute(
        select(GeometryFeature.sheet_id, GeometryFeature.label, GeometryFeature.bbox).where(
            GeometryFeature.bid_id == bid_id,
            GeometryFeature.kind == "text",
            GeometryFeature.sheet_id.in_(list(sheets)),
        )
    ):
        if not label:
            continue
        out.setdefault(sheet_id, []).append(
            {
                "text": label,
                "minx": box[0],
                "miny": box[1],
                "maxx": box[2],
                "maxy": box[3],
                "height": box[3] - box[1],
            }
        )
    return out


def note_parameters(
    session: Session,
    bid_id: uuid.UUID,
    sheets: dict[uuid.UUID, SheetInfo],
    texts: Texts | None = None,
) -> list[rules.Parameter]:
    """What Current sheets state in words that a rule uses: ceiling heights in notes, for
    the sheet's level, and floor-to-floor heights from a level schedule, for each level it
    names (P2-01)."""
    texts = sheet_texts(session, bid_id, sheets) if texts is None else texts
    found = []
    for sheet_id, spans in texts.items():
        info = sheets[sheet_id]
        for span in spans:
            match = CEILING_NOTE.search(span["text"])
            if match is None:
                continue
            found.append(
                rules.Parameter(
                    "ceiling_height_mm",
                    float(match.group(1)),
                    f"sheet {info.number} note '{span['text'].strip()}'",
                    info.level,
                )
            )
        found.extend(rules.level_parameters(info.number, equipment.level_marks(spans)))
    return sorted(found, key=lambda p: (p.level or "", p.name, p.source))


def schedule_rows(sheets: dict[uuid.UUID, SheetInfo], texts: Texts) -> list[ScheduleRow]:
    """Every equipment schedule row drawn on a Current sheet, with the sheet it is on."""
    rows: list[ScheduleRow] = []
    for sheet_id, spans in texts.items():
        if not any(equipment.SCHEDULE_HEADING.search(span["text"]) for span in spans):
            continue
        info = sheets[sheet_id]
        rows.extend(
            ScheduleRow(
                tag=row.tag,
                values=dict(row.values),
                quote=row.quote,
                sheet_id=str(sheet_id),
                sheet_number=info.number,
                revision=info.revision.revision_label or "",
                heading=row.heading,
            )
            for row in equipment.schedules(spans)
        )
    return sorted(rows, key=lambda row: (row.sheet_number, row.tag, row.quote))


def parameters(
    session: Session,
    bid_id: uuid.UUID,
    sheets: dict[uuid.UUID, SheetInfo],
    texts: Texts | None = None,
) -> list[rules.Parameter]:
    """Notes first, then what estimators entered, oldest first: the last one stated wins."""
    entered = session.execute(
        select(BidParameter)
        .where(BidParameter.bid_id == bid_id)
        .order_by(BidParameter.created_at, BidParameter.id)
    ).scalars()
    return [
        *note_parameters(session, bid_id, sheets, texts),
        *(rules.Parameter(p.name, float(p.value), p.source, p.level) for p in entered),
    ]


# --- What the engine reads -----------------------------------------------------------------


@dataclass
class SheetInfo:
    sheet: Sheet
    revision: SheetRevision
    views: dict[uuid.UUID, SheetView] = field(default_factory=dict)
    grids: dict[uuid.UUID, GridSystem | None] = field(default_factory=dict)

    @property
    def number(self) -> str:
        return self.revision.sheet_number or f"sheet {self.sheet.index_in_document + 1}"

    @property
    def level(self) -> str | None:
        plan_levels = [v.level for v in self.views.values() if v.level]
        return level_of(self.number, *plan_levels, self.revision.level)

    def placement(self, view_id: uuid.UUID | None, level: str | None) -> Placement:
        view = self.views.get(view_id) if view_id else None
        return Placement(
            sheet_id=str(self.sheet.id),
            sheet_number=self.number,
            revision=self.revision.revision_label or "",
            document_id=str(self.sheet.document_id),
            view_id=str(view_id) if view_id else None,
            view_kind=view.kind if view else None,
            level=level_of(self.number, level, view.level if view else None, self.revision.level),
            zone=self.revision.zone,
        )

    def grid_index(
        self, view_id: uuid.UUID | None, x: float, y: float
    ) -> tuple[float, float] | None:
        grid = self.grids.get(view_id) if view_id else None
        return grid.index(x, y) if grid else None


def current_sheets(session: Session, bid_id: uuid.UUID) -> dict[uuid.UUID, SheetInfo]:
    """Sheets whose revision is Current: the only ones that feed takeoff (guardrail 6)."""
    out: dict[uuid.UUID, SheetInfo] = {}
    # One query for the revisions with their sheets, not one a sheet.
    for revision, sheet in session.execute(
        select(SheetRevision, Sheet)
        .join(Sheet, Sheet.id == SheetRevision.sheet_id)
        .where(SheetRevision.bid_id == bid_id, SheetRevision.state == "current")
    ):
        out[sheet.id] = SheetInfo(sheet, revision)
    for view in session.execute(
        select(SheetView).where(SheetView.bid_id == bid_id, SheetView.sheet_id.in_(list(out)))
    ).scalars():
        info = out[view.sheet_id]
        info.views[view.id] = view
        info.grids[view.id] = (
            GridSystem.from_json(view.grid)  # type: ignore[arg-type]
            if view.grid
            else None
        )
    return out


def inputs(
    session: Session, bid_id: uuid.UUID, sheets: dict[uuid.UUID, SheetInfo]
) -> tuple[list[Detection], list[Run]]:
    """Detections and runs of Current sheets, located as the engine needs them."""
    categories = {
        kind.key: kind.category
        for kind in object_library.current(session, _organisation_of(session, bid_id))
    }
    revisions = {info.revision.id for info in sheets.values()}
    detections = []
    for row in session.execute(
        select(DetectedObject)
        .where(DetectedObject.bid_id == bid_id, DetectedObject.sheet_id.in_(list(sheets)))
        .order_by(DetectedObject.sheet_id, DetectedObject.id)
    ).scalars():
        if row.sheet_revision_id is not None and row.sheet_revision_id not in revisions:
            continue
        info = sheets[row.sheet_id]
        position = row.geometry_ref or {}
        x, y = float(position.get("x", 0.0)), float(position.get("y", 0.0))  # type: ignore[arg-type]
        detections.append(
            Detection(
                id=str(row.id),
                at=info.placement(row.view_id, row.level),
                kind=row.kind,
                object_type=row.object_type,
                category=categories.get(row.object_type, "pipe" if row.kind != "object" else ""),
                attributes=dict(row.attributes or {}),
                x=x,
                y=y,
                grid_reference=row.grid_reference,
                grid_index=info.grid_index(row.view_id, x, y),
                confidence=float(row.confidence if row.confidence is not None else 0.0),
                method=row.extraction_method,
                evidence=dict(row.source_ref or {}),
                # Kept, not dropped: what a person rejected takes its copies with it.
                rejected=row.state == "rejected",
            )
        )
    runs = []
    for run in session.execute(
        select(PipeRun)
        .where(PipeRun.bid_id == bid_id, PipeRun.sheet_id.in_(list(sheets)))
        .order_by(PipeRun.sheet_id, PipeRun.run_index)
    ).scalars():
        if run.state == "rejected" or (
            run.sheet_revision_id is not None and run.sheet_revision_id not in revisions
        ):
            continue
        info = sheets[run.sheet_id]
        view = info.views.get(run.view_id) if run.view_id else None
        points = tuple((float(p[0]), float(p[1])) for p in run.points)
        runs.append(
            Run(
                id=str(run.id),
                at=info.placement(run.view_id, run.level),
                run_class=run.run_class,
                dn=run.nominal_dn,
                size_status=run.size_status,
                length_mm=round(run.length_mm) if run.length_mm is not None else None,
                points=points,
                grid_reference=run.grid_reference,
                grid_points=tuple(info.grid_index(run.view_id, x, y) for x, y in points),
                confidence=float(run.confidence),
                labels=tuple(str(label.get("text", "")) for label in run.labels or []),
                scale=view.denominator if view and view.scale_status in MEASURABLE else None,
                origin=run.origin,
                evidence=dict(run.features or {}) if run.origin == "designed" else {},
                system=str(system) if (system := (run.features or {}).get("system")) else None,
            )
        )
    return detections, runs


def spec_lookup(session: Session, bid_id: uuid.UUID) -> generate.SpecLookup:
    """P1-06's verified attributes, each value with the clause it cites."""
    cache: dict[tuple[str, int | None], dict[str, list[SpecValue]]] = {}

    def lookup(system: str, dn: int | None) -> dict[str, list[SpecValue]]:
        if (system, dn) not in cache:
            answers = spec_service.attributes_for(session, bid_id, system, dn)
            cache[(system, dn)] = {
                name: [
                    SpecValue(
                        value,
                        cited.clause,
                        {
                            "document_id": str(cited.document_id),
                            "document_revision_id": str(cited.document_revision_id),
                            "revision_label": cited.revision_label,
                            "clause": cited.clause,
                            "quote": cited.quote,
                        },
                    )
                    for value, cited in answer.rows
                ]
                for name, answer in answers.items()
                if answer.rows
            }
        return cache[(system, dn)]

    return lookup


# --- Recompute ------------------------------------------------------------------------------


@dataclass
class Outcome:
    created: int = 0
    unchanged: int = 0
    superseded: int = 0
    groups: int = 0
    unresolved_groups: int = 0
    incomplete: list[dict[str, Any]] = field(default_factory=list)
    # The items G1 was reopened for, when this recompute changed what it had approved.
    reopened: list[str] = field(default_factory=list)


def is_adopted(item: QtoItem) -> bool:
    """Whether the item was adopted from the project's shared takeoff (FR-BID-04)."""
    return bool(dict(item.derivation or {}).get("shared"))


def live_items(session: Session, bid_id: uuid.UUID) -> list[QtoItem]:
    """The bid's takeoff as it stands: every item not superseded."""
    return list(
        session.execute(
            select(QtoItem)
            .where(QtoItem.bid_id == bid_id, QtoItem.state.not_in(GONE))
            .order_by(QtoItem.human_id)
        ).scalars()
    )


def _rule_set_version(rule_set_: dict[str, rules.Rule]) -> str:
    return ",".join(f"{key}@{rule.version}" for key, rule in sorted(rule_set_.items()))


def recompute(session: Session, bid_id: uuid.UUID, actor: Actor = SYSTEM_ACTOR) -> Outcome:
    """Take off the bid again. The same inputs change nothing (see the module notes)."""
    organisation_id = _organisation_of(session, bid_id)
    sheets = current_sheets(session, bid_id)
    everything, runs = inputs(session, bid_id, sheets)
    rejected = [d for d in everything if d.rejected]
    detections = [d for d in everything if not d.rejected]
    found = dedup.find(detections, runs)
    stored = sync_groups(session, bid_id, found)
    excluded, lengths = dedup.exclusions(found, {key: row.status for key, row in stored.items()})
    excluded |= dedup.twins(rejected, detections)
    rule_set_ = rule_set(session, organisation_id)
    texts = sheet_texts(session, bid_id, sheets)
    drafts = generate.generate(
        detections,
        runs,
        spec_lookup(session, bid_id),
        rule_set_,
        parameters(session, bid_id, sheets, texts),
        excluded=excluded,
        excluded_length=lengths,
        carried=dedup.carried_sizes(found),
        schedules=schedule_rows(sheets, texts),
    )
    version = _rule_set_version(rule_set_)
    methods = _sheet_methods(detections)
    outcome = Outcome(groups=len(stored))
    existing: dict[str, QtoItem] = {}
    for item in sorted(live_items(session, bid_id), key=_kept_first):
        if not item.item_key or item.is_manual or is_adopted(item):
            # A person's own item, or one adopted from the project's shared takeoff
            # (FR-BID-04): neither comes from this bid's drawings, so neither is recomputed.
            continue
        if item.item_key in existing:
            # Two live items under one key: an earlier recompute created one without
            # retiring the other (items whose keys were not their own). The one a person
            # has decided, else the newest, is kept; the others are retired here.
            _supersede(session, item, "stored twice under one key by an earlier recompute")
            outcome.superseded += 1
            continue
        existing[item.item_key] = item
    for draft in drafts:
        old = existing.pop(draft.key, None)
        digest = draft.inputs_hash()
        if old is not None and old.inputs_hash == digest:
            # The same quantity from the same inputs: verification stands. Only the links to
            # the rows it was found as are refreshed, so the drawing still opens it.
            derivation: dict[str, Any] = dict(old.derivation or {})
            derivation.update(members=draft.members, geometry=draft.geometry, sources=draft.sources)
            if draft.rule:
                derivation["rule"] = draft.rule
            if derivation != old.derivation:  # untouched rows stay unlocked for people
                old.derivation = derivation
            outcome.unchanged += 1
            continue
        if old is not None:
            _supersede(session, old, "its inputs changed when takeoff was recomputed")
            outcome.superseded += 1
        _create(session, bid_id, draft, digest, version, methods, previous=old)
        outcome.created += 1
    for gone in existing.values():
        _supersede(session, gone, "no longer found when takeoff was recomputed")
        outcome.superseded += 1
    session.flush()
    # The takeoff as it now stands, read once for what follows.
    items = live_items(session, bid_id)
    _link_groups(session, bid_id, stored, items)
    outcome.unresolved_groups = sum(1 for g in stored.values() if g.status == "unresolved")
    outcome.incomplete = completeness(session, bid_id, items=items)
    if outcome.created or outcome.superseded:
        # What G1 approved has changed: the gate is reopened for those items (FR-QTO-12).
        from firebid.services import delta

        outcome.reopened = delta.reopen_if_changed(session, bid_id, items)
    log.info(
        "qto_recomputed",
        bid_id=str(bid_id),
        created=outcome.created,
        unchanged=outcome.unchanged,
        superseded=outcome.superseded,
        groups=outcome.groups,
        incomplete=len(outcome.incomplete),
    )
    return outcome


DECIDED = (str(QtoItemState.VERIFIED), str(QtoItemState.REJECTED))


def _kept_first(item: QtoItem) -> tuple[int, float]:
    """Among items stored under one key: one a person decided first, then the newest."""
    return (0 if item.state in DECIDED else 1, -item.created_at.timestamp())


def _sheet_methods(detections: list[Detection]) -> dict[str, str]:
    """How each sheet's symbols were found: its pipe was read from the same geometry."""
    methods: dict[str, dict[str, int]] = {}
    for detection in detections:
        if detection.method in ("cad_block", "pdf_shape"):
            tally = methods.setdefault(detection.at.sheet_id, {})
            tally[detection.method] = tally.get(detection.method, 0) + 1
    return {sheet: max(tally, key=lambda m: tally[m]) for sheet, tally in methods.items()}


def queue_recompute(session: Session, bid_id: uuid.UUID, user_id: uuid.UUID | None) -> None:
    """Queue `qto.recompute` in the caller's transaction.

    One waiting job serves every change made before it starts: a recompute reads the bid as
    it is when it runs, so a second one queued behind it would do the same work again.
    """
    from firebid.jobs.enqueue import enqueue_once
    from firebid.jobs.tasks import recompute_qto

    enqueue_once(
        session,
        recompute_qto,
        f"qto.recompute:{bid_id}",
        bid_id=str(bid_id),
        user_id=str(user_id) if user_id else "",
    )


def _supersede(session: Session, item: QtoItem, reason: str) -> None:
    apply_transition(
        session, item, target=QtoItemState.SUPERSEDED, actor=SYSTEM_ACTOR, reason=reason
    )


def _create(
    session: Session,
    bid_id: uuid.UUID,
    draft: ItemDraft,
    digest: str,
    rule_set_version: str,
    methods: dict[str, str],
    previous: QtoItem | None,
) -> QtoItem:
    method = draft.detection_method
    if method == "network":
        first = draft.sources[0]["sheet_id"] if draft.sources else None
        method = methods.get(str(first), "pdf_shape")
    item = QtoItem(
        bid_id=bid_id,
        human_id=previous.human_id if previous else next_qto_id(session, bid_id),
        item_type=draft.item_type,
        classification=draft.classification,
        description=draft.description,
        attributes=draft.attributes,
        unit=draft.unit,
        net_quantity=draft.net_quantity,
        allowance_percent=draft.allowance_percent,
        length=LengthMm(draft.length_mm) if draft.length_mm is not None else None,
        level=draft.level,
        zone=draft.zone,
        grid_from=draft.grid_from,
        grid_to=draft.grid_to,
        calculation_method=draft.calculation_method,
        rule_key=draft.rule["rule_key"] if draft.rule else None,
        rule_version=draft.rule["rule_version"] if draft.rule else None,
        is_manual=False,
        confidence=draft.confidence,
        state=str(QtoItemState.DETECTED),
        version=previous.version + 1 if previous else 1,
        supersedes_id=previous.id if previous else None,
        item_key=draft.key,
        inputs_hash=digest,
        derivation={
            "members": draft.members,
            "sources": draft.sources,
            "geometry": draft.geometry,
            "rule": draft.rule,
            "note": draft.note,
            "detection_method": method,
            "rule_set_version": rule_set_version,
        },
    )
    session.add(item)
    session.flush()
    apply_transition(
        session,
        item,
        target=QtoItemState.PROPOSED,
        actor=SYSTEM_ACTOR,
        reason="taken off from the Current sheets",
    )
    return item


# --- Duplicate groups (FR-QTO-08) -----------------------------------------------------------


def sync_groups(
    session: Session, bid_id: uuid.UUID, found: list[dedup.Group]
) -> dict[str, DuplicateGroup]:
    """Store what was found. A person's decision stays while the same group is found."""
    stored = {
        row.key: row
        for row in session.execute(
            select(DuplicateGroup).where(DuplicateGroup.bid_id == bid_id)
        ).scalars()
    }
    keep: dict[str, DuplicateGroup] = {}
    for group in found:
        row = stored.pop(group.key, None)
        if row is None:
            row = DuplicateGroup(
                bid_id=bid_id,
                key=group.key,
                kind=group.kind,
                level=group.level,
                status=group.status,
                reason=group.reason,
                members=group.members,
            )
            session.add(row)
        elif row.members != group.members:
            row.members = group.members
            row.reason = group.reason
            if row.decided_at is None:
                row.status = group.status
        keep[group.key] = row
    for row in stored.values():  # no longer found: nothing left to decide
        session.delete(row)
    session.flush()
    return keep


def decide_group(
    session: Session,
    group: DuplicateGroup,
    decision: str,
    actor: Actor,
    note: str | None = None,
) -> DuplicateGroup:
    """`confirmed` keeps one of each repeat; `not_duplicate` counts every member."""
    if decision not in ("confirmed", "not_duplicate"):
        raise QtoError("decide 'confirmed' or 'not_duplicate'")
    before = group.status
    group.status = decision
    group.decided_by_id = actor.id
    group.decided_by = actor.label
    group.decided_at = datetime.now(UTC)
    group.decision_note = note
    session.flush()
    record_event(
        session,
        context=AuditContext(
            organisation_id=_organisation_of(session, group.bid_id), bid_id=group.bid_id
        ),
        actor=actor,
        action="duplicate group: decide",
        entity_type=DuplicateGroup.__tablename__,
        entity_id=group.id,
        before={"status": before},
        after={"status": decision},
        reason=note,
    )
    return group


def _link_groups(
    session: Session,
    bid_id: uuid.UUID,
    groups: dict[str, DuplicateGroup],
    items: list[QtoItem] | None = None,
) -> None:
    """Each live item points at an unresolved group any of its members is in."""
    member_group: dict[str, uuid.UUID] = {}
    for group in groups.values():
        if group.status != "unresolved":
            continue
        for member in group.members:
            member_group.setdefault(str(member["id"]), group.id)
    for item in items if items is not None else live_items(session, bid_id):
        ids = [str(m.get("id")) for m in (item.derivation or {}).get("members", [])]  # type: ignore[attr-defined]
        group_id = next((member_group[i] for i in ids if i in member_group), None)
        if item.duplicate_group_id != group_id:  # untouched rows are not written again
            item.duplicate_group_id = group_id
    session.flush()


# --- Evidence (FR-QTO-09) -------------------------------------------------------------------


def _calculation_note(item: QtoItem, derivation: dict[str, Any]) -> str:
    note = _how_calculated(item, derivation)
    edit = derivation.get("edit")
    if isinstance(edit, dict):
        note += (
            f"; edited by {edit.get('by')} on {str(edit.get('at', ''))[:10]} "
            f"({edit.get('reason_code')}), was {edit.get('before', {}).get('net_quantity')}"
        )
    return note


def _how_calculated(item: QtoItem, derivation: dict[str, Any]) -> str:
    members = derivation.get("members") or []
    sheets = sorted({str(m.get("sheet")) for m in members if m.get("sheet")})
    where = ", ".join(sheets)
    if item.is_manual:
        return str(derivation.get("note") or "entered by a person")
    if item.calculation_method == CalculationMethod.RULE_DERIVED:
        rule = derivation.get("rule") or {}
        stated = "; ".join(
            f"{i.get('name')} = {i.get('value')} ({i.get('source')})"
            for i in rule.get("inputs", [])
            if "name" in i
        )
        note = derivation.get("note")
        return (
            f"rule {rule.get('rule_key')} v{rule.get('rule_version')} "
            f"({rule.get('rule_status')})"
            + (f": {note}" if note else "")
            + (f"; inputs: {stated}" if stated else "")
        )
    if item.calculation_method == CalculationMethod.CENTRELINE_LENGTH:
        note = derivation.get("note")
        return f"sum of {len(members)} run centrelines at verified scale on {where}" + (
            f"; {note}" if note else ""
        )
    return f"count of {len(members)} detections on {where}"


def _extraction(derivation: dict[str, Any], manual: bool) -> ExtractionMethod:
    if manual:
        return ExtractionMethod.MANUAL
    method = str(derivation.get("detection_method") or "")
    return EXTRACTION.get(method, ExtractionMethod.PDF_VECTOR)


def evidence_for(
    session: Session,
    bid: Bid,
    item: QtoItem,
    *,
    project_name: str | None = None,
    verifiers: dict[uuid.UUID, str] | None = None,
) -> EvidenceRecord:
    """The item's Appendix B record, from the item and how it was derived.

    `project_name` and `verifiers` are for a caller with many items, which reads them once
    for all of them; without them they are read here.
    """
    derivation: dict[str, Any] = dict(item.derivation or {})
    sources = derivation.get("sources") or []
    first = sources[0] if sources else {}
    by_number = {s.get("sheet_number"): s.get("sheet_id") for s in sources}
    geometry = derivation.get("geometry") or []
    links = []
    for mark in geometry:
        sheet_id = by_number.get(mark.get("sheet"))
        if sheet_id is None:
            continue
        if mark.get("points"):
            x, y = mark["points"][0]
        else:
            x, y = mark.get("x"), mark.get("y")
        links.append(f"/bids/{bid.id}/sheets/{sheet_id}?x={x}&y={y}")
    shared = derivation.get("shared")
    if isinstance(shared, dict):
        # Adopted from the project's shared takeoff (FR-BID-04): the drawings are the
        # publishing bid's, so the evidence is the published item, by its own reference.
        links = [
            f"/bids/{bid.id}/shared-takeoff#{shared.get('source_bid')}-"
            f"{shared.get('source_human_id')}-v{shared.get('version')}"
        ]
    members = derivation.get("members") or []
    kinds = sorted({str(m.get("kind")) for m in members})
    geometry_reference = (
        f"{len(members)} {'/'.join(kinds)} on "
        + ", ".join(sorted({str(m.get("sheet")) for m in members}))
        + ": "
        + ", ".join(str(m.get("id") or f"{m.get('x')},{m.get('y')}") for m in members)
        if members
        else None
    )
    if verifiers is not None:
        verified_by = verifiers.get(item.verified_by_id) if item.verified_by_id else None
    else:
        verifier = session.get(AppUser, item.verified_by_id) if item.verified_by_id else None
        verified_by = verifier.display_name if verifier else None
    if project_name is None:
        project = session.get(Project, bid.project_id)
        project_name = project.name if project else ""
    allowance = (
        f"; allowance {item.allowance_percent}% shown separately, "
        f"{rules.adjusted(item.net_quantity, item.allowance_percent)} {item.unit} with it"
        if item.allowance_percent
        else ""
    )
    return EvidenceRecord(
        qto_human_id=item.human_id,
        bid_human_id=bid.human_id,
        project_name=project_name,
        item_description=item.description,
        classification=item.classification or "",
        attributes={
            name: str(value.get("value")) if isinstance(value, dict) else str(value)
            for name, value in (item.attributes or {}).items()
        },
        quantity=item.net_quantity,
        unit=item.unit,
        quantity_note=f"net {item.net_quantity} {item.unit}{allowance}",
        source=SourceRef(
            document_id=uuid.UUID(str(first["document_id"])) if first else uuid.UUID(int=0),
            sheet_id=uuid.UUID(str(first["sheet_id"])) if first.get("sheet_id") else None,
            sheet_number=first.get("sheet_number"),
            revision_label=first.get("revision") or None,
        ),
        location=Location(
            level=item.level, zone=item.zone, grid_from=item.grid_from, grid_to=item.grid_to
        ),
        geometry_reference=geometry_reference,
        detection_method=_extraction(derivation, item.is_manual),
        calculation_method=CalculationMethod(item.calculation_method),
        calculation_note=_calculation_note(item, derivation),
        evidence_links=links,
        confidence=max(0.0, min(1.0, item.confidence if item.confidence is not None else 0.0)),
        verification_status=item.state,
        verified_by=verified_by,
        verified_at=item.verified_at,
        run_metadata=RunMetadata(rule_set_version=derivation.get("rule_set_version")),
    )


def completeness(
    session: Session,
    bid_id: uuid.UUID,
    *,
    write: bool = True,
    only: Iterable[uuid.UUID] | None = None,
    items: list[QtoItem] | None = None,
) -> list[dict[str, Any]]:
    """Check every live item's evidence record; list the items missing a mandatory field.

    `items` is the bid's live items when the caller has just read them.

    With `write`, the records are stored too: for every item, or `only` the items an action
    touched. Checking alone writes nothing, so G1's status can be asked for as often as a
    screen likes without holding up the people working (P1-08).
    """
    bid = session.get(Bid, bid_id)
    if bid is None:
        raise QtoError("no such bid")
    wanted = set(only) if only is not None else None
    stored = (
        {
            row.qto_item_id: row
            for row in session.execute(select(Evidence).where(Evidence.bid_id == bid_id)).scalars()
        }
        if write
        else {}
    )
    if items is None:
        items = live_items(session, bid_id)
    # Read once for every item, not once an item: a real tender has thousands.
    project = session.get(Project, bid.project_id)
    project_name = project.name if project else ""
    verifier_ids = {item.verified_by_id for item in items if item.verified_by_id}
    verifiers = (
        {
            row.id: row.display_name
            for row in session.execute(
                select(AppUser).where(AppUser.id.in_(verifier_ids))
            ).scalars()
        }
        if verifier_ids
        else {}
    )
    offending = []
    for item in items:
        record = evidence_for(session, bid, item, project_name=project_name, verifiers=verifiers)
        missing = record.missing_mandatory_fields()
        if missing and item.state != str(QtoItemState.REJECTED):
            # A rejected item is decided and out of the takeoff: it does not hold G1 up.
            offending.append({"id": str(item.id), "human_id": item.human_id, "missing": missing})
        if not write or (wanted is not None and item.id not in wanted):
            continue
        row = stored.get(item.id)
        if row is None:
            session.add(
                Evidence(
                    bid_id=bid_id,
                    qto_item_id=item.id,
                    record=record.model_dump(mode="json"),
                    missing_fields=missing,
                )
            )
        else:
            dumped = record.model_dump(mode="json")
            if row.record != dumped:  # an unchanged record is not written again
                row.record = dumped
            if row.missing_fields != missing:
                row.missing_fields = missing
    if write:
        session.flush()
    return offending


# --- Manual items (FR-QTO-11) ---------------------------------------------------------------


@dataclass
class Measured:
    """A length measured on a view: the view and the sheet points along it."""

    view: SheetView
    points: list[tuple[float, float]]


def _measure(session: Session, bid_id: uuid.UUID, measured: Measured) -> tuple[int, dict[str, Any]]:
    from firebid.drawings import scale
    from firebid.services import views as view_service

    if measured.view.bid_id != bid_id:
        raise QtoError("no such view")
    try:
        length = view_service.measure(measured.view, measured.points)
    except scale.NotMeasurable as refused:
        raise QtoError(f"this view cannot be measured: {refused}") from refused
    return round(length), {
        "sheet_id": str(measured.view.sheet_id),
        "view_id": str(measured.view.id),
        "points": [list(p) for p in measured.points],
        "scale": measured.view.denominator,
        "scale_status": measured.view.scale_status,
    }


def _mark(session: Session, bid_id: uuid.UUID, marked: Measured) -> dict[str, Any]:
    """Counted points on a view: allowed only where the view's scale is verified or
    calibrated, as a measurement is, so every manual item sits on a trustworthy sheet."""
    if marked.view.bid_id != bid_id:
        raise QtoError("no such view")
    if marked.view.scale_status not in MEASURABLE:
        raise QtoError(
            f"this view cannot be measured: its scale is {marked.view.scale_status}; "
            "calibrate it first"
        )
    if not marked.points:
        raise QtoError("place at least one mark")
    return {
        "kind": "marks",
        "sheet_id": str(marked.view.sheet_id),
        "view_id": str(marked.view.id),
        "points": [list(p) for p in marked.points],
        "scale": marked.view.denominator,
        "scale_status": marked.view.scale_status,
    }


def _manual_derivation(
    session: Session, bid_id: uuid.UUID, actor: Actor, measurement: dict[str, Any] | None
) -> dict[str, Any]:
    stamp = datetime.now(UTC).isoformat()
    derivation: dict[str, Any] = {
        "manual": {"by": actor.label, "by_id": str(actor.id) if actor.id else None, "at": stamp},
        "detection_method": "manual",
        "members": [],
        "sources": [],
        "geometry": [],
        "rule_set_version": "manual",
    }
    if measurement is None:
        derivation["note"] = f"entered by {actor.label} at {stamp}"
        return derivation
    sheets = current_sheets(session, bid_id)
    info = sheets.get(uuid.UUID(measurement["sheet_id"]))
    if info is None:
        raise QtoError("measure on a Current sheet (guardrail 6)")
    derivation.update(
        members=[{"kind": "measurement", "id": measurement["view_id"], "sheet": info.number}],
        sources=[
            {
                "sheet_id": str(info.sheet.id),
                "sheet_number": info.number,
                "revision": info.revision.revision_label or "",
                "document_id": str(info.sheet.document_id),
                "view_id": measurement["view_id"],
            }
        ],
        geometry=(
            [{"sheet": info.number, "x": p[0], "y": p[1]} for p in measurement["points"]]
            if measurement.get("kind") == "marks"
            else [{"sheet": info.number, "points": measurement["points"]}]
        ),
        measurement=measurement,
        note=(
            f"{len(measurement['points'])} placed by {actor.label} at {stamp} on a view at "
            f"1:{measurement['scale']:g} ({measurement['scale_status']} scale)"
            if measurement.get("kind") == "marks"
            else f"measured by {actor.label} at {stamp} along {len(measurement['points'])} "
            f"points at 1:{measurement['scale']:g} ({measurement['scale_status']} scale)"
        ),
    )
    return derivation


def create_manual(
    session: Session,
    bid_id: uuid.UUID,
    actor: Actor,
    *,
    item_type: str,
    description: str,
    unit: str,
    quantity: Decimal | None = None,
    measured: Measured | None = None,
    marked: Measured | None = None,
    classification: str | None = None,
    attributes: dict[str, str] | None = None,
    level: str | None = None,
    zone: str | None = None,
    grid_from: str | None = None,
    grid_to: str | None = None,
    allowance_percent: Decimal | None = None,
) -> QtoItem:
    """A person's count or length item, tagged manual with their name and the time."""
    item = QtoItem(
        bid_id=bid_id,
        human_id=next_qto_id(session, bid_id),
        version=1,
        state=str(QtoItemState.DETECTED),
    )
    _fill_manual(
        session,
        bid_id,
        item,
        actor,
        item_type=item_type,
        description=description,
        unit=unit,
        quantity=quantity,
        measured=measured,
        marked=marked,
        classification=classification,
        attributes=attributes,
        level=level,
        zone=zone,
        grid_from=grid_from,
        grid_to=grid_to,
        allowance_percent=allowance_percent,
    )
    session.add(item)
    session.flush()
    apply_transition(
        session, item, target=QtoItemState.PROPOSED, actor=SYSTEM_ACTOR, reason="manual item"
    )
    _audit_manual(session, actor, "manual QTO item: create", None, item)
    completeness(session, bid_id, only=[item.id])
    return item


def _fill_manual(
    session: Session,
    bid_id: uuid.UUID,
    item: QtoItem,
    actor: Actor,
    *,
    item_type: str,
    description: str,
    unit: str,
    quantity: Decimal | None,
    measured: Measured | None,
    classification: str | None,
    marked: Measured | None = None,
    attributes: dict[str, str] | None,
    level: str | None,
    zone: str | None,
    grid_from: str | None,
    grid_to: str | None,
    allowance_percent: Decimal | None,
) -> None:
    if sum(x is not None for x in (quantity, measured, marked)) != 1:
        raise QtoError(
            "give a quantity, marks on the drawing for a count, or a measurement for a length"
        )
    if not description.strip():
        raise QtoError("describe the item")
    measurement = None
    length_mm = None
    if measured is not None:
        length_mm, measurement = _measure(session, bid_id, measured)
        quantity = (Decimal(length_mm) / Decimal(1000)).quantize(Decimal("0.001"))
        unit = "m"
    if marked is not None:
        measurement = _mark(session, bid_id, marked)
        quantity = Decimal(len(marked.points))
    if quantity is None or quantity < 0:
        raise QtoError("a quantity cannot be negative")
    quantity = quantity.quantize(Decimal("0.001"))
    derivation = _manual_derivation(session, bid_id, actor, measurement)
    item.item_type = item_type
    item.classification = classification or item_type
    item.description = description
    item.attributes = {k: {"value": v, "source": "manual"} for k, v in (attributes or {}).items()}
    item.unit = unit
    item.net_quantity = quantity
    item.allowance_percent = allowance_percent
    item.length = LengthMm(length_mm) if length_mm is not None else None
    item.level = level or (
        level_of(derivation["sources"][0]["sheet_number"]) if derivation["sources"] else None
    )
    item.zone = zone
    item.grid_from = grid_from
    item.grid_to = grid_to
    item.calculation_method = str(
        CalculationMethod.MANUAL_MEASURE if measured else CalculationMethod.COUNT
    )
    item.is_manual = True
    item.confidence = 1.0
    item.created_by_id = actor.id
    item.item_key = None
    item.inputs_hash = None
    item.derivation = derivation


def edit_manual(session: Session, item: QtoItem, actor: Actor, **changes: Any) -> QtoItem:
    """A new version of a manual item; the old one is superseded and kept."""
    if not item.is_manual:
        raise QtoError(
            "only manual items are edited here; generated items are verified or edited in review"
        )
    if item.state in GONE:
        raise QtoError("this version has been replaced; edit the current one")
    fresh = QtoItem(
        bid_id=item.bid_id,
        human_id=item.human_id,
        version=item.version + 1,
        supersedes_id=item.id,
        state=str(QtoItemState.DETECTED),
    )
    stated: dict[str, Any] = dict(item.attributes or {})
    current: dict[str, Any] = {
        "item_type": item.item_type,
        "description": item.description,
        "unit": item.unit,
        "classification": item.classification,
        "attributes": {k: v.get("value") for k, v in stated.items()},
        "level": item.level,
        "zone": item.zone,
        "grid_from": item.grid_from,
        "grid_to": item.grid_to,
        "allowance_percent": item.allowance_percent,
        "quantity": None,
        "measured": None,
        "marked": None,
    }
    if "measured" not in changes and "quantity" not in changes:
        if item.calculation_method == CalculationMethod.MANUAL_MEASURE:
            raise QtoError("re-measure a length item to change it")
        current["quantity"] = item.net_quantity
    current.update(changes)
    _fill_manual(session, item.bid_id, fresh, actor, **current)
    session.add(fresh)
    session.flush()
    _retire_manual(session, item, "replaced by a person's edit")
    apply_transition(
        session,
        fresh,
        target=QtoItemState.PROPOSED,
        actor=SYSTEM_ACTOR,
        reason="manual item edited",
    )
    _audit_manual(session, actor, "manual QTO item: edit", item, fresh)
    completeness(session, item.bid_id, only=[fresh.id])
    return fresh


def delete_manual(session: Session, item: QtoItem, actor: Actor, reason: str | None = None) -> None:
    """Deleted is rejected, through the state machine: the row and its history stay."""
    if not item.is_manual:
        raise QtoError("only manual items are deleted; reject a generated item in review")
    apply_transition(
        session,
        item,
        target=QtoItemState.REJECTED,
        actor=actor,
        reason=reason or "manual item deleted",
    )
    item.reason_code = "deleted"
    _audit_manual(session, actor, "manual QTO item: delete", item, None)
    session.flush()


def _retire_manual(session: Session, item: QtoItem, reason: str) -> None:
    apply_transition(
        session, item, target=QtoItemState.SUPERSEDED, actor=SYSTEM_ACTOR, reason=reason
    )


def _audit_manual(
    session: Session, actor: Actor, action: str, before: QtoItem | None, after: QtoItem | None
) -> None:
    def state(item: QtoItem | None) -> dict[str, Any] | None:
        if item is None:
            return None
        return {
            "human_id": item.human_id,
            "version": item.version,
            "description": item.description,
            "quantity": str(item.net_quantity),
            "unit": item.unit,
            "state": item.state,
        }

    subject = after or before
    if subject is None:
        return
    record_event(
        session,
        context=AuditContext(
            organisation_id=_organisation_of(session, subject.bid_id), bid_id=subject.bid_id
        ),
        actor=actor,
        action=action,
        entity_type=QtoItem.__tablename__,
        entity_id=subject.id,
        before=state(before),
        after=state(after),
    )


# --- Verification (the workbench, P1-08, builds on these) -----------------------------------


def verify(session: Session, item: QtoItem, actor: Actor, note: str | None = None) -> QtoItem:
    target = QtoItemState.VERIFIED
    apply_transition(session, item, target=target, actor=actor, reason=note)
    item.verified_by_id = actor.id
    item.verified_at = datetime.now(UTC)
    session.flush()
    return item


# --- G1 (QTO verified) ----------------------------------------------------------------------


@dataclass
class Blockers:
    unresolved_groups: list[dict[str, Any]]
    incomplete_items: list[dict[str, Any]]
    pending_work: list[dict[str, Any]] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)
    unmapped_symbols: list[dict[str, Any]] = field(default_factory=list)
    # BOQ lines with no QTO item behind them and not marked provisional or lump sum
    # (FR-BOQ-05), once a BOQ is built.
    untraced_lines: list[dict[str, Any]] = field(default_factory=list)

    @property
    def coverage_short(self) -> bool:
        return bool(self.coverage) and not self.coverage.get("met")

    @property
    def clear(self) -> bool:
        return not (
            self.unresolved_groups
            or self.incomplete_items
            or self.pending_work
            or self.unmapped_symbols
            or self.untraced_lines
            or self.coverage_short
        )


# Jobs that change what takeoff reads. A queued recompute is not here: approving G1
# recomputes first, in its own transaction.
_UPSTREAM = (
    "parse.document",
    "parse.sheet",
    "parse.finish",
    "parse.complete",
    "detection.sheet",
    "detection.run",
    "detection.vision",
    "spec.read",
    "spec.sections",
    "spec.attributes",
)
_PENDING = text(
    """
    SELECT task_name, status, count(*) AS jobs FROM procrastinate_jobs
    WHERE status IN ('todo', 'doing') AND task_name = ANY(:tasks)
      AND (args->>'bid_id' = :bid
           OR args->>'document_id' IN (SELECT id::text FROM document WHERE bid_id = :bid_id)
           OR args->>'sheet_id' IN (SELECT id::text FROM sheet WHERE bid_id = :bid_id)
           OR args->>'revision_id' IN
              (SELECT id::text FROM document_revision WHERE bid_id = :bid_id))
    GROUP BY task_name, status ORDER BY task_name
    """
)


def pending_work(session: Session, bid_id: uuid.UUID) -> list[dict[str, Any]]:
    """Reading and detection still queued or running: the takeoff is not settled yet."""
    rows = session.execute(
        _PENDING, {"tasks": list(_UPSTREAM), "bid": str(bid_id), "bid_id": bid_id}
    )
    return [{"task": row.task_name, "status": row.status, "jobs": row.jobs} for row in rows]


def g1_blockers(session: Session, bid_id: uuid.UUID) -> Blockers:
    """What stops G1: unresolved duplicate groups (FR-QTO-08), incomplete evidence (FR-QTO-09).

    Also, work still queued or running for the bid: drawings being read or detected
    (the takeoff would change under the approval); verification coverage below the policy
    (FR-REV-04); and symbols on Current sheets nobody has mapped, which count as nothing.
    """
    from firebid.services import boq, review

    groups = session.execute(
        select(DuplicateGroup)
        .where(DuplicateGroup.bid_id == bid_id, DuplicateGroup.status == "unresolved")
        .order_by(DuplicateGroup.created_at)
    ).scalars()
    return Blockers(
        unresolved_groups=[
            {"id": str(g.id), "kind": g.kind, "level": g.level, "reason": g.reason} for g in groups
        ],
        incomplete_items=completeness(session, bid_id, write=False),
        pending_work=pending_work(session, bid_id),
        coverage=review.coverage(session, bid_id),
        unmapped_symbols=review.unmapped_in_scope(session, bid_id),
        untraced_lines=boq.untraced_lines(session, bid_id),
    )


def approve_g1(
    session: Session, bid: Bid, actor: Actor, role: str, comment: str | None = None
) -> Approval:
    """Record G1 on the takeoff as it stands now, or raise `QtoError` listing what blocks it.

    Takeoff is recomputed first, so the approval never rests on a result a queued
    recompute was about to change.
    """
    recompute(session, bid.id)
    blockers = g1_blockers(session, bid.id)
    if not blockers.clear:
        parts = []
        if blockers.pending_work:
            jobs = sum(int(w["jobs"]) for w in blockers.pending_work)
            parts.append(f"{jobs} reading or detection job(s) still to finish")
        if blockers.unresolved_groups:
            parts.append(f"{len(blockers.unresolved_groups)} unresolved duplicate group(s)")
        if blockers.incomplete_items:
            parts.append(f"{len(blockers.incomplete_items)} item(s) with incomplete evidence")
        if blockers.coverage_short:
            parts.append(
                f"{blockers.coverage['items_percent']:g}% of items verified, "
                f"{blockers.coverage['policy_percent']:g}% needed"
            )
        if blockers.unmapped_symbols:
            parts.append(f"{len(blockers.unmapped_symbols)} unmapped symbol type(s) in scope")
        if blockers.untraced_lines:
            parts.append(
                f"{len(blockers.untraced_lines)} BOQ line(s) with no QTO trace, "
                "not marked provisional or lump sum"
            )
        raise QtoError("G1 is blocked: " + "; ".join(parts))
    if actor.id is None:
        raise QtoError("a gate is approved by a named person")
    approval = Approval(
        bid_id=bid.id,
        gate="G1",
        decision="approved",
        approver_id=actor.id,
        approver_role=role,
        decided_at=datetime.now(UTC),
        comment=comment,
        snapshot_hash=snapshot_hash(live_items(session, bid.id)),
    )
    session.add(approval)
    session.flush()
    # The baseline a later revision or addendum is compared with (FR-QTO-12).
    from firebid.services import delta

    delta.take_snapshot(session, bid.id, "G1 approved", approval_id=approval.id)
    record_event(
        session,
        context=AuditContext(organisation_id=bid.organisation_id, bid_id=bid.id),
        actor=actor,
        action="gate G1: approve",
        entity_type=Approval.__tablename__,
        entity_id=approval.id,
        after={"gate": "G1", "decision": "approved", "snapshot_hash": approval.snapshot_hash},
        reason=comment,
    )
    return approval


def snapshot_hash(items: Iterable[QtoItem]) -> str:
    """What was approved: every live item's ID, version, state and quantity."""
    import hashlib

    lines = sorted(
        f"{i.human_id}|{i.version}|{i.state}|{i.net_quantity}|{i.allowance_percent}|{i.inputs_hash}"
        for i in items
    )
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()
