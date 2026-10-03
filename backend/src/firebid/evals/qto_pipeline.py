"""A tender's sheets through detection to QTO inputs, in memory (P1-07 tests and suites).

The platform does this through the database (`services.qto`); this does the same steps on
drawings directly, so the engine can be tested and evaluated without a stack: geometry and
views (P1-03), symbols typed as a person would confirm them (P1-04), detections and runs
(P1-05), each placed on its sheet, view, level and grid position.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from firebid.drawings import geometry
from firebid.drawings.detection import SheetDetections, detect, placed_from_legend, views_of
from firebid.drawings.grids import GridSystem
from firebid.drawings.legends import detect as detect_legends
from firebid.drawings.symbols import Candidates, best_match, clusters
from firebid.evals import synthetic
from firebid.evals.detection_calibration import type_of_factory
from firebid.qto import rules
from firebid.qto.model import Detection, Placement, Run, ScheduleRow, level_of


@dataclass
class Sheet:
    number: str
    document: Any  # an ezdxf drawing
    sheet_scale: str = "1:100"


def _level_of(number: str, view_level: str | None) -> str | None:
    return level_of(number, view_level)


def read(
    sheets: list[Sheet], described: dict[str, str] | None = None
) -> tuple[list[Detection], list[Run]]:
    """Every sheet's detections and runs, as the QTO engine takes them.

    `described` is the fixture's legend, each description with the object type a person
    would confirm it as; the synthetic network's when not given.
    """
    type_of = type_of_factory(described)
    tables = []
    references: list[Any] = []  # the tender's legend rows, wherever they are drawn
    for sheet in sheets:
        result = geometry_of(sheet)
        table = geometry.from_parquet(result["parquet"])
        page = (result["page"][0], result["page"][1], result["page"][2], result["page"][3])
        for legend in detect_legends(table, page):
            references.extend(row for row in legend.rows if row.symbol.signature is not None)
        tables.append((sheet, table, page, result.get("views")))

    detections: list[Detection] = []
    runs: list[Run] = []
    for sheet, table, page, source_views in tables:
        views = views_of(table, page, sheet.sheet_scale, source_views)
        placed, excluded = placed_from_legend(table, page, type_of)
        if not placed and references:
            placed = _placed_from(table, excluded, references, type_of)
        found = detect(table, placed, views, excluded=excluded)
        collect(sheet, found, detections, runs)
    return detections, runs


def revision_of(sheet: Sheet, described: dict[str, str] | None = None) -> dict[str, Any]:
    """One sheet as revision comparison takes it (P2-02): its elements, the grid lines of
    its largest gridded view, and its frame. The platform builds the same from stored
    detections, views and the sheet's page (`services.revision_compare`)."""
    from firebid.drawings import revision_diff

    result = geometry_of(sheet)
    table = geometry.from_parquet(result["parquet"])
    page = (result["page"][0], result["page"][1], result["page"][2], result["page"][3])
    views = views_of(table, page, sheet.sheet_scale, result.get("views"))
    placed, excluded = placed_from_legend(table, page, type_of_factory(described))
    found = detect(table, placed, views, excluded=excluded)
    elements = [
        revision_diff.Element(
            f"d{index}", item.kind, item.object_type, item.x, item.y, _compared(item.attributes)
        )
        for index, item in enumerate(found.objects)
        if item.kind != "drop"
    ]
    elements.extend(
        revision_diff.Element(
            f"r{run.run_id}",
            "run",
            f"pipe_{run.run_class}",
            (run.points[0][0] + run.points[-1][0]) / 2,
            (run.points[0][1] + run.points[-1][1]) / 2,
            {"dn": run.dn},
            tuple((p[0], p[1]) for p in run.points),
            run.length_mm,
        )
        for run in found.runs
    )
    gridded = [view for view in views if view.grid is not None]
    grid = max(
        gridded,
        key=lambda v: (v.extent[2] - v.extent[0]) * (v.extent[3] - v.extent[1]),
        default=None,
    )
    return {
        "elements": elements,
        "grid": revision_diff.grid_lines(grid.grid.as_json()) if grid and grid.grid else None,
        "frame": page,
    }


def _compared(attributes: dict[str, Any]) -> dict[str, Any]:
    """A detection's attributes that say what it is, not how it was drawn or derived."""
    return {k: v for k, v in attributes.items() if k not in ("run_class", "vertical_not_drawn")}


def words(sheets: list[Sheet]) -> tuple[list[ScheduleRow], list[rules.Parameter]]:
    """What the sheets say in words that takeoff uses (P2-01): equipment schedule rows, and
    floor-to-floor heights from a level schedule. The platform reads the same from stored
    text (`services.qto`)."""
    from firebid.drawings import equipment

    schedules: list[ScheduleRow] = []
    parameters: list[rules.Parameter] = []
    for sheet in sheets:
        spans = geometry.texts(geometry.from_parquet(geometry_of(sheet)["parquet"]))
        schedules.extend(
            ScheduleRow(
                tag=row.tag,
                values=dict(row.values),
                quote=row.quote,
                sheet_id=sheet.number,
                sheet_number=sheet.number,
                revision="R01",
                heading=row.heading,
            )
            for row in equipment.schedules(spans)
        )
        parameters.extend(rules.level_parameters(sheet.number, equipment.level_marks(spans)))
    return schedules, parameters


def geometry_of(sheet: Sheet) -> dict[str, Any]:
    from firebid.parsing.geometry_dxf import extract

    return extract(synthetic.dxf_bytes(sheet.document), None)


def _placed_from(table: Any, excluded: Any, references: list[Any], type_of: Any) -> list[Any]:
    """A sheet with no legend of its own: its symbols typed by the tender's legend."""
    from firebid.drawings.pipe_network import Placed

    placed = []
    signatures = Candidates([row.symbol.signature for row in references])
    for cluster in clusters(table, excluding=excluded):
        if cluster.signature is None:
            continue
        match = best_match(cluster.signature, signatures)
        if match is None:
            continue
        kind = type_of(references[match.index].description)
        if kind is None:
            continue
        cx, cy = cluster.centre
        placed.append(
            Placed(
                kind.object_type,
                kind.category,
                kind.measure,
                cx,
                cy,
                cluster.box,
                cluster.rotation,
                match.how,
                match.distance,
                attributes=dict(kind.attributes),
                rows=cluster.rows,
                description=references[match.index].description,
            )
        )
    return placed


def collect(
    sheet: Sheet, found: SheetDetections, detections: list[Detection], runs: list[Run]
) -> None:
    for index, item in enumerate(found.objects):
        view = item.view
        grid: GridSystem | None = view.grid if view else None
        at = Placement(
            sheet_id=sheet.number,
            sheet_number=sheet.number,
            revision="R01",
            document_id=sheet.number,
            view_id=str(view.id) if view else None,
            view_kind=_view_kind(found, view),
            level=_level_of(sheet.number, item.level),
            zone=None,
        )
        detections.append(
            Detection(
                id=f"{sheet.number}:d{index}",
                at=at,
                kind=item.kind,
                object_type=item.object_type,
                category=item.category,
                attributes=dict(item.attributes),
                x=item.x,
                y=item.y,
                grid_reference=item.grid_reference,
                grid_index=grid.index(item.x, item.y) if grid else None,
                confidence=item.raw_confidence,
                method=item.method,
                evidence=dict(item.evidence),
            )
        )
    for run in found.runs:
        view = run.view
        grid = view.grid if view else None
        points = tuple((p[0], p[1]) for p in run.points)
        runs.append(
            Run(
                id=f"{sheet.number}:r{run.run_id}",
                at=Placement(
                    sheet_id=sheet.number,
                    sheet_number=sheet.number,
                    revision="R01",
                    document_id=sheet.number,
                    view_id=str(view.id) if view else None,
                    view_kind=_view_kind(found, view),
                    level=_level_of(sheet.number, view.level if view else None),
                    zone=None,
                ),
                run_class=run.run_class,
                dn=run.dn,
                size_status=run.size_status,
                length_mm=round(run.length_mm) if run.length_mm is not None else None,
                points=points,
                grid_reference=run.grid_reference,
                grid_points=tuple(grid.index(x, y) if grid else None for x, y in points),
                confidence=run.raw_confidence,
                labels=tuple(label.text for label in run.labels),
                scale=view.denominator if view else None,
                system=run.system,
            )
        )


def _view_kind(found: SheetDetections, view: Any) -> str | None:
    return view.kind if view is not None else None
