"""Fittings the drawing does not show, derived from the pipe network by rule (FR-QTO-04).

From the stored runs of each sheet (their ends are the network's nodes):

* a **tee** where three or more runs meet;
* a **reducer** where two runs of different sizes meet;
* an **elbow** where two runs meet at an angle, and at every turn within a run;
* **grooved couplings** on runs the specification says are grooved: one between each pair
  of random lengths, one at each end that meets a fitting, valve, tee or riser.

A fitting that is drawn (a detected reducer, a valve) takes precedence: nothing is derived
at its place. Every derived item is labelled rule-derived, with the rule and version and
what it was counted from.

Pure: runs, drawn symbols and rules in, fitting drafts out.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from firebid.qto import rules
from firebid.qto.model import Detection, ItemDraft, Run, SpecValue, key_of

NEAR_MM = 0.05  # two run ends this close on paper are one node


def _node(point: tuple[float, float]) -> tuple[float, float]:
    return (round(point[0] / NEAR_MM) * NEAR_MM, round(point[1] / NEAR_MM) * NEAR_MM)


def _direction(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    size = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
    return ((b[0] - a[0]) / size, (b[1] - a[1]) / size)


def _turn(first: tuple[float, float], second: tuple[float, float]) -> float:
    """Degrees between two directions of travel (0: straight on)."""
    dot = max(-1.0, min(1.0, first[0] * second[0] + first[1] * second[1]))
    return math.degrees(math.acos(dot))


def derive(
    runs: list[Run],
    drawn: list[Detection],
    rule_set: dict[str, rules.Rule],
    joining_of: Callable[[int | None], list[SpecValue]],
    allowance: rules.Rule | None,
    excluded_length: dict[str, int],
) -> list[ItemDraft]:
    found: dict[str, dict[str, Any]] = {}

    def add(
        kind: str,
        rule: rules.Rule,
        size: str,
        run: Run,
        where: tuple[float, float],
        count: int = 1,
        inputs: list[dict[str, Any]] | None = None,
    ) -> None:
        key = key_of("fitting", kind, size, run.at.level, run.at.zone)
        entry = found.setdefault(
            key,
            {
                "kind": kind,
                "rule": rule,
                "size": size,
                "run": run,
                "count": 0,
                "at": [],
                "inputs": [],
            },
        )
        entry["count"] += count
        entry.setdefault("sheets", {})[run.at.sheet_id] = {
            "sheet_id": run.at.sheet_id,
            "sheet_number": run.at.sheet_number,
            "revision": run.at.revision,
            "document_id": run.at.document_id,
            "view_id": run.at.view_id,
        }
        entry["at"].append(
            {
                "sheet": run.at.sheet_number,
                "x": round(where[0], 3),
                "y": round(where[1], 3),
                "count": count,
            }
        )
        if inputs:
            entry["inputs"].extend(inputs)

    by_sheet: dict[str, list[Run]] = defaultdict(list)
    for run in runs:
        by_sheet[run.at.sheet_id].append(run)
    # A fitting at one place is counted once, however many sheets show it: the general plan's
    # first, then the lower-numbered sheet's, as duplicates are kept (FR-QTO-08).
    seen: set[tuple[str | None, str, float, float]] = set()

    def first_time(kind: str, run: Run, where: tuple[float, float]) -> bool:
        index = _grid_at(run, where)
        if index is None:
            return True
        place = (run.at.level, kind, round(index[0] / 0.02) * 0.02, round(index[1] / 0.02) * 0.02)
        if place in seen:
            return False
        seen.add(place)
        return True

    ordered = sorted(
        by_sheet.items(),
        key=lambda item: (
            0 if item[1][0].at.view_kind in ("plan", None) else 1,
            item[1][0].at.sheet_number,
        ),
    )
    for sheet_id, sheet_runs in ordered:
        symbols = {
            _node((d.x, d.y))
            for d in drawn
            if d.at.sheet_id == sheet_id and d.category in ("valve", "device", "fitting", "pipe")
        }
        ends: dict[tuple[float, float], list[tuple[Run, tuple[float, float]]]] = defaultdict(list)
        for run in sheet_runs:
            if len(run.points) < 2:
                continue
            ends[_node(run.points[0])].append((run, _direction(run.points[0], run.points[1])))
            ends[_node(run.points[-1])].append((run, _direction(run.points[-1], run.points[-2])))

        for node, meeting in ends.items():
            if node in symbols:
                continue  # a drawn fitting, valve or riser is there: it takes precedence
            if len(meeting) >= 3 and "fitting_tee" in rule_set:
                if not first_time("tee", meeting[0][0], node):
                    continue
                sizes = sorted({r.dn for r, _ in meeting if r.dn}, reverse=True)
                size = "x".join(f"DN{s}" for s in sizes[:2]) or "size not determined"
                add("tee", rule_set["fitting_tee"], size, meeting[0][0], node)
            elif len(meeting) == 2:
                (a, da), (b, db) = meeting
                if not first_time("joint", a, node):
                    continue
                if a.dn and b.dn and a.dn != b.dn and "fitting_reducer" in rule_set:
                    big, small = max(a.dn, b.dn), min(a.dn, b.dn)
                    add("reducer", rule_set["fitting_reducer"], f"DN{big}xDN{small}", a, node)
                elif "fitting_elbow" in rule_set:
                    limit = float(rule_set["fitting_elbow"].definition.get("min_turn_degrees", 30))
                    # Directions point away from the node: straight on is 180 degrees apart.
                    if 180.0 - _turn(da, db) > limit:
                        add("elbow", rule_set["fitting_elbow"], f"DN{a.dn or '?'}", a, node)
        if "fitting_elbow" in rule_set:
            limit = float(rule_set["fitting_elbow"].definition.get("min_turn_degrees", 30))
            for run in sheet_runs:
                for before, here, after in zip(
                    run.points, run.points[1:], run.points[2:], strict=False
                ):
                    if _turn(_direction(before, here), _direction(here, after)) > limit:
                        add("elbow", rule_set["fitting_elbow"], f"DN{run.dn or '?'}", run, here)

        coupling_rule = rule_set.get("grooved_couplings")
        if coupling_rule is not None:
            for run in sheet_runs:
                joining = {v.value for v in joining_of(run.dn)}
                if "grooved" not in joining or run.length_mm is None:
                    continue
                connected = sum(
                    1
                    for end, next_point in (
                        (run.points[0], run.points[1]),
                        (run.points[-1], run.points[-2]),
                    )
                    if (_node(end) in symbols or len(ends[_node(end)]) >= 2)
                    and first_time(f"coupled end {_heading(end, next_point)}", run, end)
                )
                length = run.length_mm - excluded_length.get(run.id, 0)
                result = rules.couplings(coupling_rule, length, connected)
                if result.value:
                    add(
                        "grooved_coupling",
                        coupling_rule,
                        f"DN{run.dn}",
                        run,
                        run.points[0],
                        result.value,
                        [{"run": run.id, **result.as_json()}],
                    )

    drafts = []
    for key, entry in found.items():
        rule: rules.Rule = entry["rule"]
        lead: Run = entry["run"]
        label = {
            "tee": "Tee",
            "reducer": "Reducer",
            "elbow": "Elbow",
            "grooved_coupling": "Grooved coupling",
        }[entry["kind"]]
        drafts.append(
            ItemDraft(
                key=key,
                item_type=f"fitting_{entry['kind']}",
                classification="fitting",
                description=f"{label}, {entry['size']} (rule-derived: not drawn)",
                attributes={"size": {"value": entry["size"], "source": "pipe network"}},
                unit="no",
                net_quantity=Decimal(entry["count"]),
                length_mm=None,
                level=lead.at.level,
                zone=lead.at.zone,
                grid_from=None,
                grid_to=None,
                calculation_method="rule_derived",
                detection_method="rule",
                confidence=lead.confidence,
                members=[
                    {
                        "kind": "network",
                        "sheet": a["sheet"],
                        "x": a["x"],
                        "y": a["y"],
                        "count": a["count"],
                    }
                    for a in entry["at"]
                ],
                sources=sorted(entry["sheets"].values(), key=lambda x: x["sheet_number"]),
                rule={
                    "rule_key": rule.key,
                    "rule_version": rule.version,
                    "rule_status": rule.status,
                    "inputs": entry["inputs"]
                    or [
                        {
                            "name": "network nodes",
                            "value": entry["count"],
                            "source": f"pipe network on {lead.at.sheet_number}",
                        }
                    ],
                    "value": entry["count"],
                },
                allowance_percent=rules.allowance_percent(allowance, "fitting"),
                geometry=entry["at"],
            )
        )
    return drafts


def _heading(end: tuple[float, float], towards: tuple[float, float]) -> int:
    """The compass direction a run leaves a node in, to the nearest 45 degrees.

    Two runs ending at one node are two pipe ends, each with its own coupling; the same run
    drawn on two sheets is one.
    """
    dx, dy = _direction(end, towards)
    return round(math.degrees(math.atan2(dy, dx)) / 45) % 8 * 45


def _grid_at(run: Run, where: tuple[float, float]) -> tuple[float, float] | None:
    """The grid position of a point of a run (its end or a turn), if the view has a grid."""
    for point, index in zip(run.points, run.grid_points, strict=True):
        if abs(point[0] - where[0]) <= NEAR_MM and abs(point[1] - where[1]) <= NEAR_MM:
            return index
    return None
