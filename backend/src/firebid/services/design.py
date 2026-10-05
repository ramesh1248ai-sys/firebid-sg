"""Design development for a bid, through the database (P1-12).

A design-intent tender draws the mains and leaves the heads and range pipes to the
contractor. For each Current plan sheet this service:

1. **Reads the design basis** (`read_basis`): the criteria the sheet's notes state, whether
   a note leaves the design to the contractor, and how many heads are already drawn. The
   result is a `sheet_design` row in state `proposed`. Nothing is laid out yet.
2. **Waits for a person** (`confirm`): a named person chooses the criterion for each sheet.
   Only then is a layout made (guardrail 2).
3. **Lays out** (`lay_out`): heads and range pipes by the organisation's design rules, stored
   as detections and pipe runs marked `designed`. They are proposals in their turn: they
   appear on the workbench in the review queue, a person can say one is not there, and the
   takeoff counts them as items of their own with the rule, its version and the criterion
   (guardrail 1).

The design rules are a versioned measurement rule (`sprinkler_layout`, FR-ADM-02), seeded
from `config/design_rules.yaml` and marked "to be confirmed" until a senior estimator edits
them. A layout keeps the version it was made with.

A sheet with no plan view at a verified or calibrated scale is `blocked`, and says so: a
head grid is set out in real millimetres (FR-VIS-05).
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from firebid.db.audit import record_event
from firebid.db.models.design import SheetDesign
from firebid.db.models.drawings import SheetGeometry, SheetView
from firebid.db.models.symbols import LegendEntry
from firebid.db.models.takeoff import DetectedObject, MeasurementRule, PipeRun
from firebid.design import basis, layout, match_lines, rooms
from firebid.design import export as layout_export
from firebid.design.basis import NoteLine
from firebid.design.layout import Criterion, Layout, Rules
from firebid.domain.actors import Actor, AuditContext
from firebid.drawings import geometry
from firebid.drawings.grids import GridSystem
from firebid.services import geometry as geometry_service
from firebid.services import qto as qto_service
from firebid.storage.object_store import ObjectStore

log = structlog.get_logger("firebid.design")

DESIGNED = "designed"
DESIGN_VERSION = "design-1"
MEASURABLE = ("verified", "calibrated")
PLAN_KINDS = ("plan", "enlarged plan")
# A proposal from rules, not a reading of the drawing: never above the review threshold.
CONFIDENCE = 0.5
HEAD_BOX_MM = 1.5  # half the side of a proposed head's mark on the sheet
NOT_MEASURABLE = "no plan view on this sheet has a verified or calibrated scale (FR-VIS-05)"


class DesignError(ValueError):
    """A design request that cannot be done, with the reason a person can act on."""


# --- The design rules (FR-ADM-02) -------------------------------------------------------------


def rules_row(session: Session, organisation_id: uuid.UUID) -> MeasurementRule:
    """The design rules in force; the seed's, marked "to be confirmed", the first time."""
    current = qto_service.rule_rows(session, organisation_id).get(basis.RULE_KEY)
    if current is not None:
        return current
    seed = basis.seed()
    row = MeasurementRule(
        organisation_id=organisation_id,
        key=basis.RULE_KEY,
        version=1,
        title="Sprinkler layout for design-intent tenders",
        definition=seed,
        status=str(seed.get("status", "to be confirmed")),
        source_note="seeded default (config/design_rules.yaml)",
        effective_from=datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    return row


def rules_of(row: MeasurementRule) -> Rules:
    return basis.load_rules(dict(row.definition) | {"status": row.status}, version=row.version)


# --- Reading the design basis -----------------------------------------------------------------


def _note_lines(table: Any) -> list[NoteLine]:
    return [
        NoteLine(
            str(span.get("text") or ""),
            float(span["minx"]),
            float(span["miny"]),
            float(span["maxy"]) - float(span["miny"]),
        )
        for span in geometry.texts(table)
        if span.get("text")
    ]


def _plan_view(views: list[SheetView]) -> tuple[SheetView | None, bool]:
    """The sheet's largest measurable plan view, and whether it has a plan view at all."""
    plans = [view for view in views if view.kind in PLAN_KINDS]
    measurable = [
        view for view in plans if view.scale_status in MEASURABLE and view.denominator is not None
    ]

    def area(view: SheetView) -> float:
        x0, y0, x1, y1 = view.extent
        return (x1 - x0) * (y1 - y0)

    return (max(measurable, key=area) if measurable else None), bool(plans)


def _drawn_heads(session: Session, bid_id: uuid.UUID, sheet_id: uuid.UUID) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(DetectedObject)
            .where(
                DetectedObject.bid_id == bid_id,
                DetectedObject.sheet_id == sheet_id,
                DetectedObject.kind == "object",
                DetectedObject.object_type.like("sprinkler%"),
                DetectedObject.extraction_method != DESIGNED,
                DetectedObject.state != "rejected",
            )
        ).scalar_one()
    )


def designs(session: Session, bid_id: uuid.UUID) -> dict[uuid.UUID, SheetDesign]:
    return {
        row.sheet_id: row
        for row in session.execute(
            select(SheetDesign).where(SheetDesign.bid_id == bid_id)
        ).scalars()
    }


def read_basis(session: Session, store: ObjectStore, bid_id: uuid.UUID) -> list[SheetDesign]:
    """Read every Current plan sheet's design basis. A confirmed sheet keeps its choice.

    Idempotent: reading again refreshes what the sheet states and leaves a person's
    confirmation, and the layout made from it, alone.
    """
    sheets = qto_service.current_sheets(session, bid_id)
    organisation_id = qto_service._organisation_of(session, bid_id)
    definition = dict(rules_row(session, organisation_id).definition)
    existing = designs(session, bid_id)
    records = {
        record.sheet_id: record
        for record in session.execute(
            select(SheetGeometry).where(SheetGeometry.sheet_id.in_(list(sheets)))
        ).scalars()
    }
    out: list[SheetDesign] = []
    for sheet_id, info in sheets.items():
        record = records.get(sheet_id)
        view, has_plan = _plan_view(list(info.views.values()))
        if record is None or not has_plan:
            continue  # not a plan (a schematic, a detail sheet): nothing to lay out on
        table = geometry_service.load(store, record)
        lines = _note_lines(table)
        stated = basis.criteria_from_notes(lines, info.number)
        quote = basis.design_intent(lines)
        row = existing.get(sheet_id)
        if row is None:
            row = SheetDesign(bid_id=bid_id, sheet_id=sheet_id, state="proposed")
            session.add(row)
        row.criteria = [
            basis.criterion_json(c) for c in [*stated, basis.default_criterion(definition)]
        ]
        row.design_intent = quote is not None
        row.intent_quote = quote
        row.drawn_heads = _drawn_heads(session, bid_id, sheet_id)
        row.view_id = view.id if view else None
        if row.state != "confirmed":
            row.state = "proposed" if view else "blocked"
            row.note = None if view else NOT_MEASURABLE
            _read_match_lines(session, row, table, view)
        out.append(row)
    session.flush()
    log.info("design_basis_read", bid_id=str(bid_id), sheets=len(out))
    return out


def _extent(view: SheetView) -> tuple[float, float, float, float]:
    return (view.extent[0], view.extent[1], view.extent[2], view.extent[3])


def _read_match_lines(
    session: Session, row: SheetDesign, table: Any, view: SheetView | None
) -> None:
    """Find the sheet's match lines and propose its scope from them (FR-DSN-06).

    A scope a person set, and the side a person chose for a line, are left as they are.
    """
    if view is None:
        row.match_lines = []
        return
    grid = GridSystem.from_json(view.grid) if view.grid else None  # type: ignore[arg-type]
    found = match_lines.find(
        table,
        _extent(view),
        grid=[(line.axis, line.position) for line in (*grid.across, *grid.up)] if grid else None,
        services=_drawn_pipes(session, row.sheet_id),
    )
    chosen = {
        _line_key(line): line.get("side")
        for line in row.match_lines or []
        if row.scope_source == "person"
    }
    lines = []
    for line in found.lines:
        entry = line.to_json()
        entry["side"] = chosen.get(_line_key(entry)) or entry["side"]
        lines.append(entry)
    lines += [{"label": label, "line": None} for label in found.unplaced]
    row.match_lines = lines
    if row.scope_source == "person":
        return
    placed = [line for line in lines if line.get("line")]
    polygon = match_lines.scope_from_json(_extent(view), placed)
    row.scope = [[round(x, 3), round(y, 3)] for x, y in polygon] if polygon else None
    row.scope_source = "match_lines" if polygon else None


def _drawn_pipes(session: Session, sheet_id: uuid.UUID) -> list[list[tuple[float, float]]]:
    """The pipe runs found drawn on the sheet, less any a person rejected."""
    return [
        [(float(p[0]), float(p[1])) for p in run.points]
        for run in session.execute(
            select(PipeRun).where(
                PipeRun.sheet_id == sheet_id,
                PipeRun.origin == "detected",
                PipeRun.state != "rejected",
            )
        ).scalars()
    ]


def _line_key(line: dict[str, Any]) -> tuple[float, ...] | None:
    ends = line.get("line")
    if not ends:
        return None
    return tuple(round(float(value)) for point in ends for value in point)


# --- A person's decisions ---------------------------------------------------------------------


@dataclass(frozen=True)
class Entered:
    """A criterion a person states themselves, where the notes give none that applies."""

    max_area_m2: float
    max_spacing_mm: tuple[int, int]
    title: str = "entered criterion"


def _chosen(row: SheetDesign, key: str | None, entered: Entered | None, actor: Actor) -> Criterion:
    if entered is not None:
        if entered.max_area_m2 <= 0 or min(entered.max_spacing_mm) <= 0:
            raise DesignError("a criterion needs a positive area and spacing")
        along, across = max(entered.max_spacing_mm), min(entered.max_spacing_mm)
        return Criterion(
            "entered",
            entered.title,
            entered.max_area_m2,
            (along, across),
            f"entered by {actor.label}",
        )
    found = next((c for c in row.criteria if c.get("key") == key), None)
    if found is None:
        raise DesignError(f"this sheet states no criterion {key!r}")
    return basis.criterion_from_json(dict(found))


@dataclass
class Confirmed:
    confirmed: list[SheetDesign]
    skipped: dict[uuid.UUID, str]  # sheet -> why it was left as it was


def confirm(
    session: Session,
    bid_id: uuid.UUID,
    sheet_ids: list[uuid.UUID],
    actor: Actor,
    *,
    key: str | None = None,
    entered: Entered | None = None,
) -> Confirmed:
    """A person confirms the criterion for these sheets. Queues their layout.

    Each sheet takes the criterion of that key from its own notes, so its evidence cites its
    own words. A sheet that is blocked, or states no such criterion, is skipped with the
    reason; the others are confirmed.
    """
    if (key is None) == (entered is None):
        raise DesignError("choose one of the sheet's criteria, or enter one")
    organisation_id = qto_service._organisation_of(session, bid_id)
    rows = designs(session, bid_id)
    outcome = Confirmed([], {})
    for sheet_id in sheet_ids:
        row = rows.get(sheet_id)
        if row is None:
            outcome.skipped[sheet_id] = "no design basis has been read for this sheet"
            continue
        if row.state == "blocked":
            outcome.skipped[sheet_id] = row.note or NOT_MEASURABLE
            continue
        try:
            criterion = _chosen(row, key, entered, actor)
        except DesignError as refusal:
            if entered is not None:
                raise
            outcome.skipped[sheet_id] = str(refusal)
            continue
        before = {"state": row.state, "criterion": row.criterion}
        row.criterion = basis.criterion_json(criterion)
        row.state = "confirmed"
        row.note = None
        row.confirmed_by_id = actor.id
        row.confirmed_by = actor.label
        row.confirmed_at = datetime.now(UTC)
        session.flush()
        record_event(
            session,
            context=AuditContext(organisation_id=organisation_id, bid_id=bid_id),
            actor=actor,
            action="design basis: confirm",
            entity_type=SheetDesign.__tablename__,
            entity_id=row.id,
            before=before,
            after={"state": row.state, "criterion": row.criterion},
        )
        outcome.confirmed.append(row)
    queue_layout(session, bid_id, actor.id, [row.sheet_id for row in outcome.confirmed])
    return outcome


def withdraw(session: Session, row: SheetDesign, actor: Actor, reason: str | None = None) -> None:
    """Take a sheet's proposed layout out of the bid: its heads and pipes are removed."""
    if row.state != "confirmed":
        raise DesignError("this sheet has no confirmed design basis to withdraw")
    before = {"state": row.state, "criterion": row.criterion, "totals": row.totals}
    _delete_designed(session, row)
    row.state = "proposed"
    row.criterion = None
    row.spaces = []
    row.totals = {}
    row.laid_out_at = None
    row.rule_version = None
    row.confirmed_by_id = None
    row.confirmed_by = None
    row.confirmed_at = None
    session.flush()
    record_event(
        session,
        context=AuditContext(
            organisation_id=qto_service._organisation_of(session, row.bid_id), bid_id=row.bid_id
        ),
        actor=actor,
        action="design basis: withdraw",
        entity_type=SheetDesign.__tablename__,
        entity_id=row.id,
        before=before,
        after={"state": row.state},
        reason=reason,
    )
    qto_service.queue_recompute(session, row.bid_id, actor.id)


def set_scope(
    session: Session, row: SheetDesign, polygon: list[tuple[float, float]] | None, actor: Actor
) -> None:
    """The part of the plan this sheet answers for (its side of a match line)."""
    if polygon is not None and len(polygon) < 3:
        raise DesignError("a scope needs at least three points")
    before = {"scope": row.scope, "scope_source": row.scope_source}
    row.scope = [[round(x, 3), round(y, 3)] for x, y in polygon] if polygon else None
    # A person's choice, the whole sheet included: reading the sheet again leaves it alone.
    row.scope_source = "person"
    session.flush()
    record_event(
        session,
        context=AuditContext(
            organisation_id=qto_service._organisation_of(session, row.bid_id), bid_id=row.bid_id
        ),
        actor=actor,
        action="design basis: scope",
        entity_type=SheetDesign.__tablename__,
        entity_id=row.id,
        before=before,
        after={"scope": row.scope, "scope_source": row.scope_source},
    )
    if row.state == "confirmed":
        queue_layout(session, row.bid_id, actor.id, [row.sheet_id])


def choose_sides(session: Session, row: SheetDesign, sides: dict[int, int], actor: Actor) -> None:
    """A person says which side of each match line this sheet answers for (FR-DSN-06).

    `sides` is by the match line's place in the sheet's list: +1 or -1, as the line records
    it. The scope becomes the plan on the chosen side of every line.
    """
    view = session.get(SheetView, row.view_id) if row.view_id else None
    lines = [dict(line) for line in row.match_lines or []]
    placed = [index for index, line in enumerate(lines) if line.get("line")]
    if view is None or not placed:
        raise DesignError("no match line was found on this sheet: set its scope by hand")
    for index, side in sides.items():
        if index not in placed:
            raise DesignError(f"this sheet has no match line {index + 1}")
        if side not in (1, -1):
            raise DesignError("a side is +1 or -1")
        lines[index]["side"] = side
    polygon = match_lines.scope_from_json(_extent(view), [lines[index] for index in placed])
    if polygon is None:
        raise DesignError("those sides leave no part of the plan: choose another")
    before = {"scope": row.scope, "scope_source": row.scope_source}
    row.match_lines = lines
    row.scope = [[round(x, 3), round(y, 3)] for x, y in polygon]
    row.scope_source = "person"
    session.flush()
    record_event(
        session,
        context=AuditContext(
            organisation_id=qto_service._organisation_of(session, row.bid_id), bid_id=row.bid_id
        ),
        actor=actor,
        action="design basis: match line side",
        entity_type=SheetDesign.__tablename__,
        entity_id=row.id,
        before=before,
        after={
            "scope": row.scope,
            "scope_source": row.scope_source,
            "sides": {str(index): lines[index]["side"] for index in placed},
        },
    )
    if row.state == "confirmed":
        queue_layout(session, row.bid_id, actor.id, [row.sheet_id])


def follow_match_lines(session: Session, row: SheetDesign, actor: Actor) -> None:
    """Go back to the scope the sheet's match lines propose, as it was first read."""
    view = session.get(SheetView, row.view_id) if row.view_id else None
    lines = [dict(line) for line in row.match_lines or []]
    for line in lines:
        if line.get("line"):
            line["side"] = line.get("proposed_side", line.get("side"))
    placed = [line for line in lines if line.get("line")]
    polygon = match_lines.scope_from_json(_extent(view), placed) if view is not None else None
    before = {"scope": row.scope, "scope_source": row.scope_source}
    row.match_lines = lines
    row.scope = [[round(x, 3), round(y, 3)] for x, y in polygon] if polygon else None
    row.scope_source = "match_lines" if polygon else None
    session.flush()
    record_event(
        session,
        context=AuditContext(
            organisation_id=qto_service._organisation_of(session, row.bid_id), bid_id=row.bid_id
        ),
        actor=actor,
        action="design basis: scope",
        entity_type=SheetDesign.__tablename__,
        entity_id=row.id,
        before=before,
        after={"scope": row.scope, "scope_source": row.scope_source},
    )
    if row.state == "confirmed":
        queue_layout(session, row.bid_id, actor.id, [row.sheet_id])


def queue_layout(
    session: Session, bid_id: uuid.UUID, user_id: uuid.UUID | None, sheet_ids: list[uuid.UUID]
) -> None:
    """Queue `design.layout` for each of these sheets, in the caller's transaction.

    A job a sheet, and one waiting job a sheet: confirming one sheet's basis lays out that
    sheet, not every confirmed sheet of the bid again, and a second change to it made before
    the first job has run finds that job waiting.
    """
    from firebid.jobs.enqueue import enqueue_once
    from firebid.jobs.tasks import lay_out_design

    for sheet_id in sheet_ids:
        enqueue_once(
            session,
            lay_out_design,
            f"design.layout:{sheet_id}",
            bid_id=str(bid_id),
            user_id=str(user_id) if user_id else "",
            sheet_id=str(sheet_id),
        )


# --- Laying out -------------------------------------------------------------------------------


def _delete_designed(session: Session, row: SheetDesign) -> None:
    session.execute(
        delete(DetectedObject).where(
            DetectedObject.bid_id == row.bid_id,
            DetectedObject.sheet_id == row.sheet_id,
            DetectedObject.extraction_method == DESIGNED,
        )
    )
    session.execute(
        delete(PipeRun).where(PipeRun.sheet_id == row.sheet_id, PipeRun.origin == DESIGNED)
    )


def _near(value: float) -> float:
    return round(value * 2) / 2


def _rejected(session: Session, row: SheetDesign) -> set[tuple[float, float]]:
    """Proposed heads a person said are not wanted, by where they are: kept across layouts."""
    out = set()
    for found in session.execute(
        select(DetectedObject).where(
            DetectedObject.bid_id == row.bid_id,
            DetectedObject.sheet_id == row.sheet_id,
            DetectedObject.extraction_method == DESIGNED,
            DetectedObject.state == "rejected",
        )
    ).scalars():
        position: dict[str, Any] = dict(found.geometry_ref or {})
        out.add((_near(float(position.get("x", 0.0))), _near(float(position.get("y", 0.0)))))
    return out


def _excluded(session: Session, sheet_id: uuid.UUID, table: Any, page: list[float]) -> list[Any]:
    """Legends and the title block: drawn on the sheet, not part of the plan."""
    from firebid.drawings.views import _title_block_region as title_block

    boxes = [
        (e.row_box[0], e.row_box[1], e.row_box[2], e.row_box[3])
        for e in session.execute(
            select(LegendEntry).where(LegendEntry.sheet_id == sheet_id)
        ).scalars()
    ]
    region = title_block(geometry.texts(table), (page[0], page[1], page[2], page[3]))
    if region is not None:
        boxes.append((region.x0, region.y0, region.x1, region.y1))
    return boxes


def lay_out(session: Session, store: ObjectStore, row: SheetDesign) -> Layout | None:
    """Make the sheet's layout from its confirmed criterion and replace what was proposed.

    Idempotent: the same sheet, criterion and rules give the same heads in the same places.
    """
    if row.state != "confirmed" or row.criterion is None:
        raise DesignError("a person must confirm the design basis before a layout is made")
    view = session.get(SheetView, row.view_id) if row.view_id else None
    record = session.execute(
        select(SheetGeometry).where(SheetGeometry.sheet_id == row.sheet_id)
    ).scalar_one_or_none()
    rejected = _rejected(session, row)
    _delete_designed(session, row)
    row.spaces, row.totals, row.laid_out_at = [], {}, datetime.now(UTC)
    if (
        view is None
        or record is None
        or view.scale_status not in MEASURABLE
        or view.denominator is None
    ):
        # The scale was verified when the basis was confirmed and is not now.
        row.note = NOT_MEASURABLE
        session.flush()
        return None
    organisation_id = qto_service._organisation_of(session, row.bid_id)
    rule = rules_row(session, organisation_id)
    rules = rules_of(rule)
    criterion = basis.criterion_from_json(dict(row.criterion))
    table = geometry_service.load(store, record)
    extent = _extent(view)
    found = rooms.find(
        table,
        extent,
        float(view.denominator),
        exclude=_excluded(session, row.sheet_id, table, record.page),
        scope=[(p[0], p[1]) for p in row.scope] if row.scope else None,
        settings=basis.space_settings(dict(rule.definition)),
    )
    drawn = _drawn_pipes(session, row.sheet_id)
    plan = layout.plan(
        found.spaces, float(view.denominator), criterion, rules, level=view.level, pipes=drawn
    )
    _store(session, row, view, plan, found, basis.rule_record(criterion, rules), rejected)
    row.rule_version = rules.version
    row.note = found.note
    row.totals = plan.totals() | {
        "area_m2": round(sum(space.area_m2 for space in found.spaces), 1),
        "footprint_m2": found.footprint_m2,
        "shafts": len(found.shafts),
    }
    row.spaces = [
        {
            "index": result.space,
            "kind": result.kind,
            "name": result.name,
            "area_m2": result.area_m2,
            "heads": result.heads,
            "pitch_mm": list(result.pitch_mm),
            "omitted_by": result.omitted_by,
            "note": result.note,
            "box": [round(v, 2) for v in found.spaces[position].box],
        }
        for position, result in enumerate(plan.spaces)
    ]
    session.flush()
    log.info(
        "sheet_designed",
        sheet_id=str(row.sheet_id),
        heads=len(plan.heads),
        spaces=len(found.spaces),
        rule_version=rules.version,
    )
    return plan


def _store(
    session: Session,
    row: SheetDesign,
    view: SheetView,
    plan: Layout,
    found: rooms.Found,
    rule: dict[str, Any],
    rejected: set[tuple[float, float]],
) -> None:
    from firebid.services.detection import _current_revision

    revision = _current_revision(session, row.sheet_id)
    grid = GridSystem.from_json(view.grid) if view.grid else None  # type: ignore[arg-type]
    calibration = f"design-rules-v{rule['rule_version']}"
    spaces = {space.index: space for space in found.spaces}
    no_grid = "outside the structural grid" if grid else "the view shows no structural grid"

    def located(x: float, y: float) -> tuple[str | None, dict[str, str]]:
        reference = grid.reference(x, y) if grid is not None else None
        return reference, ({} if reference else {"grid_reference": no_grid})

    for head in plan.heads:
        space = spaces[head.space]
        reference, gaps = located(head.x, head.y)
        state = "rejected" if (_near(head.x), _near(head.y)) in rejected else "proposed"
        source = {
            "rule": rule,
            "design_id": str(row.id),
            "space": {
                "index": space.index,
                "kind": space.kind,
                "name": space.name,
                "area_m2": space.area_m2,
            },
            "head_rule": head.rule_note,
        }
        common: dict[str, Any] = {
            "bid_id": row.bid_id,
            "sheet_id": row.sheet_id,
            "sheet_revision_id": revision,
            "geometry_ref": {
                "x": round(head.x, 3),
                "y": round(head.y, 3),
                "box": [
                    round(head.x - HEAD_BOX_MM, 3),
                    round(head.y - HEAD_BOX_MM, 3),
                    round(head.x + HEAD_BOX_MM, 3),
                    round(head.y + HEAD_BOX_MM, 3),
                ],
            },
            "source_ref": source,
            "extraction_method": DESIGNED,
            "confidence": CONFIDENCE,
            "raw_confidence": CONFIDENCE,
            "features": {"origin": DESIGNED},
            "calibration_version": calibration,
            "detector_version": DESIGN_VERSION,
            "view_id": view.id,
            "grid_reference": reference,
            "level": view.level,
            "gaps": gaps,
            "state": state,
        }
        attributes: dict[str, Any] = {"temperature_rating_c": head.temperature_c}
        if rule.get("criterion") and row.criterion and row.criterion.get("k_factor"):
            attributes["k_factor"] = row.criterion["k_factor"]
        session.add(
            DetectedObject(
                kind="object", object_type=head.object_type, attributes=attributes, **common
            )
        )
        # Every head has a drop, as a drawn one does: the drop rule gives its length.
        session.add(
            DetectedObject(
                kind="drop",
                object_type="pipe",
                attributes={"run_class": "drop", "vertical_not_drawn": True},
                **common,
            )
        )

    index = 1_000_000  # clear of the detected runs' own numbering
    for pipe in plan.ranges:
        lengths: list[tuple[int, int, list[tuple[float, float]], str, bool]] = [
            (dn, length, points, "range", False) for dn, length, points in pipe.stretches()
        ]
        if pipe.feed_mm:
            end = pipe.points[0]
            lengths.append(
                (pipe.feed_dn, pipe.feed_mm, [end, pipe.feed_to or end], "feed", pipe.remote)
            )
        for dn, length, points, part, remote in lengths:
            middle = points[len(points) // 2]
            reference, gaps = located(*middle)
            paper = sum(
                ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
                for (x0, y0), (x1, y1) in itertools.pairwise(points)
            )
            session.add(
                PipeRun(
                    bid_id=row.bid_id,
                    sheet_id=row.sheet_id,
                    sheet_revision_id=revision,
                    view_id=view.id,
                    run_index=index,
                    run_class="branch",
                    nominal_dn=dn,
                    size_status="propagated",
                    size_reason=f"sized by the design rules' range-pipe table "
                    f"(v{rule['rule_version']})",
                    labels=[],
                    paper_length_mm=round(paper, 3),
                    length_mm=float(length),
                    points=[[round(x, 3), round(y, 3)] for x, y in points],
                    geometry_rows=[],
                    grid_reference=reference,
                    level=view.level,
                    features={
                        "origin": DESIGNED,
                        "rule": rule,
                        "design_id": str(row.id),
                        "part": part,
                        "remote": remote,
                        "space": pipe.space,
                    },
                    raw_confidence=CONFIDENCE,
                    confidence=CONFIDENCE,
                    calibration_version=calibration,
                    detector_version=DESIGN_VERSION,
                    gaps=gaps,
                    state="proposed",
                    origin=DESIGNED,
                )
            )
            index += 1


@dataclass
class Outcome:
    sheets: int = 0
    heads: int = 0
    blocked: int = 0


def lay_out_bid(
    session: Session, store: ObjectStore, bid_id: uuid.UUID, only: uuid.UUID | None = None
) -> Outcome:
    """Lay out every sheet whose basis is confirmed; with `only`, that sheet alone."""
    outcome = Outcome()
    current = qto_service.current_sheets(session, bid_id)
    for row in designs(session, bid_id).values():
        if row.state != "confirmed" or row.sheet_id not in current:
            continue
        if only is not None and row.sheet_id != only:
            continue
        plan = lay_out(session, store, row)
        outcome.sheets += 1
        if plan is None:
            outcome.blocked += 1
        else:
            outcome.heads += len(plan.heads)
    return outcome


# --- Export (FR-DSN-05) -----------------------------------------------------------------------


@dataclass
class Exported:
    content: bytes
    media_type: str
    filename: str
    # "gzip" when the content is kept compressed: a DXF is text, and a real sheet's is large.
    encoding: str | None = None


def export_layout(
    session: Session,
    store: ObjectStore,
    row: SheetDesign,
    fmt: str,
    actor: Actor,
    *,
    audit: bool = True,
) -> Exported:
    """The sheet's proposed layout over its tender drawing, as a PDF or a DXF, stamped
    "For estimation only: not for construction". A download for the person asking: nothing
    is sent anywhere. What a person rejected is left out; the export is recorded.

    Made here and now: a real sheet takes tens of seconds, so the API asks a job for it
    (`request_export`, `make_export`) and hands out the kept file (`export_file`).
    """
    if fmt not in layout_export.FORMATS:
        raise DesignError(f"a layout is exported as PDF or DXF, not {fmt!r}")
    if row.state != "confirmed" or row.laid_out_at is None:
        raise DesignError("this sheet has no proposed layout to export yet")
    record = session.execute(
        select(SheetGeometry).where(SheetGeometry.sheet_id == row.sheet_id)
    ).scalar_one_or_none()
    if record is None:
        raise DesignError("this sheet's drawing has not been read")
    heads = [
        layout_export.Head(float(place["x"]), float(place["y"]), found.object_type)
        for found in session.execute(
            select(DetectedObject).where(
                DetectedObject.bid_id == row.bid_id,
                DetectedObject.sheet_id == row.sheet_id,
                DetectedObject.extraction_method == DESIGNED,
                DetectedObject.kind == "object",
                DetectedObject.state != "rejected",
            )
        ).scalars()
        if (place := cast("dict[str, Any]", found.geometry_ref or {})).get("x") is not None
    ]
    if not heads:
        raise DesignError("this sheet's layout proposes no heads: there is nothing to export")
    pipes = [
        layout_export.Pipe(
            [(float(p[0]), float(p[1])) for p in run.points],
            run.nominal_dn,
            str((run.features or {}).get("part", "range")),
        )
        for run in session.execute(
            select(PipeRun)
            .where(
                PipeRun.sheet_id == row.sheet_id,
                PipeRun.origin == DESIGNED,
                PipeRun.state != "rejected",
            )
            .order_by(PipeRun.run_index)
        ).scalars()
    ]
    info = qto_service.current_sheets(session, row.bid_id).get(row.sheet_id)
    number = info.number if info else "sheet"
    revision = info.revision.revision_label if info else None
    view = session.get(SheetView, row.view_id) if row.view_id else None
    criterion: dict[str, Any] = dict(row.criterion or {})
    spacing = list(criterion.get("max_spacing_mm") or [])
    metres = round(sum(run_length for run_length in _lengths(session, row)) / 1000.0, 1)
    notes = [
        f"Tender drawing {number}"
        + (f" revision {revision}" if revision else "")
        + (f", drawn at 1:{view.denominator:g}" if view and view.denominator else "")
        + ".",
        f"Criterion: {criterion.get('title', '-')}: at most {criterion.get('max_area_m2', '-')} m2 "
        f"a head, {' x '.join(str(v) for v in spacing) or '-'} mm apart "
        f"({criterion.get('source', '-')}).",
        f"Design rules version {row.rule_version}; confirmed by {row.confirmed_by}.",
        f"{len(heads)} proposed heads; {metres} m of proposed range pipe and feeds.",
    ]
    if row.scope:
        notes.append(
            "Limited to this sheet's side of its match lines (the dashed outline)."
            if row.scope_source == "match_lines"
            else "Limited to the scope set for this sheet (the dashed outline)."
        )
    notes.append(f"Made {datetime.now(UTC):%d %b %Y %H:%M} UTC by FireBid SG for {actor.label}.")
    sheet = layout_export.Sheet(
        number=number,
        page=(record.page[0], record.page[1], record.page[2], record.page[3]),
        table=geometry_service.load(store, record),
        heads=heads,
        pipes=pipes,
        scope=[(p[0], p[1]) for p in row.scope] if row.scope else None,
        notes=notes,
    )
    content = layout_export.render(sheet, fmt)
    if audit:
        _exported_event(session, row, actor, fmt, len(content), len(heads))
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in number)
    return Exported(content, layout_export.FORMATS[fmt], f"{safe}-proposed-layout.{fmt}")


def _exported_event(
    session: Session, row: SheetDesign, actor: Actor, fmt: str, size: int, heads: int
) -> None:
    record_event(
        session,
        context=AuditContext(
            organisation_id=qto_service._organisation_of(session, row.bid_id), bid_id=row.bid_id
        ),
        actor=actor,
        action="design layout: exported",
        entity_type=SheetDesign.__tablename__,
        entity_id=row.id,
        after={"format": fmt, "bytes": size, "heads": heads},
    )


# The export as a job: asked for, made on a worker, kept, and handed out.


def _proposed_heads(row: SheetDesign) -> int:
    totals: dict[str, Any] = dict(row.totals or {})
    return int(totals.get("heads") or 0)


def layout_stamp(session: Session, row: SheetDesign) -> str:
    """What an export is made from, as a hash: a file made before any of it changed is not
    the layout's file any more."""
    import hashlib
    import json

    kept = session.execute(
        select(func.count())
        .select_from(DetectedObject)
        .where(
            DetectedObject.bid_id == row.bid_id,
            DetectedObject.sheet_id == row.sheet_id,
            DetectedObject.extraction_method == DESIGNED,
            DetectedObject.state != "rejected",
        )
    ).scalar_one()
    pipes = session.execute(
        select(func.count())
        .select_from(PipeRun)
        .where(
            PipeRun.sheet_id == row.sheet_id,
            PipeRun.origin == DESIGNED,
            PipeRun.state != "rejected",
        )
    ).scalar_one()
    facts = [
        row.laid_out_at.isoformat() if row.laid_out_at else None,
        row.scope,
        row.rule_version,
        row.criterion,
        int(kept),
        int(pipes),
    ]
    return hashlib.sha256(json.dumps(facts, sort_keys=True, default=str).encode()).hexdigest()


def exports_of(session: Session, row: SheetDesign) -> dict[str, dict[str, Any]]:
    """Each format's export as it stands: none, queued, ready, stale (the layout has
    changed since it was made) or failed, with why."""
    stamp = layout_stamp(session, row)
    out: dict[str, dict[str, Any]] = {}
    for fmt in layout_export.FORMATS:
        entry: dict[str, Any] = dict((row.exports or {}).get(fmt) or {})
        state = str(entry.get("state") or "none")
        if state == "ready" and entry.get("stamp") != stamp:
            state = "stale"
        out[fmt] = {
            "format": fmt,
            "state": state,
            "bytes": entry.get("bytes"),
            "made_at": entry.get("made_at"),
            "reason": entry.get("reason"),
        }
    return out


def request_export(session: Session, row: SheetDesign, fmt: str, actor: Actor) -> dict[str, Any]:
    """Ask for the sheet's layout in this format. A file already made from the layout as
    it stands is offered at once; otherwise a job is queued to make it."""
    from firebid.jobs.enqueue import enqueue_once
    from firebid.jobs.tasks import make_design_export

    if fmt not in layout_export.FORMATS:
        raise DesignError(f"a layout is exported as PDF or DXF, not {fmt!r}")
    if row.state != "confirmed" or row.laid_out_at is None:
        raise DesignError("this sheet has no proposed layout to export yet")
    if not _proposed_heads(row):
        raise DesignError("this sheet's layout proposes no heads: there is nothing to export")
    status = exports_of(session, row)[fmt]
    if status["state"] == "ready":
        return status
    row.exports = {
        **(row.exports or {}),
        fmt: {
            "state": "queued",
            "asked_by": actor.label,
            "asked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
    }
    session.flush()
    enqueue_once(
        session,
        make_design_export,
        f"design.export:{row.sheet_id}:{fmt}",
        bid_id=str(row.bid_id),
        user_id=str(actor.id) if actor.id else "",
        sheet_id=str(row.sheet_id),
        fmt=fmt,
    )
    return exports_of(session, row)[fmt]


def make_export(session: Session, store: ObjectStore, row: SheetDesign, fmt: str) -> dict[str, Any]:
    """Make the file and keep it (the job). A DXF is kept compressed."""
    import gzip

    entry: dict[str, Any] = dict((row.exports or {}).get(fmt) or {})
    asked_by = str(entry.get("asked_by") or "the estimator")
    try:
        made = export_layout(
            session, store, row, fmt, Actor(label=asked_by, roles=frozenset()), audit=False
        )
    except DesignError as refusal:
        failed: dict[str, object] = {"state": "failed", "reason": str(refusal)}
        row.exports = {**(row.exports or {}), fmt: failed}
        session.flush()
        return exports_of(session, row)[fmt]
    stamp = layout_stamp(session, row)
    compressed = fmt == "dxf"
    data = gzip.compress(made.content, 6) if compressed else made.content
    key = f"bids/{row.bid_id}/design-exports/{row.sheet_id}/{stamp[:16]}.{fmt}" + (
        ".gz" if compressed else ""
    )
    store.put(key, data, content_type="application/gzip" if compressed else made.media_type)
    row.exports = {
        **(row.exports or {}),
        fmt: {
            "state": "ready",
            "stamp": stamp,
            "key": key,
            "gzip": compressed,
            "bytes": len(made.content),
            "stored_bytes": len(data),
            "heads": _proposed_heads(row),
            "filename": made.filename,
            "asked_by": asked_by,
            "made_at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
    }
    session.flush()
    log.info(
        "design_export_made",
        sheet_id=str(row.sheet_id),
        format=fmt,
        bytes=len(made.content),
        stored_bytes=len(data),
    )
    return exports_of(session, row)[fmt]


def export_file(
    session: Session, store: ObjectStore, row: SheetDesign, fmt: str, actor: Actor
) -> Exported:
    """The kept file, for the person asking. Recorded; sent nowhere."""
    if fmt not in layout_export.FORMATS:
        raise DesignError(f"a layout is exported as PDF or DXF, not {fmt!r}")
    status = exports_of(session, row)[fmt]
    if status["state"] == "stale":
        raise DesignError("the layout has changed since this file was made: ask for it again")
    if status["state"] == "failed":
        raise DesignError(f"the file could not be made: {status['reason']}")
    if status["state"] != "ready":
        raise DesignError("this file is not made yet: ask for it, then wait for it to be ready")
    entry: dict[str, Any] = dict(row.exports[fmt])
    content = store.get(str(entry["key"]))
    size = int(entry.get("bytes") or len(content))
    _exported_event(session, row, actor, fmt, size, int(entry.get("heads") or 0))
    return Exported(
        content,
        layout_export.FORMATS[fmt],
        str(entry.get("filename") or f"proposed-layout.{fmt}"),
        "gzip" if entry.get("gzip") else None,
    )


def _lengths(session: Session, row: SheetDesign) -> list[float]:
    return [
        float(length or 0.0)
        for length in session.execute(
            select(PipeRun.length_mm).where(
                PipeRun.sheet_id == row.sheet_id,
                PipeRun.origin == DESIGNED,
                PipeRun.state != "rejected",
            )
        ).scalars()
    ]
