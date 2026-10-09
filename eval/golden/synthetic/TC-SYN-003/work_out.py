"""Work out and write this folder's golden.json and manifest.json.

    cd backend && uv run python ../eval/golden/synthetic/TC-SYN-003/work_out.py

Kept so the figures can be reproduced and checked. Running it again records the commit it
was run at in manifest.json. package.md is written by hand and must be kept in step.

Reads only what the fixture generator says it draws (each symbol and pipe as it is placed)
and the measurement rules. Calls none of the platform's readers or takeoff code.
"""

from __future__ import annotations

import json
import math
import subprocess
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from firebid.evals import synthetic_systems as fx

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
FIRST = json.loads(
    (ROOT / "eval/golden/synthetic/TC-SYN-001/golden.json").read_text(encoding="utf-8")
)
(GRID,) = [s["expected_output"]["grid"] for s in FIRST["stages"] if s["stage_id"] == "STG-003"]

# --- What the generator draws, recorded as it draws it ---------------------------------------
placed: dict[str, list[dict[str, Any]]] = {}
piped: dict[str, list[dict[str, Any]]] = {}
_place, _pipe = fx._Sheet.place, fx._Sheet.pipe


def bay(x: float, y: float) -> str | None:
    def between(lines: dict[str, float], value: float) -> str | None:
        names = list(lines)
        for low, high in zip(names, names[1:], strict=False):
            if lines[low] <= value < lines[high]:
                return f"{low}-{high}"
        return None

    across, up = between(GRID["across_mm"], x), between(GRID["up_mm"], y)
    return f"{across}/{up}" if across and up else None


def place(self: Any, block: str, x: float, y: float, tag: str | None = None) -> None:
    _place(self, block, x, y, tag)
    one: dict[str, Any] = {"object_type": fx.TYPES[block], "block": block, "x_mm": float(x), "y_mm": float(y)}
    if tag:
        one["tag"] = tag
    if self.truth.measured:
        one["grid"] = bay(x, y)
    placed.setdefault(self.truth.number, []).append(one)


def pipe(self: Any, dn: int, x0: float, y0: float, x1: float, y1: float) -> None:
    _pipe(self, dn, x0, y0, x1, y1)
    piped.setdefault(self.truth.number, []).append(
        {"dn": dn, "from_mm": [float(x0), float(y0)], "to_mm": [float(x1), float(y1)]}
    )


fx._Sheet.place, fx._Sheet.pipe = place, pipe  # type: ignore[method-assign]
truths = {truth.number: truth for _, truth in fx.tender()}
fx._Sheet.place, fx._Sheet.pipe = _place, _pipe  # type: ignore[method-assign]

ORDER = (fx.PUMP_ROOM, fx.FLOOR, fx.SITE, fx.SCHEMATIC)
TITLES = {
    fx.PUMP_ROOM: ("FIRE PUMP ROOM LAYOUT PLAN", "1:100", "B1"),
    fx.FLOOR: ("LEVEL 3 WET RISER AND HOSE REEL LAYOUT PLAN", "1:100", "L03"),
    fx.SITE: ("SITE PLAN - EXTERNAL HYDRANT LAYOUT", "1:100", None),
    fx.SCHEMATIC: ("FIRE PROTECTION RISER SCHEMATIC", "NTS", None),
}
PLANS = (fx.PUMP_ROOM, fx.FLOOR, fx.SITE)
for number in ORDER:
    counted = [one for one in placed[number] if one["object_type"] != "pipe"]
    assert {t: sum(1 for o in counted if o["object_type"] == t) for t in truths[number].counts} == truths[number].counts

rules = {r["key"]: r["definition"] for r in yaml.safe_load((ROOT / "backend/config/measurement_rules.yaml").read_text(encoding="utf-8"))["rules"]}
spacing = rules["hanger_spacing"]["default_spacing_mm"]
riser_default = rules["riser_length"]["defaults"]


def hangers(length_mm: float, dn: int) -> int:
    gap = next(band["spacing_mm"] for band in spacing if dn <= band["up_to_dn"])
    return math.ceil(length_mm / gap)


def gap(dn: int) -> int:
    return next(band["spacing_mm"] for band in spacing if dn <= band["up_to_dn"])


lengths = {number: {str(dn): float(v) for dn, v in truths[number].lengths.items()} for number in ORDER}

# --- The takeoff: each thing once -------------------------------------------------------------
once: dict[str, int] = {}
for number in PLANS:
    for kind, count in truths[number].counts.items():
        once[kind] = once.get(kind, 0) + count
on_schematic_only = {k: v for k, v in truths[fx.SCHEMATIC].counts.items() if k not in once}
once.update(on_schematic_only)
summed: dict[str, int] = {}
for number in ORDER:
    for kind, count in truths[number].counts.items():
        summed[kind] = summed.get(kind, 0) + count
repeated = {kind: summed[kind] for kind in sorted(summed) if summed[kind] != once[kind]}

levels = [{"level": name, "ffl_m": text} for name, text in (*fx.LEVELS, fx.ROOF)]
heights = {
    low[0]: int((Decimal(high[1]) - Decimal(low[1])) * 1000)
    for low, high in zip((*fx.LEVELS,), (*fx.LEVELS[1:], fx.ROOF), strict=True)
}
assert heights["L03"] == fx.FLOOR_TO_FLOOR_L03

schedule = []
for tag, description, duty, flow, head, power in fx.SCHEDULE_ROWS:
    schedule.append(
        {
            "tag": tag,
            "description": description,
            "duty": None if duty == "-" else duty.lower(),
            "driver": description.split()[0].lower() if "FIRE PUMP" in description else None,
            "flow_l_s": flow,
            "flow_l_min": str(Decimal(flow) * 60).rstrip("0").rstrip("."),
            "head_m": head,
            "power_kw": power,
            "evidence": f"the row \"{' | '.join((tag, description, duty, flow, head, power))}\" of the {fx.SCHEDULE_HEADING} on {fx.PUMP_ROOM}",
        }
    )
by_tag = {row["tag"]: row for row in schedule}

equipment = []
for number in PLANS:
    for one in placed[number]:
        if one["object_type"] == "pipe":
            continue
        item: dict[str, Any] = {"item": one["object_type"], "quantity": 1, "unit": "no", "sheet": number, "level": TITLES[number][2]}
        if "tag" in one:
            item["tag"] = one["tag"]
            if one["tag"] in by_tag:
                row = by_tag[one["tag"]]
                item["attributes"] = {k: row[k] for k in ("duty", "driver", "flow_l_min", "head_m", "power_kw") if row[k]}
                item["attributes_from"] = row["evidence"]
        if one["object_type"] == "landing_valve":
            item["dn"] = 100
        equipment.append(item)
(inlet,) = [one for one in placed[fx.SCHEMATIC] if one["object_type"] == "breeching_inlet"]
equipment.append({"item": "breeching_inlet", "quantity": 1, "unit": "no", "sheet": fx.SCHEMATIC, "level": None, "tag": inlet["tag"], "why_from_the_schematic": "it is on no plan"})

SYSTEM = {fx.PUMP_ROOM: "fire pump suction and discharge, to the rising main", fx.FLOOR: "wet rising main", fx.SITE: "hydrant"}
pipe_items = [
    {"item": "pipe", "dn": int(dn), "quantity": length / 1000, "unit": "m", "sheet": number, "level": TITLES[number][2], "system": SYSTEM[number]}
    for number in PLANS
    for dn, length in sorted(lengths[number].items(), key=lambda kv: -int(kv[0]))
]
hanger_items = [
    {"item": "hanger", "dn": int(dn), "quantity": hangers(length, int(dn)), "unit": "no", "level": TITLES[number][2], "rule": "hanger_spacing", "calculation": f"ceil({length:.0f} / {gap(int(dn))})", "spacing_source": "company default: there is no specification"}
    for number in (fx.PUMP_ROOM, fx.FLOOR)
    for dn, length in sorted(lengths[number].items(), key=lambda kv: -int(kv[0]))
]

commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()

golden = {
    "test_case_id": "TC-SYN-003",
    "title": "Synthetic Phase 2 systems tender: a fire pump room, a typical floor, a site hydrant plan and a riser schematic",
    "status": "draft: not verified by a person",
    "units": "millimetres in the building's own coordinates (the DXF model space); lengths in mm unless a field says m",
    "business_rules_used": {"hanger_default_spacing_mm": spacing, "hanger_excluded_systems": rules["hanger_spacing"]["exclude_systems"], "riser_length_defaults": riser_default},
    "stages": [
        {
            "stage_id": "STG-001",
            "stage_name": "Document intake",
            "comparison_type": "EXACT",
            "expected_output": {
                "documents": [{"filename": f"{number}.dxf", "kind": "dxf", "state": "done", "sheets": 1} for number in ORDER],
                "sheets_total": len(ORDER),
                "refused": [],
                "unread_sheets": [],
            },
            "mandatory_fields": ["filename", "kind", "state", "sheets"],
            "allowed_variations": ["the order of documents"],
            "tolerance": None,
            "minimum_confidence": None,
            "severity_if_incorrect": "HIGH",
        },
        {
            "stage_id": "STG-002",
            "stage_name": "Title blocks and registers",
            "comparison_type": "EXACT",
            "expected_output": {
                "consultant": fx.DELTA.name,
                "project": "PROPOSED COMMERCIAL DEVELOPMENT AT MARINA BAY",
                "sheets": [
                    {"drawing_number": number, "title": TITLES[number][0], "revision": "R01", "date": "2026-05-01", "stated_scale": TITLES[number][1], "level": TITLES[number][2], "status": "current"}
                    for number in ORDER
                ],
                "superseded": [],
            },
            "mandatory_fields": ["drawing_number", "revision", "status"],
            "allowed_variations": ["the date shown as 01/05/2026 or 01.05.2026", "the site plan's level as none, SITE or EXTERNAL", "no level, or the levels the schematic names, for FP-SCH-002"],
            "tolerance": None,
            "minimum_confidence": None,
            "severity_if_incorrect": "HIGH",
        },
        {
            "stage_id": "STG-003",
            "stage_name": "Views and scale",
            "comparison_type": "EXACT",
            "expected_output": {
                "grid": GRID,
                "sheets": [
                    {"sheet": number, "views": [{"kind": "plan", "title": TITLES[number][0], "scale": "1:100", "scale_verdict": "verified", "verified_by": "its own dimensions", "measurable": True}]}
                    for number in PLANS
                ]
                + [{"sheet": fx.SCHEMATIC, "views": [{"kind": "schematic", "title": TITLES[fx.SCHEMATIC][0], "scale": "NTS", "scale_verdict": "not to scale", "measurable": False}]}],
                "schedules": [{"sheet": fx.PUMP_ROOM, "heading": fx.SCHEDULE_HEADING, "columns": [name for name, _ in fx.SCHEDULE_COLUMNS], "rows": len(fx.SCHEDULE_ROWS), "is_a_view": False}],
                "level_schedule": {"sheet": fx.SCHEMATIC, "levels": levels, "floor_to_floor_mm": heights, "note": "read from a sheet that is not to scale: its text is still evidence"},
            },
            "mandatory_fields": ["kind", "scale", "measurable"],
            "allowed_variations": ["a view's extent, within 5 mm on paper"],
            "tolerance": {"view_extent_paper_mm": 5},
            "minimum_confidence": None,
            "severity_if_incorrect": "HIGH",
        },
        {
            "stage_id": "STG-004",
            "stage_name": "Legends and symbols",
            "comparison_type": "COMPLETENESS",
            "expected_output": {
                "legend_sheets": list(PLANS),
                "heading": "LEGEND",
                "rows": [
                    {"ordinal": index, "block": s.block, "description": s.description, "letters": "", "object_type": s.object_type, "counted_as": "length" if s.object_type == "pipe" else "count"}
                    for index, s in enumerate(fx.DELTA.symbols)
                ],
                "the_same_legend_on_each_sheet": True,
                "sheets_without_a_legend": [fx.SCHEMATIC],
                "in_the_legend_and_not_installed": [],
                "symbols_no_legend_explains": [],
            },
            "mandatory_fields": ["description", "object_type"],
            "allowed_variations": ["the legend held once for the consultant or once for each sheet, with the same rows", "a row's state as proposed or confirmed: the object type is what is compared"],
            "tolerance": None,
            "minimum_confidence": None,
            "severity_if_incorrect": "CRITICAL",
        },
        {
            "stage_id": "STG-005",
            "stage_name": "Object detection",
            "comparison_type": "TOLERANCE",
            "expected_output": {
                "sheets": [
                    {
                        "sheet": number,
                        "counts": dict(sorted(truths[number].counts.items())),
                        "tags": dict(sorted(truths[number].tags.items())),
                        "not_counted": [{"block": o["block"], "object_type": "pipe", "x_mm": o["x_mm"], "y_mm": o["y_mm"], "reason": "the rising main is pipe, not a counted object"} for o in placed[number] if o["object_type"] == "pipe"],
                        "instances": [o for o in placed[number] if o["object_type"] != "pipe"],
                    }
                    for number in ORDER
                ]
            },
            "mandatory_fields": ["sheet", "counts"],
            "allowed_variations": ["an instance's position within 250 mm", "an untagged valve set with no tag"],
            "tolerance": {"count": 0, "position_mm": 250},
            "minimum_confidence": None,
            "severity_if_incorrect": "CRITICAL",
        },
        {
            "stage_id": "STG-006",
            "stage_name": "Pipe network",
            "comparison_type": "TOLERANCE",
            "expected_output": {
                "sheets": [
                    {"sheet": number, "measured": truths[number].measured, "length_mm_by_dn": lengths[number], "runs": piped.get(number, []), **({"system": SYSTEM[number]} if number in SYSTEM else {})}
                    for number in ORDER
                ],
                "sizes_read_from": "the annotation DN200, DN150, DN100 or DN50 written on or along each run",
                "exceptions": [f"{fx.SCHEMATIC} is not to scale: its rising main line is not measured"],
            },
            "mandatory_fields": ["sheet", "length_mm_by_dn"],
            "allowed_variations": ["a run measured to the centre of the pump, tank, riser or hydrant symbol it meets, where the drawing stops at the symbol's edge: up to 250 mm more at each such end (C4)", "the name of a system, where the pipe is put with the right equipment (C6)"],
            "tolerance": {"length_percent": 5},
            "minimum_confidence": None,
            "severity_if_incorrect": "HIGH",
        },
        {
            "stage_id": "STG-007",
            "stage_name": "Takeoff",
            "comparison_type": "TOLERANCE",
            "expected_output": {
                "duplicates": [
                    {"sheet": fx.SCHEMATIC, "repeats": fx.PUMP_ROOM, "what": "fire pumps FP-01 and FP-02, by their tags", "counted_from": fx.PUMP_ROOM},
                    {"sheet": fx.SCHEMATIC, "repeats": fx.FLOOR, "what": "the landing valve and hose reel of level L03", "counted_from": fx.FLOOR},
                    {"sheet": fx.SCHEMATIC, "repeats": "no sheet of the tender", "what": "the landing valves and hose reels of L01, L02, L04 and L05", "counted_from": "nowhere: see C1", "ambiguity": "C1"},
                ],
                "counted_once": dict(sorted(once.items())),
                "equipment": equipment,
                "pipe": pipe_items,
                "derived_items": [
                    {"item": "pipe", "dn": 100, "run": "riser", "quantity": heights["L03"] / 1000, "unit": "m", "level": "L03", "rule": "riser_length", "calculation": f"{heights['L03']} mm x 1 level", "evidence": f"L03 FFL +9.000 and L04 FFL +13.500 on {fx.SCHEMATIC}", "ambiguity": "C2"},
                    {"item": "pipe", "dn": 150, "run": "riser", "quantity": riser_default["floor_to_floor_mm"] / 1000, "unit": "m", "level": "B1", "rule": "riser_length", "calculation": f"{riser_default['floor_to_floor_mm']} mm x 1 level", "evidence": "the rule's default: the level schedule does not name B1", "ambiguity": "C2"},
                    {"item": "fitting", "fitting": "tee", "dn": "200x150", "quantity": 2, "unit": "no", "level": "B1", "rule": "fitting_tee", "calculation": "the fire pump spurs at x = 5000 and 8000 leave the DN200 suction header", "ambiguity": "C3"},
                    {"item": "fitting", "fitting": "tee", "dn": "150x150", "quantity": 1, "unit": "no", "level": "B1", "rule": "fitting_tee", "calculation": "the standby pump joins the discharge header at x = 8000", "ambiguity": "C3"},
                    {"item": "fitting", "fitting": "tee", "dn": "150x50", "quantity": 1, "unit": "no", "level": "B1", "rule": "fitting_tee", "calculation": "the jockey pump joins the discharge header at x = 11000", "ambiguity": "C3"},
                    {"item": "fitting", "fitting": "elbow", "dn": "200", "quantity": 1, "unit": "no", "level": "B1", "rule": "fitting_elbow", "calculation": "the suction header turns down to the jockey pump at (11000, 9000)", "ambiguity": "C3"},
                    {"item": "fitting", "fitting": "elbow", "dn": "150", "quantity": 1, "unit": "no", "level": "B1", "rule": "fitting_elbow", "calculation": "the duty pump's discharge turns into the header at (5000, 3000)", "ambiguity": "C3"},
                    {"item": "fitting", "fitting": "tee", "dn": "150x150", "quantity": 3, "unit": "no", "level": None, "rule": "fitting_tee", "calculation": "the hydrant spurs at x = 4000, 10000 and 16000 leave the DN150 main", "ambiguity": "C3"},
                    *hanger_items,
                ],
                "not_expected": [
                    {"item": "hanger", "where": fx.SITE, "why": "the hydrant main is buried: the rule leaves the hydrant system out"},
                    {"item": "sprinkler drop", "why": "there is no sprinkler"},
                    {"item": "seismic restraint", "why": "no specification requires it"},
                    {"item": "pipe to the valve sets, the test header or the pump control panel", "why": "none is drawn"},
                    {"item": "fitting", "fitting": "reducer", "why": "sizes change only at tees"},
                ],
                "status_of_every_derived_item": "to be confirmed (the rules are seeded defaults)",
            },
            "mandatory_fields": ["item", "quantity", "unit"],
            "allowed_variations": ["pipe lengths within 5%", "an item's description wording", "a pump's flow as 47.5 L/s or as 2850 L/min, where the schedule row is cited", "the derived fittings, until C3 is settled: report differences, not defects"],
            "tolerance": {"count": 0, "length_percent": 5},
            "minimum_confidence": None,
            "severity_if_incorrect": "CRITICAL",
        },
    ],
    "final_output": {
        "expected_result": {
            "scope": "stages 1 to 7: the takeoff. There is no specification, client bill or rate for this tender",
            "counted_once": dict(sorted(once.items())),
            "pipe_m_by_sheet_and_dn_drawn": {number: {dn: v / 1000 for dn, v in lengths[number].items()} for number in PLANS},
            "sum_of_the_sheets_before_duplicates": repeated,
            "pump_schedule": schedule,
        },
        "mandatory_fields": ["counted_once", "pipe_m_by_sheet_and_dn_drawn"],
        "allowed_variations": ["pipe lengths within 5%"],
    },
    "ambiguities": [f"C{n}" for n in range(1, 9)],
}

# --- The other reading of C8 (what a site plan draws is on a level named SITE) ---------------
# Keyed by the label the comparison gives each fact (`firebid.evals.golden`). A run that
# gives the expected value passes; one that gives this value is to settle with C8; one that
# gives neither has a defect.
OTHER_READING = {
    "STG-002": {f"{fx.SITE.lower()} / level": "SITE"},
    "STG-007": {
        "hydrant / no / level": "site",
        "pipe / DN150 / m / level": "b1, site",
        "fitting / tee / DN150x150 / no / level": "b1, site",
    },
}
for one in golden["stages"]:
    if one["stage_id"] in OTHER_READING:
        one["expected_output"]["alternatives"] = [
            {
                "ambiguity": "C8",
                "reading": "the site plan, and what is taken off from it, is on a level named SITE",
                "values": OTHER_READING[one["stage_id"]],
            }
        ]

manifest = {
    "test_case_id": "TC-SYN-003",
    "kind": "synthetic",
    "inputs": [
        {"filename": f"{number}.dxf", "generator": f"firebid.evals.synthetic_systems.{name}()", "written_by": "firebid.evals.synthetic.write_dxf"}
        for number, name in ((fx.PUMP_ROOM, "pump_room"), (fx.FLOOR, "floor_plan"), (fx.SITE, "site_plan"), (fx.SCHEMATIC, "riser_schematic"))
    ],
    "inputs_note": "The files are generated, not stored. A written DXF is not byte-identical from one run to the next (the file carries its own stamps), so the inputs are named by generator and commit, not by checksum.",
    "generator_commit": commit,
    "business_rules": ["backend/config/measurement_rules.yaml (version 1 of each rule, all 'to be confirmed')"],
    "bid_details": {"consultant": fx.DELTA.name, "project": "PROPOSED COMMERCIAL DEVELOPMENT AT MARINA BAY"},
    "drafted_on": "2026-10-08",
    "drafted_by": "Claude (Opus 5.5); see package.md part 10",
    "verified_by": None,
    "verified_on": None,
    "data_owner": None,
}

for name, data in (("golden.json", golden), ("manifest.json", manifest)):
    (OUT / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")

for number in ORDER:
    print(number, truths[number].counts, lengths[number], truths[number].tags)
    for one in placed[number]:
        print("   ", one)
print("once", once)
print("repeated", repeated)
print("heights", heights)
print("hangers", [(h["level"], h["dn"], h["quantity"], h["calculation"]) for h in hanger_items])
print(json.dumps(schedule, indent=1))
