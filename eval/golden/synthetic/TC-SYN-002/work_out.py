"""Work out and write this folder's golden.json and manifest.json.

    cd backend && uv run python ../eval/golden/synthetic/TC-SYN-002/work_out.py

Kept so the figures can be reproduced and checked. Running it again records the commit it
was run at in manifest.json. package.md is written by hand and must be kept in step.

Reads only the fixture generators' constants and the config files (the business rules).
Calls none of the platform's readers, takeoff, pricing, labour or risk code: every figure
is worked out here, by hand-written arithmetic in Decimal.
"""

from __future__ import annotations

import json
import math
import subprocess
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

from firebid.evals import synthetic_boq, synthetic_labour, synthetic_qto, synthetic_rates
from firebid.evals import synthetic_spec as spec
from firebid.evals.synthetic_network import NETWORK, TYPES

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "eval" / "golden" / "synthetic" / "TC-SYN-002"
CONFIG = ROOT / "backend" / "config"
FIRST = json.loads(
    (ROOT / "eval/golden/synthetic/TC-SYN-001/golden.json").read_text(encoding="utf-8")
)
SHEET = "FP-B1-201"
CENT = Decimal("0.01")
D = Decimal


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, ROUND_HALF_UP)


def money(value: Decimal) -> str:
    return str(cents(value))


def config(name: str) -> dict:
    return yaml.safe_load((CONFIG / name).read_text(encoding="utf-8"))


def first_stage(stage_id: str) -> dict:
    (found,) = [s for s in FIRST["stages"] if s["stage_id"] == stage_id]
    return found["expected_output"]


# --- What the generator draws (stages 5 and 6) ------------------------------------------------
_, drawn = synthetic_qto.car_park_plan(SHEET, spec.CAR_PARK_NOTES)
counts = synthetic_qto.counted(drawn)
(ga5,) = [s for s in first_stage("STG-005")["sheets"] if s["sheet"] == "FP-L05-201"]
(ga6,) = [s for s in first_stage("STG-006")["sheets"] if s["sheet"] == "FP-L05-201"]
placed = sorted((TYPES[s.block], s.x, s.y) for s in drawn.symbols if TYPES[s.block] != "pipe")
assert placed == sorted((i["object_type"], i["x_mm"], i["y_mm"]) for i in ga5["instances"])
assert counts == ga5["counts"]
lengths: dict[str, float] = {}
for pipe in drawn.pipes:
    lengths[str(pipe.dn)] = lengths.get(str(pipe.dn), 0.0) + math.hypot(
        pipe.x1 - pipe.x0, pipe.y1 - pipe.y0
    )
assert lengths == ga6["length_mm_by_dn"], (lengths, ga6["length_mm_by_dn"])
runs = [{"dn": p.dn, "from_mm": [p.x0, p.y0], "to_mm": [p.x1, p.y1]} for p in drawn.pipes]

# --- The rules used (recorded, and guarded by the test) ---------------------------------------
rules = {r["key"]: r for r in config("measurement_rules.yaml")["rules"]}
drop = rules["drop_length"]["definition"]["defaults"]
drop_mm = drop["branch_elevation_mm"] - drop["ceiling_height_mm"] - drop["sprinkler_setting_mm"]
riser = rules["riser_length"]["definition"]["defaults"]
riser_mm = riser["floor_to_floor_mm"] * riser["levels_served"]
spacing = rules["hanger_spacing"]["definition"]["default_spacing_mm"]
allowance = rules["allowance"]["definition"]["percent"]
boq_rules = config("boq.yaml")
labour_rules = config("labour.yaml")
(table,) = labour_rules["rate_tables"]
gst_percent = config("pricing.yaml")["gst"]["rates"][-1]["percent"]


def hangers(length_mm: float, dn: int) -> int:
    gap = next(band["spacing_mm"] for band in spacing if dn <= band["up_to_dn"])
    return math.ceil(length_mm / gap)


pendents = counts["sprinkler_pendent"]
heads = sum(n for kind, n in counts.items() if kind.startswith("sprinkler_"))
m = {dn: D(str(length)) / 1000 for dn, length in lengths.items()}  # metres by size, drawn
drop_m = D(pendents * drop_mm) / 1000
drop_all_m = D(heads * drop_mm) / 1000
riser_m = D(riser_mm) / 1000

# --- The bill (stage 9) and its prices (stage 10) ---------------------------------------------
rate_of = {(r.type, r.dn): r for r in synthetic_rates.RATES}
PRICED_ON = "2026-10-04"
DEADLINE = "2026-10-15"
VALIDITY_DAYS = 90
TENDER_END = "2027-01-13"

# (key, group, description, unit, quantity, rate key, allowance class, labour (type, dn), trade)
LINES = [
    (
        "heads_pendent",
        "Sprinkler heads",
        "Pendent sprinkler head",
        "nr",
        D(counts["sprinkler_pendent"]),
        ("sprinkler_pendent", ""),
        "sprinkler",
        ("sprinkler_pendent", ""),
    ),
    (
        "heads_upright",
        "Sprinkler heads",
        "Upright sprinkler head",
        "nr",
        D(counts["sprinkler_upright"]),
        ("sprinkler_upright", ""),
        "sprinkler",
        ("sprinkler", ""),
    ),
    (
        "heads_sidewall",
        "Sprinkler heads",
        "Sidewall sprinkler head",
        "nr",
        D(counts["sprinkler_sidewall"]),
        ("sprinkler_sidewall", ""),
        "sprinkler",
        ("sprinkler", ""),
    ),
    (
        "pipe_150_main",
        "Pipework",
        "150 mm pipe (main)",
        "m",
        m["150"],
        ("pipe", "150"),
        "pipe",
        ("pipe", "150"),
    ),
    (
        "pipe_100_main",
        "Pipework",
        "100 mm pipe (main)",
        "m",
        m["100"],
        ("pipe", "100"),
        "pipe",
        ("pipe", "100"),
    ),
    (
        "pipe_50_branch",
        "Pipework",
        "50 mm pipe (branch)",
        "m",
        m["50"],
        ("pipe", "50"),
        "pipe",
        ("pipe", "50"),
    ),
    (
        "pipe_25_drop",
        "Pipework",
        "25 mm pipe (sprinkler drop)",
        "m",
        drop_m,
        ("pipe", "25"),
        "pipe",
        ("pipe", ""),
    ),
    (
        "pipe_150_riser",
        "Pipework",
        "150 mm pipe (riser)",
        "m",
        riser_m,
        ("pipe", "150"),
        "pipe",
        ("pipe", "150"),
    ),
    (
        "reducer_150",
        "Fittings",
        "Reducer, 150 mm (drawn)",
        "nr",
        D(counts["fitting"]),
        ("fitting_reducer", "150"),
        "fitting",
        ("fitting", ""),
    ),
    (
        "tee_150x50",
        "Fittings",
        "Tee, DN150xDN50 (rule-derived)",
        "nr",
        D(3),
        ("fitting_tee", "150x50"),
        "fitting",
        ("fitting", ""),
    ),
    (
        "tee_100x50",
        "Fittings",
        "Tee, DN100xDN50 (rule-derived)",
        "nr",
        D(3),
        ("fitting_tee", "100x50"),
        "fitting",
        ("fitting", ""),
    ),
    (
        "gate_valve_150",
        "Valves and ancillaries",
        "150 mm gate valve",
        "nr",
        D(counts["gate_valve"]),
        ("gate_valve", "150"),
        "valve",
        ("gate_valve", "150"),
    ),
    (
        "check_valve_150",
        "Valves and ancillaries",
        "150 mm check valve",
        "nr",
        D(counts["check_valve"]),
        ("check_valve", "150"),
        "valve",
        None,
    ),
    (
        "hanger_50",
        "Hangers and supports",
        "Pipe hanger, DN50 (rule-derived)",
        "nr",
        D(hangers(lengths["50"], 50)),
        ("pipe_hanger", "50"),
        "support",
        None,
    ),
    (
        "hanger_100",
        "Hangers and supports",
        "Pipe hanger, DN100 (rule-derived)",
        "nr",
        D(hangers(lengths["100"], 100)),
        ("pipe_hanger", "100"),
        "support",
        None,
    ),
    (
        "hanger_150",
        "Hangers and supports",
        "Pipe hanger, DN150 (rule-derived)",
        "nr",
        D(hangers(lengths["150"], 150)),
        ("pipe_hanger", "150"),
        "support",
        None,
    ),
]
COMPONENT = {
    "Sprinkler heads": "materials",
    "Pipework": "materials",
    "Hangers and supports": "materials",
    "Fittings": "fittings",
    "Valves and ancillaries": "valves",
}

# Labour: the hourly cost of each grade and trade, from config/labour.yaml.
FOUR = D("0.0001")
hours_month = D(str(table["productive_hours_per_month"]))
share = D(str(table["overtime"]["share_percent"])) / 100
premium = D(str(table["overtime"]["premium"])) - 1
insurance = D(str(table["insurance_percent"])) / 100


def q4(value: Decimal) -> Decimal:
    return value.quantize(FOUR, ROUND_HALF_UP)


def own_cost(grade: str) -> dict[str, Decimal]:
    g = table["grades"][grade]
    wage = D(str(g["wage"]))
    return {
        "wage": q4(wage / hours_month),
        "levy": q4(D(str(g["levy"])) / hours_month),
        "accommodation": q4(D(str(g["accommodation"])) / hours_month),
        "transport": q4(D(str(g["transport"])) / hours_month),
        "insurance": q4(wage * insurance / hours_month),
        "overtime": q4(wage / hours_month * share * premium),
    }


supervisor = sum(own_cost(table["supervision"]["grade"]).values(), D(0))
supervision = q4(supervisor / D(str(table["supervision"]["ratio"])))
grade_hourly = {}
grade_lines = {}
for grade in ("skilled", "general"):
    parts = {**own_cost(grade), "supervision": supervision}
    grade_lines[grade] = {k: str(v) for k, v in parts.items()}
    grade_hourly[grade] = q4(sum(parts.values(), D(0)))
trade_hourly = {
    trade: q4(
        sum(
            (
                grade_hourly[g] * D(str(pc)) / 100
                for g, pc in table["trades"][trade]["crew"].items()
            ),
            D(0),
        )
    )
    for trade in ("pipefitter", "sprinkler_fitter")
}
productivity = {(str(e[0]), str(e[1])): e for e in synthetic_labour.ENTRIES}


def priced_lines(drop_quantity: Decimal) -> tuple[list[dict], dict]:
    bill, totals = [], {"materials": D(0), "fittings": D(0), "valves": D(0), "wastage": D(0)}
    labour_cost, labour_hours, unpriced = D(0), D(0), []
    for key, group, description, unit, quantity, rate_key, klass, labour_key in LINES:
        if key == "pipe_25_drop":
            quantity = drop_quantity
        rate = rate_of.get(rate_key)
        line: dict = {
            "line": key,
            "group": group,
            "description": description,
            "unit": unit,
            "quantity": float(quantity),
            "allowance_percent": allowance[klass],
        }
        partial = rate is not None and rate.brand
        if rate is None or partial:
            line["price_status"] = "proposed" if partial else "unpriced"
            line["unit_rate"] = line["amount"] = None
            if partial:
                line["proposed_rate"] = {
                    "rate": rate.rate,
                    "source": f"{rate.source_type} {rate.source_reference}",
                    "why_not_applied": f"the entry names the brand {rate.brand}; the line names none",
                }
            unpriced.append(key)
        else:
            amount = cents(quantity * D(rate.rate))
            line["price_status"] = "priced"
            line["unit_rate"] = rate.rate
            line["amount"] = str(amount)
            line["rate_source"] = f"{rate.source_type} {rate.source_reference}"
            line["rate_valid_until"] = rate.valid_until.isoformat() if rate.valid_until else None
            warn = []
            if rate.valid_until and rate.valid_until.isoformat() < PRICED_ON:
                warn.append("expired")
            if rate.valid_until and rate.valid_until.isoformat() < TENDER_END:
                warn.append("ends_before_tender_validity")
            line["warnings"] = warn
            totals[COMPONENT[group]] += amount
            if allowance[klass]:
                totals["wastage"] += quantity * D(allowance[klass]) / 100 * D(rate.rate)
        if labour_key is None:
            line["labour"] = None
        else:
            entry = productivity[labour_key]
            hours = cents(quantity * D(str(entry[5])))
            cost = cents(hours * trade_hourly[entry[6]])
            line["labour"] = {
                "hours_per_unit": str(entry[5]),
                "productivity_entry": entry[3],
                "productivity_source": f"{entry[7]}: {entry[8]}",
                "trade": entry[6],
                "hours": str(hours),
                "hourly_rate": str(trade_hourly[entry[6]]),
                "cost": str(cost),
            }
            labour_cost += cost
            labour_hours += hours
        bill.append(line)
    totals["wastage"] = cents(totals["wastage"])
    direct = (
        totals["materials"]
        + totals["fittings"]
        + totals["valves"]
        + totals["wastage"]
        + labour_cost
    )
    margin = cents(direct * D(8) / 100)
    total = direct + margin
    gst = cents(total * D(gst_percent) / 100)
    figures = {
        "materials": money(totals["materials"]),
        "fittings": money(totals["fittings"]),
        "valves": money(totals["valves"]),
        "equipment": "0.00",
        "wastage": money(totals["wastage"]),
        "labour": money(labour_cost),
        "labour_hours": str(labour_hours),
        "priced_bill": money(totals["materials"] + totals["fittings"] + totals["valves"]),
        "direct": money(direct),
        "cost": money(direct),
        "margin": money(margin),
        "total_excluding_gst": money(total),
        "gst": money(gst),
        "total_including_gst": money(total + gst),
        "unpriced_lines": unpriced,
    }
    return bill, figures


bill, figures = priced_lines(drop_m)
_, figures_all_heads = priced_lines(drop_all_m)
labour_cost = D(figures["labour"])
labour_hours = D(figures["labour_hours"])

# --- The client's bill against ours (stage 9) ------------------------------------------------
OURS = {
    "Sprinkler, pendent": "heads_pendent",
    "Sprinkler, upright": "heads_upright",
    "Sprinkler, sidewall": "heads_sidewall",
    "Pipe, DN150, main": "pipe_150_main",
    "Pipe, DN100, main": "pipe_100_main",
    "Pipe, DN50, branch": "pipe_50_branch",
    "Sprinkler drop, DN25 (vertical, not drawn)": "pipe_25_drop",
    "Riser, DN150 (vertical, not drawn)": "pipe_150_riser",
    "Gate valve, DN150": "gate_valve_150",
    "Check valve, DN150": "check_valve_150",
    "Fitting, DN150 (fitting reducer)": "reducer_150",
}
ours_quantity = {line["line"]: D(str(line["quantity"])) for line in bill}
limit = D(str(boq_rules["variance_threshold_percent"]))
client_lines = []
for letter, title, lines in synthetic_boq.SECTIONS:
    for line in lines:
        row: dict = {
            "item": line.item,
            "section": f"{letter} {title}",
            "description": line.description,
            "unit": line.unit,
            "client_quantity": float(line.quantity) if line.quantity is not None else None,
            "maps_to": OURS[line.maps_to] if line.maps_to else None,
            "generator_says": line.maps_to,
        }
        if line.maps_to:
            measured = ours_quantity[OURS[line.maps_to]]
            difference = (measured - line.quantity).quantize(D("0.001"))
            percent = (difference / line.quantity * 100).quantize(D("0.1"), ROUND_HALF_UP)
            row.update(
                measured_quantity=float(measured),
                difference=float(difference),
                variance_percent=float(percent),
                flagged=abs(percent) > limit,
            )
        elif line.unit == "sum":
            row.update(
                kind="provisional sum",
                client_amount=str(line.amount),
                flagged=False,
                expected="not mapped to a measured item; shown for a person to carry as an allowance",
            )
        else:
            row.update(
                measured_quantity=0,
                flagged=True,
                expected="nothing measured: no flow switch is installed on the drawing",
            )
        client_lines.append(row)
mapped = {row["maps_to"] for row in client_lines if row["maps_to"]}
ours_only = [line["line"] for line in bill if line["line"] not in mapped]

# --- Risk impacts (stage 11) ------------------------------------------------------------------
multipliers = {c["key"]: D(str(c["value"])) for c in labour_rules["multipliers"]["conditions"]}


def impact(key: str) -> dict:
    extra = multipliers[key] - 1
    return {
        "multiplier": key,
        "factor": str(multipliers[key]),
        "hours": str(cents(labour_hours * extra)),
        "cost": money(labour_cost * extra),
        "on": f"{labour_hours} man-hours, SGD {labour_cost}",
    }


clauses = spec.with_risks()
commit = subprocess.run(
    ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
).stdout.strip()

golden = {
    "test_case_id": "TC-SYN-002",
    "title": "Synthetic basement car park sprinkler tender: one plan, a specification, the client's bill, a rate list and a productivity list",
    "status": "draft: not verified by a person",
    "units": "millimetres in the building's own coordinates (the DXF model space); lengths in mm unless a field says m; money in SGD as text, exact to the cent",
    "scenario": {
        "priced_on": PRICED_ON,
        "submission_deadline": DEADLINE,
        "tender_validity_days": VALIDITY_DAYS,
        "tender_validity_ends": TENDER_END,
        "measurement_conventions": {k: v["default"] for k, v in boq_rules["conventions"].items()},
        "what_people_have_done": [
            "confirmed each of the eight legend rows as the legend describes it",
            "verified every takeoff item as proposed; G1 approved by the Senior Estimator",
            "built the company bill from the verified takeoff",
            "imported the rate list and the productivity list; priced the bill by rule",
            "entered a margin of 8% on cost (Senior Estimator)",
        ],
        "what_people_have_not_done": [
            "verified any specification attribute, obligation or issue: all are proposals",
            "confirmed any proposed rate match",
            "confirmed any labour multiplier",
            "resolved any checklist item, treated any risk, or issued any clarification",
            "carried the client's provisional sums into the bill",
            "decided G2, G3 or G4; nothing is frozen or submitted",
        ],
    },
    "business_rules_used": {
        "drop_length_defaults": drop,
        "riser_length_defaults": riser,
        "hanger_default_spacing_mm": spacing,
        "allowance_percent": allowance,
        "variance_threshold_percent": boq_rules["variance_threshold_percent"],
        "gst_percent": gst_percent,
        "labour_rate_table": {
            "effective_from": str(table["effective_from"]),
            "productive_hours_per_month": table["productive_hours_per_month"],
            "overtime": table["overtime"],
            "supervision": table["supervision"],
            "insurance_percent": table["insurance_percent"],
            "grades": table["grades"],
            "trades": {t: table["trades"][t] for t in ("pipefitter", "sprinkler_fitter")},
        },
        "labour_multipliers": {k: str(v) for k, v in multipliers.items()},
    },
    "stages": [
        {
            "stage_id": "STG-001",
            "stage_name": "Document intake",
            "comparison_type": "EXACT",
            "expected_output": {
                "documents": [
                    {
                        "filename": "FP-B1-201.dxf",
                        "kind": "dxf",
                        "document_type": "drawing",
                        "state": "done",
                        "sheets": 1,
                    },
                    {
                        "filename": "Particular Specification Fire Protection.docx",
                        "kind": "docx",
                        "document_type": "specification",
                        "state": "done",
                        "clauses": len(clauses),
                    },
                    {
                        "filename": "Bill of Quantities - Fire Sprinkler.xlsx",
                        "kind": "xlsx",
                        "document_type": "boq",
                        "state": "done",
                        "sheets_read": [synthetic_boq.BILL],
                        "sheets_not_read": [
                            {
                                "sheet": synthetic_boq.SUMMARY,
                                "why": "a collection of totals: no measured lines",
                            },
                            {
                                "sheet": synthetic_boq.LISTS,
                                "why": "hidden; the unit list behind a data validation",
                            },
                        ],
                    },
                ],
                "library_imports": [
                    {
                        "filename": "rates.xlsx",
                        "what": "rate library",
                        "entries": len(synthetic_rates.RATES),
                        "refused": 0,
                    },
                    {
                        "filename": "productivity.xlsx",
                        "what": "productivity library",
                        "entries": len(synthetic_labour.ENTRIES),
                        "refused": 0,
                    },
                ],
                "sheets_total": 1,
                "refused": [],
                "unread_sheets": [],
            },
            "mandatory_fields": ["filename", "document_type", "state"],
            "allowed_variations": [
                "the order of documents",
                "the specification's clause count where a heading with no text is or is not held as a clause (see STG-008)",
            ],
            "tolerance": None,
            "minimum_confidence": None,
            "severity_if_incorrect": "HIGH",
        },
        {
            "stage_id": "STG-002",
            "stage_name": "Title blocks and registers",
            "comparison_type": "EXACT",
            "expected_output": {
                "consultant": NETWORK.name,
                "project": "PROPOSED COMMERCIAL DEVELOPMENT AT MARINA BAY",
                "sheets": [
                    {
                        "drawing_number": SHEET,
                        "title": "BASEMENT 1 CAR PARK SPRINKLER LAYOUT PLAN",
                        "revision": "R01",
                        "date": "2026-05-01",
                        "stated_scale": "1:100",
                        "level": "B1",
                        "status": "current",
                    }
                ],
                "superseded": [],
                "specification": {
                    "title": "PARTICULAR SPECIFICATION FOR FIRE PROTECTION SERVICES",
                    "revision": "Rev B",
                    "status": "current",
                },
            },
            "mandatory_fields": ["drawing_number", "revision", "status", "level"],
            "allowed_variations": [
                "the date shown as 01/05/2026 or 01.05.2026",
                "the level shown as BASEMENT 1 where the code B1 is also held",
            ],
            "tolerance": None,
            "minimum_confidence": None,
            "severity_if_incorrect": "HIGH",
        },
        {
            "stage_id": "STG-003",
            "stage_name": "Views and scale",
            "comparison_type": "EXACT",
            "expected_output": {
                "grid": first_stage("STG-003")["grid"],
                "sheets": [
                    {
                        "sheet": SHEET,
                        "views": [
                            {
                                "kind": "plan",
                                "title": "BASEMENT 1 CAR PARK SPRINKLER LAYOUT PLAN",
                                "scale": "1:100",
                                "scale_verdict": "verified",
                                "verified_by": "its own dimensions",
                                "measurable": True,
                            }
                        ],
                    }
                ],
                "general_notes": list(spec.CAR_PARK_NOTES),
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
                **{
                    k: v
                    for k, v in first_stage("STG-004").items()
                    if k
                    in (
                        "heading",
                        "rows",
                        "in_the_legend_and_not_installed",
                        "symbols_no_legend_explains",
                    )
                },
                "legend_sheet": SHEET,
                "sheets_without_a_legend": [],
            },
            "mandatory_fields": ["description", "object_type"],
            "allowed_variations": [
                "REDUCER mapped to object type `fitting` with or without the attribute fitting=reducer",
                "a row's state as proposed or confirmed: the object type is what is compared",
            ],
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
                        "sheet": SHEET,
                        "counts": dict(sorted(counts.items())),
                        "not_counted": ga5["not_counted"],
                        "instances": ga5["instances"],
                    }
                ]
            },
            "mandatory_fields": ["sheet", "counts"],
            "allowed_variations": ["an instance's position within 250 mm"],
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
                    {"sheet": SHEET, "measured": True, "length_mm_by_dn": lengths, "runs": runs}
                ],
                "sizes_read_from": first_stage("STG-006")["sizes_read_from"],
                "exceptions": [],
            },
            "mandatory_fields": ["sheet", "length_mm_by_dn"],
            "allowed_variations": [
                "the main measured through each valve and the reducer (the bid's convention): DN150 then reads 9,050 to 9,500 mm and DN100 up to 8,500 mm; see B2"
            ],
            "tolerance": {"length_percent": 5},
            "minimum_confidence": None,
            "severity_if_incorrect": "HIGH",
        },
        {
            "stage_id": "STG-007",
            "stage_name": "Takeoff",
            "comparison_type": "TOLERANCE",
            "expected_output": {
                "level": "B1",
                "duplicates": [],
                "drawn_items": [
                    {
                        "item": "sprinkler_pendent",
                        "quantity": counts["sprinkler_pendent"],
                        "unit": "no",
                    },
                    {
                        "item": "sprinkler_upright",
                        "quantity": counts["sprinkler_upright"],
                        "unit": "no",
                    },
                    {
                        "item": "sprinkler_sidewall",
                        "quantity": counts["sprinkler_sidewall"],
                        "unit": "no",
                    },
                    {
                        "item": "gate_valve",
                        "dn": 150,
                        "quantity": counts["gate_valve"],
                        "unit": "no",
                    },
                    {
                        "item": "check_valve",
                        "dn": 150,
                        "quantity": counts["check_valve"],
                        "unit": "no",
                    },
                    {
                        "item": "fitting",
                        "fitting": "reducer",
                        "dn": "150x100",
                        "quantity": counts["fitting"],
                        "unit": "no",
                    },
                    {
                        "item": "pipe",
                        "dn": 150,
                        "run": "main",
                        "quantity": float(m["150"]),
                        "unit": "m",
                    },
                    {
                        "item": "pipe",
                        "dn": 100,
                        "run": "main",
                        "quantity": float(m["100"]),
                        "unit": "m",
                    },
                    {
                        "item": "pipe",
                        "dn": 50,
                        "run": "branch",
                        "quantity": float(m["50"]),
                        "unit": "m",
                    },
                ],
                "derived_items": [
                    {
                        "item": "pipe",
                        "dn": 25,
                        "run": "drop",
                        "quantity": float(drop_m),
                        "unit": "m",
                        "rule": "drop_length",
                        "calculation": f"{pendents} pendent heads x ({drop['branch_elevation_mm']} - {drop['ceiling_height_mm']} - {drop['sprinkler_setting_mm']}) mm = {pendents} x {drop_mm} mm",
                        "evidence": "no ceiling height is stated on the sheet: every input is the rule's default",
                        "ambiguity": "B1",
                    },
                    {
                        "item": "pipe",
                        "dn": 150,
                        "run": "riser",
                        "quantity": float(riser_m),
                        "unit": "m",
                        "rule": "riser_length",
                        "calculation": f"{riser['floor_to_floor_mm']} mm floor to floor x {riser['levels_served']} level served",
                        "ambiguity": "B3",
                    },
                    {
                        "item": "fitting",
                        "fitting": "tee",
                        "dn": "150x50",
                        "quantity": 3,
                        "unit": "no",
                        "rule": "fitting_tee",
                        "calculation": "branches at x = 3000, 6000 and 9000 leave the DN150 main",
                    },
                    {
                        "item": "fitting",
                        "fitting": "tee",
                        "dn": "100x50",
                        "quantity": 3,
                        "unit": "no",
                        "rule": "fitting_tee",
                        "calculation": "branches at x = 12000, 15000 and 18000 leave the DN100 main, which runs on to 19000",
                    },
                    {
                        "item": "hanger",
                        "dn": 50,
                        "quantity": hangers(lengths["50"], 50),
                        "unit": "no",
                        "rule": "hanger_spacing",
                        "calculation": "ceil(72000 / 3000)",
                        "spacing_source": "company default: the specification has no supports clause",
                    },
                    {
                        "item": "hanger",
                        "dn": 100,
                        "quantity": hangers(lengths["100"], 100),
                        "unit": "no",
                        "rule": "hanger_spacing",
                        "calculation": "ceil(8250 / 4000)",
                        "spacing_source": "company default: the specification has no supports clause",
                    },
                    {
                        "item": "hanger",
                        "dn": 150,
                        "quantity": hangers(lengths["150"], 150),
                        "unit": "no",
                        "rule": "hanger_spacing",
                        "calculation": "ceil(8050 / 4500)",
                        "spacing_source": "company default: the specification has no supports clause",
                    },
                ],
                "attributes": {
                    "expected": "not specified, for every pipe material, class, joining method and every sprinkler K-factor, temperature, response and finish",
                    "why": "the drawing's symbols state none, and no specification attribute has been verified by a person",
                    "a_defect_if": "a takeoff item carries a specification value (black steel, grooved, K80, chrome ...) while that attribute is still a proposal",
                },
                "not_expected": [
                    {"item": "fitting", "fitting": "reducer", "why": "drawn, so not derived again"},
                    {
                        "item": "fitting",
                        "fitting": "elbow",
                        "why": "no change of direction on plan",
                        "ambiguity": "B4",
                    },
                    {
                        "item": "grooved coupling",
                        "why": "clause 2.1.2 says grooved for 65 mm and above, but it is not verified, and the drawing's note says welded: nothing is derived from a proposal",
                    },
                    {"item": "seismic restraint", "why": "no clause requires it"},
                    {
                        "item": "flow_switch",
                        "why": "in the legend and in the client's bill; installed nowhere on the drawing",
                    },
                    {
                        "item": "test header",
                        "why": "required by clause 7.6; not drawn. It is an issue at stage 8, not a takeoff item",
                    },
                ],
                "status_of_every_derived_item": "to be confirmed (the rules are seeded defaults)",
            },
            "mandatory_fields": ["item", "quantity", "unit"],
            "allowed_variations": [
                "pipe lengths within 5%",
                "an item's description wording",
                f"sprinkler drops of {drop_all_m} m if every head, not only the pendents, is given one (B1): a difference to settle, not a defect, until B1 is decided",
            ],
            "tolerance": {"count": 0, "length_percent": 5},
            "minimum_confidence": None,
            "severity_if_incorrect": "CRITICAL",
        },
        {
            "stage_id": "STG-008",
            "stage_name": "Specification",
            "comparison_type": "COMPLETENESS",
            "expected_output": {
                "clauses": [
                    {"number": n, "heading": h, "has_text": bool(t)} for n, h, t in clauses
                ],
                "sections_that_are_fire_protection": ["1", "2", "3", "4", "6", "7", "8"],
                "sections_that_are_not": [
                    {
                        "section": "5",
                        "heading": "LOW VOLTAGE ELECTRICAL INSTALLATION",
                        "expected": "nothing is extracted from clause 5.1",
                    }
                ],
                "attributes": [
                    {
                        "system": e.system,
                        "attribute": e.attribute,
                        "value": e.value,
                        "clause": e.clause,
                        "dn_min": e.dn_min,
                        "dn_max": e.dn_max,
                        "condition": e.condition,
                        "state": "proposed",
                    }
                    for e in spec.EXPECTED
                ],
                "obligations": [
                    {"category": c, "clause": n, "quantities": q, "state": "proposed"}
                    for c, n, q in spec.EXPECTED_OBLIGATIONS
                ],
                "issues": [
                    {
                        "rule": rule,
                        "clause": clause,
                        "about": about,
                        "sheet": SHEET,
                        "state": "open",
                    }
                    for rule, clause, about in spec.SEEDED_ISSUES
                ],
                "issue_evidence": {
                    "conflict:pipe_material": 'clause 2.1.3 (galvanised in the basement car park) against the note "ALL SPRINKLER PIPEWORK TO BE BLACK STEEL" on FP-B1-201 R01',
                    "conflict:joining_method": 'clause 2.1.2 (65 mm and above grooved) against the note "PIPES DN65 AND ABOVE: WELDED JOINTS" on FP-B1-201 R01',
                    "missing_from_drawings": "clause 7.6 requires a flow test header; FP-B1-201 R01 shows none",
                    "missing_from_specification": "FP-B1-201 R01 shows 4 upright sprinklers; no clause mentions an upright sprinkler",
                    "ambiguous_clause 7.7": '"where required"',
                    "ambiguous_clause 7.8": '"or equal"',
                },
                "scope_matrix": {
                    "systems": ["sprinkler", "hose_reel", "hydrant"],
                    "interfaces_for_each_system": [
                        {"key": key, "status": status, "clause": clause}
                        for key, status, clause in spec.EXPECTED_INTERFACES
                    ],
                },
            },
            "mandatory_fields": ["category", "clause", "rule", "status"],
            "allowed_variations": [
                "an obligation's or an issue's wording",
                "an attribute's value in other words with the same meaning (screwed for threaded, roll-grooved for grooved)",
                "further rows for the hydrant system only (excavation), as unclear",
            ],
            "tolerance": None,
            "minimum_confidence": None,
            "severity_if_incorrect": "HIGH",
        },
        {
            "stage_id": "STG-009",
            "stage_name": "Bill of quantities",
            "comparison_type": "EXACT",
            "expected_output": {
                "our_bill": {
                    "section": "FIRE SPRINKLER INSTALLATION",
                    "template": "company_standard, version 1",
                    "lines": [
                        {
                            k: line[k]
                            for k in (
                                "line",
                                "group",
                                "description",
                                "unit",
                                "quantity",
                                "allowance_percent",
                            )
                        }
                        | ({"level": "B1"} if line["group"] == "Sprinkler heads" else {})
                        for line in bill
                    ],
                },
                "client_bill": {
                    "sheet": synthetic_boq.BILL,
                    "header_row": synthetic_boq.HEADER_ROW,
                    "lines": client_lines,
                },
                "in_ours_and_not_in_the_client_s": ours_only,
                "variance_rule": f"measured less client, as a percentage of the client's quantity to one decimal place; flagged beyond {limit}% either way",
                "flagged": [row["item"] for row in client_lines if row["flagged"]],
            },
            "mandatory_fields": [
                "item",
                "maps_to",
                "client_quantity",
                "measured_quantity",
                "flagged",
            ],
            "allowed_variations": [
                "the wording and the numbering of our lines",
                "a line's quantity to three decimal places",
                "hanger lines left out of the bill where the convention has hangers deemed included (B7)",
            ],
            "tolerance": {"quantity_percent": 5},
            "minimum_confidence": None,
            "severity_if_incorrect": "HIGH",
        },
        {
            "stage_id": "STG-010",
            "stage_name": "Pricing and labour",
            "comparison_type": "EXACT",
            "expected_output": {
                "lines": bill,
                "labour_rates": {
                    "table": f"{table['source']}, from {table['effective_from']}",
                    "supervisor_hourly": str(supervisor),
                    "grades": {
                        g: {"lines": grade_lines[g], "hourly": str(grade_hourly[g])}
                        for g in grade_lines
                    },
                    "trades": {t: str(v) for t, v in trade_hourly.items()},
                    "rounding": "each line of a grade's hourly cost to four decimal places, half up; hours and money to two",
                },
                "labour": {
                    "hours": figures["labour_hours"],
                    "cost": figures["labour"],
                    "lines_without_hours": [
                        line["line"] for line in bill if line["labour"] is None
                    ],
                    "multipliers_applied": [],
                },
                "build_up": {
                    "materials": figures["materials"],
                    "fittings": figures["fittings"],
                    "valves": figures["valves"],
                    "equipment": figures["equipment"],
                    "wastage": figures["wastage"],
                    "labour": figures["labour"],
                    "direct": figures["direct"],
                    "cost": figures["cost"],
                    "margin": {
                        "basis": "8% of cost",
                        "entered_by": "the Senior Estimator",
                        "amount": figures["margin"],
                    },
                    "not_set": [
                        "supervision",
                        "access_equipment",
                        "testing_commissioning",
                        "transport",
                        "subcontract",
                        "site_overheads",
                        "preliminaries",
                        "insurance",
                        "bonds",
                        "contingency",
                    ],
                    "total_excluding_gst": figures["total_excluding_gst"],
                    "gst_percent": gst_percent,
                    "gst": figures["gst"],
                    "total_including_gst": figures["total_including_gst"],
                },
                "unpriced_lines": figures["unpriced_lines"],
                "if_every_head_takes_a_drop": {
                    k: figures_all_heads[k]
                    for k in (
                        "materials",
                        "wastage",
                        "labour",
                        "labour_hours",
                        "direct",
                        "margin",
                        "total_excluding_gst",
                        "gst",
                        "total_including_gst",
                    )
                },
            },
            "mandatory_fields": ["line", "price_status", "unit_rate", "amount", "rate_source"],
            "allowed_variations": [
                "a labour cost one cent either side, from the order of rounding",
                "the figures under if_every_head_takes_a_drop, until B1 is decided",
            ],
            "tolerance": {"money": 0, "labour_hours_percent": 2},
            "minimum_confidence": None,
            "severity_if_incorrect": "CRITICAL",
        },
        {
            "stage_id": "STG-011",
            "stage_name": "Clarifications, risks and qualifications",
            "comparison_type": "COMPLETENESS",
            "expected_output": {
                "clarification_candidates": [
                    {
                        "from": "specification issue",
                        "about": about,
                        "rule": rule,
                        "clause": clause,
                        "sheet": SHEET,
                    }
                    | ({"needs_the_design_manager": True} if rule.startswith("conflict:") else {})
                    for rule, clause, about in spec.SEEDED_ISSUES
                ]
                + [
                    {
                        "from": "bill variance",
                        "client_item": row["item"],
                        "about": row["description"],
                        "client_quantity": row["client_quantity"],
                        "measured_quantity": row["measured_quantity"],
                    }
                    for row in client_lines
                    if row["flagged"]
                ],
                "checklist": {
                    "system": "sprinkler",
                    "items": [
                        {
                            "key": "pumps",
                            "status": "open",
                            "why": "no clause and no takeoff item settles it",
                        },
                        {
                            "key": "tanks",
                            "status": "open",
                            "why": "no clause and no takeoff item settles it",
                        },
                        {
                            "key": "breeching_inlets",
                            "status": "open",
                            "why": "no clause and no takeoff item settles it",
                        },
                        {
                            "key": "hydraulic_calculations",
                            "status": "included",
                            "evidence": "clause 6.6",
                        },
                        {"key": "shop_drawings", "status": "included", "evidence": "clause 6.6"},
                        {
                            "key": "testing_commissioning",
                            "status": "included",
                            "evidence": "clauses 6.1 and 6.3",
                        },
                        {"key": "authority_fsc", "status": "included", "evidence": "clause 6.7"},
                        {"key": "builders_works", "status": "excluded", "evidence": "clause 7.3"},
                        {"key": "power_supply", "status": "by_others", "evidence": "clause 7.1"},
                    ],
                    "decided_by_a_person": 0,
                },
                "risks": [
                    {
                        "kind": kind,
                        "family": "design responsibility",
                        "clauses": list(cited),
                        "proposed_treatment": "qualify" if kind == "design_and_build" else "price",
                        "impact": "not computed: for a person to enter",
                    }
                    for kind, cited in spec.EXPECTED_DESIGN_RISKS
                ]
                + [
                    {
                        "kind": "night_work",
                        "family": "execution",
                        "clauses": ["8.4"],
                        "proposed_treatment": "price",
                        "impact": impact("night_work"),
                    },
                    {
                        "kind": "occupied_building",
                        "family": "execution",
                        "clauses": ["8.5"],
                        "proposed_treatment": "price",
                        "impact": impact("occupied_building"),
                    },
                    {
                        "kind": "shutdown",
                        "family": "execution",
                        "clauses": ["8.6"],
                        "proposed_treatment": "price",
                        "impact": "not computed: no labour multiplier describes it",
                    },
                    {
                        "kind": "basement",
                        "family": "execution",
                        "evidence": "level B1 of FP-B1-201",
                        "proposed_treatment": "price",
                        "impact": impact("basement"),
                        "ambiguity": "B10",
                    },
                ],
                "risks_not_expected": [
                    {"kind": "work_at_height", "why": "no ceiling height above 3000 mm is stated"},
                    {"kind": "high_rise", "why": "one level"},
                    {"kind": "congested_ceilings", "why": "no clause or note says so"},
                    {"kind": "access", "why": "no clause or note says so"},
                ],
                "qualifications": {
                    "measurement_conventions": [
                        boq_rules["conventions"][k]["options"][
                            boq_rules["conventions"][k]["default"]
                        ]
                        for k in boq_rules["conventions"]
                    ],
                    "proposed_only_after": "a risk is treated as 'qualify' by a person: none is yet, so no qualification is proposed from a risk",
                },
            },
            "mandatory_fields": ["kind", "clauses", "key", "status"],
            "allowed_variations": [
                "wording",
                "two candidates about one subject grouped into one clarification",
                "an impact within 2%",
            ],
            "tolerance": {"impact_percent": 2},
            "minimum_confidence": None,
            "severity_if_incorrect": "HIGH",
        },
        {
            "stage_id": "STG-012",
            "stage_name": "Review pack and submission",
            "comparison_type": "EXACT",
            "expected_output": {
                "figures": {
                    k: figures[k]
                    for k in (
                        "priced_bill",
                        "direct",
                        "total_excluding_gst",
                        "gst",
                        "total_including_gst",
                    )
                },
                "unpriced_lines": len(figures["unpriced_lines"]),
                "open_items": {
                    "specification_issues": len(spec.SEEDED_ISSUES),
                    "checklist_items_open": 3,
                    "risks_untreated": 8,
                    "bill_variances_flagged": sum(1 for row in client_lines if row["flagged"]),
                    "rate_warnings": sum(1 for line in bill if line.get("warnings")),
                },
                "gates": {
                    "G1": "approved",
                    "G2": "not decided",
                    "G3": "not decided",
                    "G4": "not decided",
                },
                "ready_for_g3": False,
                "why_not_ready_for_g3": [
                    "G2 is not approved",
                    "three checklist items are open",
                    "eight risks are untreated",
                ],
                "submission": "none: nothing is frozen",
            },
            "mandatory_fields": ["figures", "gates", "ready_for_g3"],
            "allowed_variations": ["the pack's sections in another order"],
            "tolerance": {"money": 0},
            "minimum_confidence": None,
            "severity_if_incorrect": "CRITICAL",
        },
    ],
    "final_output": {
        "expected_result": {
            "scope": "stages 1 to 12, to an estimate under review. Gates G2 to G4 and the frozen submission are not exercised",
            "counted_once": {
                **{k: v for k, v in sorted(counts.items()) if k != "fitting"},
                "fitting_reducer": counts["fitting"],
            },
            "sprinklers_total": heads,
            "pipe_m_by_dn_drawn": {dn: float(v) for dn, v in m.items()},
            "total_excluding_gst": figures["total_excluding_gst"],
            "gst": figures["gst"],
            "total_including_gst": figures["total_including_gst"],
            "unpriced_lines": len(figures["unpriced_lines"]),
            "gates": {
                "G1": "approved",
                "G2": "not decided",
                "G3": "not decided",
                "G4": "not decided",
            },
        },
        "mandatory_fields": ["counted_once", "total_excluding_gst", "total_including_gst", "gates"],
        "allowed_variations": [
            "pipe lengths within 5%",
            "the totals under STG-010's if_every_head_takes_a_drop, until B1 is decided",
        ],
    },
    "ambiguities": [f"B{n}" for n in range(1, 12)],
}

manifest = {
    "test_case_id": "TC-SYN-002",
    "kind": "synthetic",
    "inputs": [
        {
            "filename": "FP-B1-201.dxf",
            "generator": "firebid.evals.synthetic_qto.car_park_plan('FP-B1-201', firebid.evals.synthetic_spec.CAR_PARK_NOTES)",
            "written_by": "firebid.evals.synthetic.write_dxf",
        },
        {
            "filename": "Particular Specification Fire Protection.docx",
            "generator": "firebid.evals.synthetic_spec.specification_docx(clauses=with_risks())",
        },
        {
            "filename": "Bill of Quantities - Fire Sprinkler.xlsx",
            "generator": "firebid.evals.synthetic_boq.client_boq().payload",
        },
        {
            "filename": "rates.xlsx",
            "generator": "firebid.evals.synthetic_rates.rate_list()",
            "what": "the organisation's rate library, not a tender document",
        },
        {
            "filename": "productivity.xlsx",
            "generator": "firebid.evals.synthetic_labour.productivity_list()",
            "what": "the organisation's productivity library, not a tender document",
        },
    ],
    "inputs_note": "The files are generated, not stored. A written DXF, Word document or workbook is not byte-identical from one run to the next (each carries its own stamps), so the inputs are named by generator and commit, not by checksum.",
    "generator_commit": commit,
    "business_rules": [
        "backend/config/measurement_rules.yaml (version 1 of each rule, all 'to be confirmed')",
        "backend/config/boq.yaml and boq_templates.yaml (the default conventions and the company standard template)",
        "backend/config/pricing.yaml (GST)",
        "backend/config/labour.yaml (the 2025 rate table; multipliers)",
        "backend/config/risk.yaml and scope_matrix.yaml",
    ],
    "bid_details": {
        "consultant": NETWORK.name,
        "project": "PROPOSED COMMERCIAL DEVELOPMENT AT MARINA BAY",
        "priced_on": PRICED_ON,
        "submission_deadline": DEADLINE,
        "tender_validity_days": VALIDITY_DAYS,
        "margin": "8% of cost, entered by the Senior Estimator",
    },
    "drafted_on": "2026-10-08",
    "drafted_by": "Claude (Opus 5.5); see package.md part 10",
    "verified_by": None,
    "verified_on": None,
    "data_owner": None,
}

OUT.mkdir(parents=True, exist_ok=True)
for name, data in (("golden.json", golden), ("manifest.json", manifest)):
    (OUT / name).write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )

print("clauses", len(clauses))
print("drop", drop_mm, drop_m, drop_all_m, "riser", riser_m)
print("grades", grade_hourly, "supervisor", supervisor, supervision, "trades", trade_hourly)
for line in bill:
    lab = line["labour"]
    print(
        f"{line['line']:<18}{line['quantity']:>7} {line['price_status']:<9}{line['unit_rate'] or '-':>8}{line['amount'] or '-':>10} {line.get('warnings', '')!s:<42}",
        (lab["hours"], lab["cost"]) if lab else "-",
    )
print(json.dumps(figures, indent=1))
print("all heads", json.dumps(figures_all_heads))
for row in client_lines:
    print(
        row["item"],
        row.get("client_quantity"),
        row.get("measured_quantity"),
        row.get("difference"),
        row.get("variance_percent"),
        row["flagged"],
        row["maps_to"],
    )
print("ours only", ours_only)
for key in ("night_work", "occupied_building", "basement"):
    print(impact(key))
