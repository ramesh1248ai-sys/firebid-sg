"""A bid's stage outputs, in the shape of a golden reference package (FR-LRN-01).

`firebid.evals.golden` compares a run with a package. This writes the run: what the
platform holds for a bid at each stage, laid out as the package lays out what it expects.
It reads what the services stored and works nothing out again: a count here is a count of
stored detections, a length the sum of stored runs, an item a live takeoff item.

Stages 1 to 7 here; stages 8 to 12 in `firebid.evals.export_commercial`. A stage with
nothing stored is left out, and the comparison reports it as not exported.

Read on a session that may see the bid: the caller's own, or the service role's for a
report across bids (`firebid-eval export-run`).
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.db.models.core import Bid
from firebid.db.models.documents import Document, Sheet, SheetRevision
from firebid.db.models.drawings import SheetView
from firebid.db.models.symbols import LegendEntry, SymbolMapping
from firebid.db.models.takeoff import DetectedObject, DuplicateGroup, PipeRun, QtoItem
from firebid.evals import export_commercial as commercial
from firebid.evals.golden import Run
from firebid.services import qto

MEASURABLE = ("verified", "calibrated")
DRAWINGS = ("pdf", "dxf")  # the kinds that are read sheet by sheet
VERDICTS = {"nts": "not to scale"}
PIPE_RUNS = ("main", "branch", "drop", "riser")
SIZE = re.compile(r"\d+")


def export(session: Session, bid_id: uuid.UUID) -> Run:
    revisions = list(
        session.execute(
            select(SheetRevision)
            .where(SheetRevision.bid_id == bid_id, SheetRevision.sheet_number.is_not(None))
            .order_by(SheetRevision.sheet_number, SheetRevision.revision_label)
        ).scalars()
    )
    # A sheet is named by the drawing number of its current revision.
    number_of = {r.sheet_id: str(r.sheet_number) for r in revisions if r.state == "current"}
    run: Run = {}
    for stage_id, output in (
        ("STG-001", _intake(session, bid_id)),
        ("STG-002", _register(revisions)),
        ("STG-003", _views(session, bid_id, number_of)),
        ("STG-004", _legend(session, bid_id, number_of)),
        ("STG-005", _objects(session, bid_id, number_of)),
        ("STG-006", _pipe(session, bid_id, number_of)),
        ("STG-007", _takeoff(session, bid_id)),
    ):
        if output is not None:
            run[stage_id] = output
    bid = session.get(Bid, bid_id)
    if bid is not None:
        for stage_id, read in (
            ("STG-008", commercial.specification),
            ("STG-009", commercial.bill),
            ("STG-010", commercial.pricing),
            ("STG-011", commercial.risks),
            ("STG-012", commercial.review),
        ):
            later = read(session, bid)
            if later is not None:
                run[stage_id] = later
    return run


def _intake(session: Session, bid_id: uuid.UUID) -> dict[str, Any] | None:
    documents = list(
        session.execute(
            select(Document).where(Document.bid_id == bid_id).order_by(Document.filename)
        ).scalars()
    )
    if not documents:
        return None
    sheets: dict[uuid.UUID, list[Sheet]] = defaultdict(list)
    for sheet in session.execute(select(Sheet).where(Sheet.bid_id == bid_id)).scalars():
        sheets[sheet.document_id].append(sheet)
    return {
        "documents": [
            {
                "filename": document.filename,
                "kind": document.kind,
                "document_type": document.doc_type,
                "state": document.state,
                # A specification or a workbook has no sheets to count.
                **({"sheets": len(sheets[document.id])} if document.kind in DRAWINGS else {}),
            }
            for document in documents
        ],
        "sheets_total": sum(len(found) for found in sheets.values()),
        "refused": [
            {"filename": d.filename, "reason": d.rejected_reason}
            for d in documents
            if d.state in ("rejected", "quarantined")
        ],
        "unread_sheets": [
            {"document": str(sheet.document_id), "index": sheet.index_in_document}
            for found in sheets.values()
            for sheet in found
            if sheet.parse_error
        ],
    }


def _register(revisions: list[SheetRevision]) -> dict[str, Any] | None:
    if not revisions:
        return None
    return {
        "sheets": [
            {
                "drawing_number": r.sheet_number,
                "title": r.title,
                "revision": r.revision_label,
                "date": r.revision_date.isoformat() if r.revision_date else None,
                "stated_scale": r.scale_text,
                "level": r.level,
                "status": r.state,
            }
            for r in revisions
        ],
        "superseded": [r.sheet_number for r in revisions if r.state == "superseded"],
    }


def _scale(view: SheetView) -> str | None:
    if view.scale_status == "nts":
        return "NTS"
    denominator = view.denominator or view.stated_denominator
    if denominator:
        return f"1:{denominator:g}"
    return view.stated_scale


def _views(
    session: Session, bid_id: uuid.UUID, number_of: dict[uuid.UUID, str]
) -> dict[str, Any] | None:
    views = list(
        session.execute(
            select(SheetView)
            .where(SheetView.bid_id == bid_id)
            .order_by(SheetView.sheet_id, SheetView.ordinal)
        ).scalars()
    )
    if not views:
        return None
    by_sheet: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for view in views:
        if view.sheet_id not in number_of:
            continue  # not a current sheet: nothing is taken off from it
        by_sheet[number_of[view.sheet_id]].append(
            {
                "kind": view.kind,
                "title": view.title,
                "scale": _scale(view),
                "scale_verdict": VERDICTS.get(view.scale_status, view.scale_status),
                "measurable": view.scale_status in MEASURABLE,
                "level": view.level,
            }
        )
    return {"sheets": [{"sheet": number, "views": by_sheet[number]} for number in sorted(by_sheet)]}


def _legend(
    session: Session, bid_id: uuid.UUID, number_of: dict[uuid.UUID, str]
) -> dict[str, Any] | None:
    entries = list(
        session.execute(
            select(LegendEntry)
            .where(LegendEntry.bid_id == bid_id)
            .order_by(LegendEntry.sheet_id, LegendEntry.ordinal)
        ).scalars()
    )
    entries = [entry for entry in entries if entry.sheet_id in number_of]
    if not entries:
        return None
    lineages = {entry.mapping_lineage_id for entry in entries if entry.mapping_lineage_id}
    latest: dict[uuid.UUID, SymbolMapping] = {}
    if lineages:
        for mapping in session.execute(
            select(SymbolMapping)
            .where(SymbolMapping.lineage_id.in_(lineages))
            .order_by(SymbolMapping.version)
        ).scalars():
            latest[mapping.lineage_id] = mapping
    rows = []
    for entry in entries:
        mapped = latest.get(entry.mapping_lineage_id) if entry.mapping_lineage_id else None
        rows.append(
            {
                "sheet": number_of[entry.sheet_id],
                "ordinal": entry.ordinal,
                "description": entry.description,
                "object_type": mapped.object_type_key if mapped else None,
                "state": entry.status,
            }
        )
    return {
        "legend_sheets": sorted({row["sheet"] for row in rows}),
        "heading": entries[0].heading,
        "rows": rows,
    }


def _objects(
    session: Session, bid_id: uuid.UUID, number_of: dict[uuid.UUID, str]
) -> dict[str, Any] | None:
    found = list(
        session.execute(select(DetectedObject).where(DetectedObject.bid_id == bid_id)).scalars()
    )
    if not found:
        return None
    counts: dict[str, dict[str, int]] = {number: {} for number in number_of.values()}
    for one in found:
        number = number_of.get(one.sheet_id)
        # What a person rejected is not there; a riser or a drop is pipe, counted at stage 7.
        if number is None or one.kind != "object" or one.state == "rejected":
            continue
        counts[number][one.object_type] = counts[number].get(one.object_type, 0) + 1
    return {
        "sheets": [
            {"sheet": number, "counts": dict(sorted(counts[number].items()))}
            for number in sorted(counts)
        ]
    }


def _pipe(
    session: Session, bid_id: uuid.UUID, number_of: dict[uuid.UUID, str]
) -> dict[str, Any] | None:
    runs = list(session.execute(select(PipeRun).where(PipeRun.bid_id == bid_id)).scalars())
    if not runs:
        return None
    lengths: dict[str, dict[str, float]] = {number: {} for number in number_of.values()}
    unsized: dict[str, float] = defaultdict(float)
    for run in runs:
        number = number_of.get(run.sheet_id)
        if number is None or run.length_mm is None:
            continue
        if run.nominal_dn is None:
            unsized[number] += run.length_mm
            continue
        size = str(run.nominal_dn)
        lengths[number][size] = lengths[number].get(size, 0.0) + run.length_mm
    return {
        "sheets": [
            {
                "sheet": number,
                "measured": bool(lengths[number]) or number in unsized,
                "length_mm_by_dn": {dn: round(v, 1) for dn, v in sorted(lengths[number].items())},
                **({"unsized_length_mm": round(unsized[number], 1)} if number in unsized else {}),
            }
            for number in sorted(lengths)
        ]
    }


def _value(attributes: dict[str, Any], name: str) -> str | None:
    found = attributes.get(name)
    if isinstance(found, dict):
        found = found.get("value")
    return None if found in (None, "", "not specified") else str(found)


def _size(text: str | None) -> int | str | None:
    """`150` -> 150; `DN150xDN50` -> `150x50`."""
    numbers = SIZE.findall(text or "")
    if not numbers:
        return None
    return int(numbers[0]) if len(numbers) == 1 else "x".join(numbers)


def _item(item: QtoItem) -> dict[str, Any]:
    """One takeoff item, as the package names it: what it is, its size and its run."""
    attributes = dict(item.attributes)
    one: dict[str, Any] = {
        "item": item.item_type,
        "quantity": float(item.net_quantity),
        "unit": item.unit,
        "level": item.level,
        "state": item.state,
        "description": item.description,
    }
    size = _size(_value(attributes, "nominal_diameter_mm") or _value(attributes, "size"))
    outlet = _value(attributes, "outlet_diameter_mm")
    if outlet and size is not None:
        size = f"{size}x{outlet}"
    if size is not None:
        one["dn"] = size
    if item.item_type.startswith("fitting_"):
        one["item"], one["fitting"] = "fitting", item.item_type.removeprefix("fitting_")
    elif item.item_type == "fitting" and _value(attributes, "fitting"):
        one["fitting"] = _value(attributes, "fitting")
    elif item.item_type == "pipe_hanger":
        one["item"] = "hanger"
    elif item.item_type == "pipe" and item.classification in PIPE_RUNS:
        one["run"] = item.classification
    return one


def _takeoff(session: Session, bid_id: uuid.UUID) -> dict[str, Any] | None:
    items = qto.live_items(session, bid_id)
    if not items:
        return None
    drawn: list[dict[str, Any]] = []
    derived: list[dict[str, Any]] = []
    for item in items:
        one = _item(item)
        if item.calculation_method == "rule_derived":
            one["rule"] = item.rule_key
            derived.append(one)
        else:
            drawn.append(one)
    groups = list(
        session.execute(select(DuplicateGroup).where(DuplicateGroup.bid_id == bid_id)).scalars()
    )
    return {
        "drawn_items": drawn,
        "derived_items": derived,
        "duplicate_groups": len(groups),
    }
