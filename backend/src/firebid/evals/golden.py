"""A run's stage outputs against a Golden Reference Work Product Package (FR-LRN-01).

`docs/plan/TEST_STRATEGY.md` says what a package is and how it is compared. This is the
comparison: exact (type A), tolerance (C), evidence (D) and completeness (E), for stages 1 to
12. Semantic comparison (B) is not built: what is not compared is reported as not compared,
never as passed.

A **run** is a bid's stage outputs in the package's own shape: stage ID to that stage's
`expected_output` layout (`firebid-eval export-run` writes one from a bid). Each side is
turned into **facts**, one comparable value with a key that says what it is of: the revision
of a drawing, the count of a type on a sheet, the length at a size, an item's quantity.
The facts are then set against each other:

* a fact the reference has and the run has not is **missing**;
* one whose value differs, beyond its tolerance, is **wrong**;
* one the run has and the reference has not is **extra**: listed for review as a possible
  hallucination, and not scored.

A difference takes its stage's severity unless the fact is a lesser one (a title's wording,
a date). A difference on something the package records as an ambiguity is **to settle**:
shown with the ambiguity's ID, and neither a defect nor a pass. Where the package also
records what the other reading of the ambiguity gives (`alternatives` in a stage's expected
output), only that value is to settle: a third value is a defect.

**Evidence** is what a value cites: the sheet a legend row or a takeoff item is from, the
sheet of an issue, the clause of a scope row or a risk, the source of a rate or of a
productivity. It is compared where the package gives it in a form that can be compared (a
drawing number, a clause number, a source's name), and only for a value the run has: one that
is missing is reported once. A citation that differs is MEDIUM, and counts under evidence in
the score. Where a reviewer accepts another citation as equivalent, the package records it
(`equivalent_evidence`: what it is of, by its label, and the citations accepted), and a run
that gives one of those passes. Evidence a package writes as prose is not compared.

At the takeoff an item's **attributes** and its **system** are compared where the package
states them. An attribute is stated on an item (a pump's duty, flow and head), or for every
item of a kind that is to state none (`attributes.not_specified`: a pipe's material while the
specification is still a proposal). Items are compared by what they are, so two pumps of one
type are compared by the values they have between them, not pump by pump. An attribute an
item does not have is "not specified". A value where none is to be is CRITICAL (something
unverified reached the takeoff, counted as a business rule); any other attribute is HIGH; a
system is MEDIUM. A system is the same by its key or by its name (`wet_riser`, `wet rising
main`).

Pure: a package and a run in; differences, a score and a report out.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Comparison = Literal["EXACT", "SEMANTIC", "TOLERANCE", "EVIDENCE", "COMPLETENESS"]
Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
SEVERITIES: tuple[Severity, ...] = ("CRITICAL", "HIGH", "MEDIUM", "LOW")

# The score's dimensions and weights (test strategy, section 8).
WEIGHTS: dict[str, int] = {
    "data_extraction": 20,
    "intermediate_work_product": 20,
    "business_rule": 20,
    "calculation": 15,
    "evidence": 10,
    "completeness": 10,
    "final_output": 5,
}
DIMENSION_LABELS = {
    "data_extraction": "Data extraction accuracy",
    "intermediate_work_product": "Intermediate work product accuracy",
    "business_rule": "Business rule accuracy",
    "calculation": "Calculation accuracy",
    "evidence": "Evidence and traceability",
    "completeness": "Completeness",
    "final_output": "Final output quality",
}
NOT_BUILT = {
    "final_output": "stage 12 was not compared",
}


class Stage(BaseModel):
    model_config = ConfigDict(frozen=True)

    stage_id: str = Field(pattern=r"^STG-\d{3}$")
    stage_name: str = Field(min_length=1)
    comparison_type: Comparison
    expected_output: dict[str, Any]
    mandatory_fields: tuple[str, ...] = ()
    allowed_variations: tuple[str, ...] = ()
    tolerance: dict[str, float] | None = None
    minimum_confidence: float | None = None
    severity_if_incorrect: Severity


class Package(BaseModel):
    """`golden.json`: part 6 of a package."""

    model_config = ConfigDict(frozen=True, extra="allow")

    test_case_id: str = Field(min_length=1)
    title: str = ""
    status: str = ""
    stages: tuple[Stage, ...]
    final_output: dict[str, Any]
    ambiguities: tuple[str, ...] = ()

    def stage(self, stage_id: str) -> Stage | None:
        return next((one for one in self.stages if one.stage_id == stage_id), None)


def load(folder: Path) -> Package:
    return Package.model_validate_json((folder / "golden.json").read_text(encoding="utf-8"))


Run = dict[str, dict[str, Any]]


def load_run(path: Path) -> Run:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    stages: Run = loaded.get("stages", loaded)
    return stages


# --- Facts ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Fact:
    key: tuple[str, ...]
    value: Any
    dimension: str
    # None: exact. Otherwise the percentage either way a number may differ by.
    tolerance_percent: float | None = None
    # Or the amount either way: a cent, from the order of rounding.
    tolerance_absolute: float | None = None
    severity: Severity | None = None  # None: the stage's own
    ambiguity: str | None = None
    # What the other reading of the ambiguity gives, where the package records it.
    alternatives: tuple[Any, ...] = ()
    # For an item that has a size: the size, and what the item is without it. The same item
    # at another size is one difference (its size), not one item missing and another extra.
    size: str | None = None
    identity: tuple[str, ...] | None = None
    # The fact this one is about (an item's level is about the item). Where that one is
    # missing, or is there at another size, this one is not reported as well.
    of: tuple[str, ...] | None = None
    # For evidence: the other citations a reviewer accepts as equivalent.
    equivalents: tuple[Any, ...] = ()

    def label(self) -> str:
        return " / ".join(part for part in self.key if part)


def _text(value: Any) -> str:
    return " ".join(str(value if value is not None else "").split()).casefold()


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except TypeError, ValueError:
        return None


def _cited(value: Any) -> str:
    """A citation as it is compared: a sheet, a clause or a source, or several of them in
    order. `company_standard CS-2026` and `Company standard CS-2026` are the same source."""
    if isinstance(value, (list, tuple, set)):
        return ", ".join(sorted(found for found in (_cited(one) for one in value) if found))
    return _text(value).replace("_", " ")


NOT_SPECIFIED = "not specified"
# A system by its key, and by the name the platform's pages and the requirements give it.
SYSTEM_NAMES = {"wet riser": "wet rising main", "dry riser": "dry rising main"}


def _system(value: Any) -> str:
    name = _cited(value).removesuffix(" system")
    return SYSTEM_NAMES.get(name, name)


def _stated(items: list[dict[str, Any]], name: str) -> str:
    """The values the items of one kind have for an attribute, between them."""
    found = (
        one.get(name) if isinstance(one := item.get("attributes"), dict) else None for item in items
    )
    return ", ".join(sorted({_cited(value) or NOT_SPECIFIED for value in found}))


def _evidence(key: tuple[str, ...], of: tuple[str, ...], cited: Any) -> list[Fact]:
    """What the value at `of` cites, where it cites anything."""
    value = _cited(cited)
    return [Fact(key, value, "evidence", severity="MEDIUM", of=of)] if value else []


def _intake(out: dict[str, Any], _: Stage) -> list[Fact]:
    facts = []
    for document in out.get("documents", []):
        name = _text(document.get("filename"))
        for name_of, severity in (("kind", None), ("sheets", None), ("state", None)):
            if name_of in document:
                facts.append(
                    Fact((name, name_of), document[name_of], "data_extraction", severity=severity)
                )
    return facts


def _register(out: dict[str, Any], _: Stage) -> list[Fact]:
    lesser: dict[str, Severity | None] = {
        "revision": None,
        "status": None,
        "level": "MEDIUM",
        "stated_scale": "MEDIUM",
        "title": "LOW",
        "date": "LOW",
    }
    facts = []
    for sheet in out.get("sheets", []):
        number = _text(sheet.get("drawing_number"))
        facts.append(Fact((number, "registered"), True, "data_extraction"))
        for name, severity in lesser.items():
            if name in sheet:
                value = sheet[name]
                facts.append(
                    Fact(
                        (number, name),
                        _text(value) if isinstance(value, str) else value,
                        "data_extraction",
                        severity=severity,
                    )
                )
    return facts


def _views(out: dict[str, Any], _: Stage) -> list[Fact]:
    facts = []
    for sheet in out.get("sheets", []):
        number = _text(sheet.get("sheet"))
        views = sheet.get("views", [])
        facts.append(Fact((number, "views"), len(views), "intermediate_work_product"))
        for index, view in enumerate(views):
            where = (number, f"view {index + 1}")
            for name in ("kind", "scale", "measurable", "scale_verdict"):
                if name in view:
                    value = view[name]
                    facts.append(
                        Fact(
                            (*where, name),
                            _text(value) if isinstance(value, str) else value,
                            "intermediate_work_product",
                            # An ambiguity about a view is about whether it may be measured,
                            # not about what kind of view it is.
                            ambiguity=view.get("ambiguity")
                            if name in ("measurable", "scale_verdict")
                            else None,
                        )
                    )
    return facts


def _legend(out: dict[str, Any], _: Stage) -> list[Fact]:
    facts = [
        Fact((_text(row.get("description")), "object type"), row.get("object_type"), "completeness")
        for row in out.get("rows", [])
    ]
    # The sheets a row is on: its own, or the legend's where the rows name none.
    of_all = out.get("legend_sheets") or [out.get("legend_sheet")]
    sheets: dict[str, set[str]] = {}
    for row in out.get("rows", []):
        on = [row["sheet"]] if row.get("sheet") else of_all
        sheets.setdefault(_text(row.get("description")), set()).update(_cited(one) for one in on)
    for description, found in sheets.items():
        facts += _evidence((description, "sheet"), (description, "object type"), found)
    return facts


def _objects(out: dict[str, Any], _: Stage) -> list[Fact]:
    return [
        Fact((_text(sheet.get("sheet")), kind, "count"), count, "intermediate_work_product")
        for sheet in out.get("sheets", [])
        for kind, count in sheet.get("counts", {}).items()
    ]


def _pipe(out: dict[str, Any], stage: Stage) -> list[Fact]:
    percent = (stage.tolerance or {}).get("length_percent", 5.0)
    facts = []
    for sheet in out.get("sheets", []):
        number = _text(sheet.get("sheet"))
        if "measured" in sheet:
            facts.append(
                Fact(
                    (number, "measured"),
                    sheet["measured"],
                    "intermediate_work_product",
                    ambiguity=sheet.get("ambiguity"),
                )
            )
        for dn, length in sheet.get("length_mm_by_dn", {}).items():
            facts.append(
                Fact(
                    (number, f"DN{dn}", "length mm"),
                    length,
                    "intermediate_work_product",
                    tolerance_percent=percent,
                    ambiguity=sheet.get("ambiguity"),
                )
            )
    return facts


def _takeoff(out: dict[str, Any], stage: Stage) -> list[Fact]:
    percent = (stage.tolerance or {}).get("length_percent", 5.0)
    # A reference that does not say which of its drawn pipe is main and which branch is
    # compared by size alone: both sides are read without the run.
    runs_stated = any(
        item.get("run")
        for part in ("drawn_items", "pipe")
        for item in stage.expected_output.get(part, [])
        if item.get("item") == "pipe"
    )
    # Levels are compared where the reference states them: for the whole takeoff, or item
    # by item. An item it gives no level (a site main, a schematic's inlet) is to have none.
    parts = ("drawn_items", "equipment", "pipe", "derived_items")
    reference = stage.expected_output
    levels_stated = "level" in reference or any(
        "level" in item for part in parts for item in reference.get(part, [])
    )
    totals, levels, shares, sheets, items = _taken_off(out, out.get("level"), runs_stated)
    expected = _taken_off(reference, reference.get("level"), runs_stated)
    # Where the reference has an item on more than one level, how much of it is on each is
    # compared as well: a level's labour multiplier is on that level's share. Only the levels
    # the reference names, so a share is never missing or extra: the rest is in the total.
    split = {key: sorted(found) for key, found in expected[1].items() if len(found) > 1}
    facts = [
        Fact(
            (*key, unit),
            quantity,
            "business_rule" if derived else "calculation",
            tolerance_percent=percent if unit == "m" else None,
            ambiguity=ambiguity,
            size=key[2] or None,
            identity=(key[0], key[1], key[3], unit),
        )
        for key, (quantity, unit, derived, ambiguity) in totals.items()
    ]
    if levels_stated:
        facts += [
            Fact(
                (*key, unit, "level"),
                ", ".join(sorted(levels[key])),
                "intermediate_work_product",
                severity="HIGH",
                of=(*key, unit),
            )
            for key, (_, unit, _, _) in totals.items()
        ]
        facts += [
            Fact(
                (*key, unit, f"on {level}"),
                shares[key].get(level, 0.0),
                "calculation",
                tolerance_percent=percent if unit == "m" else None,
                severity="HIGH",
                ambiguity=ambiguity,
                of=(*key, unit),
            )
            for key, (_, unit, _, ambiguity) in totals.items()
            for level in split.get(key, [])
        ]
    # The sheets an item is taken off from, where it names any.
    for key, (_, unit, _, _) in totals.items():
        facts += _evidence((*key, unit, "sheet"), (*key, unit), sheets.get(key))
    # Attributes and systems, where the reference states them: on an item, or for every item
    # of a kind that is to state none.
    none = reference.get("attributes")
    none = none.get("not_specified", {}) if isinstance(none, dict) else {}
    for key, (_, unit, _, _) in totals.items():
        named = expected[4].get(key, [])
        names = {
            name
            for item in named
            if isinstance(item.get("attributes"), dict)
            for name in item["attributes"]
        }
        for kind, of_kind in none.items():
            if key[0] == kind or key[0].startswith(f"{kind}_"):
                names.update(of_kind)
        for name in sorted(names):
            # A value where none is to be: something unverified reached the takeoff, which
            # is a rule broken and not a value misread.
            unstated = _stated(named, name) == NOT_SPECIFIED
            facts.append(
                Fact(
                    (*key, unit, name),
                    _stated(items[key], name),
                    "business_rule" if unstated else "calculation",
                    severity="CRITICAL" if unstated else "HIGH",
                    of=(*key, unit),
                )
            )
        if any(item.get("system") for item in named):
            systems = sorted({_system(item.get("system")) for item in items[key]} - {""})
            facts.append(
                Fact(
                    (*key, unit, "system"),
                    ", ".join(systems) or "none stated",
                    "intermediate_work_product",
                    severity="MEDIUM",
                    of=(*key, unit),
                )
            )
    return facts


Totals = dict[tuple[str, ...], tuple[float, str, bool, str | None]]


def _taken_off(
    out: dict[str, Any], of_all: Any, runs_stated: bool
) -> tuple[
    Totals,
    dict[tuple[str, ...], set[str]],
    dict[tuple[str, ...], dict[str, float]],
    dict[tuple[str, ...], set[str]],
    dict[tuple[str, ...], list[dict[str, Any]]],
]:
    """A takeoff's items by what they are: the quantity of each, the levels it is on, how
    much of it is on each level, the sheets it is from, and the items themselves. `of_all`
    is the level of an item that states none."""
    items: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    levels: dict[tuple[str, ...], set[str]] = {}
    shares: dict[tuple[str, ...], dict[str, float]] = {}
    sheets: dict[tuple[str, ...], set[str]] = {}
    totals: Totals = {}
    for part, derived in (
        ("drawn_items", False),
        ("equipment", False),
        ("pipe", False),
        ("derived_items", True),
    ):
        for item in out.get(part, []):
            size = str(item["dn"]) if item.get("dn") else ""
            if item.get("fitting") == "tee" and size and "x" not in size:
                size = f"{size}x{size}"  # an equal tee, however it is written
            run = str(item.get("run") or "")
            if item.get("item") == "pipe" and not derived and not runs_stated:
                run = ""
            key = (
                str(item.get("item", "")),
                str(item.get("fitting") or ""),
                f"DN{size}" if size else "",
                run,
            )
            quantity, unit, _, ambiguity = totals.get(
                key, (0.0, str(item.get("unit", "")), 0, None)
            )
            amount = float(item.get("quantity", 0))
            totals[key] = (
                quantity + amount,
                unit,
                derived,
                item.get("ambiguity") or ambiguity,
            )
            level = _text(item.get("level", of_all)) or "no level"
            levels.setdefault(key, set()).add(level)
            on = shares.setdefault(key, {})
            on[level] = on.get(level, 0.0) + amount
            sheets.setdefault(key, set()).update(
                _cited(one) for one in item.get("sheets") or [item.get("sheet")] if one
            )
            items.setdefault(key, []).append(item)
    return totals, levels, shares, sheets, items


def _amount(value: Any) -> float | None:
    number = _number(value)
    return None if number is None else round(number, 2)


def _listed(values: Any) -> str:
    return ", ".join(sorted(str(value) for value in values or []))


def _specification(out: dict[str, Any], _: Stage) -> list[Fact]:
    facts = [Fact(("clauses",), len(out.get("clauses", [])), "data_extraction")]
    for one in out.get("attributes", []):
        sizes = (one.get("dn_min"), one.get("dn_max"))
        facts.append(
            Fact(
                (
                    "attribute",
                    str(one.get("system")),
                    str(one.get("attribute")),
                    _text(one.get("value")),
                    f"clause {one.get('clause')}",
                    f"DN {sizes[0] or ''} to {sizes[1] or ''}" if any(sizes) else "",
                ),
                one.get("state"),
                "data_extraction",
            )
        )
    for one in out.get("obligations", []):
        facts.append(
            Fact(
                ("obligation", str(one.get("category")), f"clause {one.get('clause')}"),
                json.dumps(one.get("quantities") or {}, sort_keys=True),
                "data_extraction",
            )
        )
    for one in out.get("issues", []):
        clause = one.get("clause")
        issue = ("issue", str(one.get("rule")), f"clause {clause}" if clause else "no clause")
        facts.append(Fact(issue, one.get("state"), "business_rule"))
        facts += _evidence((*issue, "sheet"), issue, one.get("sheet"))
    matrix = out.get("scope_matrix", {})
    rows = matrix.get("rows") or [
        {"system": system, **interface}
        for system in matrix.get("systems", [])
        for interface in matrix.get("interfaces_for_each_system", [])
    ]
    for row in rows:
        scope = ("scope", str(row.get("system")), str(row.get("key")))
        facts.append(Fact(scope, row.get("status"), "business_rule"))
        facts += _evidence((*scope, "clause"), scope, row.get("clause"))
    return facts


def _bill(out: dict[str, Any], stage: Stage) -> list[Fact]:
    percent = (stage.tolerance or {}).get("quantity_percent", 5.0)
    facts = []
    for line in out.get("our_bill", {}).get("lines", []):
        where = ("line", str(line.get("line")))
        measured = line.get("unit") == "m"
        facts.append(
            Fact(
                (*where, "quantity"),
                line.get("quantity"),
                "calculation",
                tolerance_percent=percent if measured else None,
                ambiguity=line.get("ambiguity"),
            )
        )
        for name in ("unit", "allowance_percent"):
            if name in line:
                facts.append(Fact((*where, name), line[name], "intermediate_work_product"))
    for row in out.get("client_bill", {}).get("lines", []):
        where = ("client", str(row.get("item")))
        for name, dimension in (
            ("client_quantity", "data_extraction"),
            ("maps_to", "business_rule"),
            ("measured_quantity", "calculation"),
            ("variance_percent", "calculation"),
            ("flagged", "business_rule"),
        ):
            if name in row:
                facts.append(Fact((*where, name), row[name], dimension))
    facts += [
        Fact(("in ours and not in the client's", str(name)), True, "business_rule")
        for name in out.get("in_ours_and_not_in_the_client_s", [])
    ]
    return facts


def _pricing(out: dict[str, Any], stage: Stage) -> list[Fact]:
    hours_percent = (stage.tolerance or {}).get("labour_hours_percent", 2.0)
    facts = []
    for line in out.get("lines", []):
        where = ("line", str(line.get("line")))
        priced = (*where, "price_status")
        facts.append(Fact(priced, line.get("price_status"), "business_rule"))
        # A price's source and how long it holds; the hours' entry and where it is from.
        for name, cited in (
            ("rate source", line.get("rate_source")),
            ("rate valid until", line.get("rate_valid_until")),
            ("productivity entry", (line.get("labour") or {}).get("productivity_entry")),
            ("productivity source", (line.get("labour") or {}).get("productivity_source")),
        ):
            facts += _evidence((*where, name), priced, cited)
        for name in ("unit_rate", "amount"):
            facts.append(Fact((*where, name), _amount(line.get(name)), "calculation"))
        if "warnings" in line:
            facts.append(Fact((*where, "warnings"), _listed(line["warnings"]), "business_rule"))
        labour = line.get("labour") or {}
        facts.append(
            Fact(
                (*where, "labour hours"),
                _amount(labour.get("hours")),
                "calculation",
                tolerance_percent=hours_percent,
            )
        )
        facts.append(
            Fact(
                (*where, "labour cost"),
                _amount(labour.get("cost")),
                "calculation",
                tolerance_absolute=0.01,
            )
        )
    labour = out.get("labour", {})
    if labour:
        facts += [
            Fact(
                ("labour", "hours"),
                _amount(labour.get("hours")),
                "calculation",
                tolerance_percent=hours_percent,
            ),
            Fact(
                ("labour", "cost"),
                _amount(labour.get("cost")),
                "calculation",
                tolerance_absolute=0.01,
            ),
            Fact(
                ("labour", "lines without hours"),
                _listed(labour.get("lines_without_hours")),
                "business_rule",
            ),
        ]
    built = out.get("build_up", {})
    for name, value in built.items():
        if name == "not_set":
            facts.append(Fact(("build-up", "not set"), _listed(value), "completeness"))
        elif name == "margin":
            amount = value.get("amount") if isinstance(value, dict) else value
            facts.append(Fact(("build-up", "margin"), _amount(amount), "calculation"))
        elif name != "gst_percent" or value is not None:
            facts.append(Fact(("build-up", name), _amount(value), "calculation"))
    if "unpriced_lines" in out:
        facts.append(Fact(("unpriced lines",), _listed(out["unpriced_lines"]), "business_rule"))
    return facts


def _risks(out: dict[str, Any], stage: Stage) -> list[Fact]:
    percent = (stage.tolerance or {}).get("impact_percent", 2.0)
    facts = []
    for one in out.get("clarification_candidates", []):
        if one.get("rule"):
            clause = one.get("clause")
            about = f"{one['rule']}, " + (f"clause {clause}" if clause else "no clause")
        else:
            about = str(one.get("client_item") or one.get("our_line") or one.get("ref"))
        facts.append(Fact(("candidate", str(one.get("from")), about), True, "completeness"))
    checklist = out.get("checklist", {})
    for item in checklist.get("items", []):
        system = str(item.get("system") or checklist.get("system"))
        facts.append(
            Fact(("checklist", system, str(item.get("key"))), item.get("status"), "business_rule")
        )
    if "decided_by_a_person" in checklist:
        facts.append(
            Fact(
                ("checklist", "decided by a person"),
                checklist["decided_by_a_person"],
                "business_rule",
            )
        )
    for risk in out.get("risks", []):
        where = ("risk", str(risk.get("kind")))
        treated = (*where, "proposed treatment")
        facts.append(Fact(treated, risk.get("proposed_treatment"), "business_rule"))
        facts += _evidence((*where, "clauses"), treated, risk.get("clauses"))
        impact = risk.get("impact")
        if isinstance(impact, dict):
            for name in ("hours", "cost"):
                facts.append(
                    Fact(
                        (*where, f"impact {name}"),
                        _amount(impact.get(name)),
                        "calculation",
                        tolerance_percent=percent,
                        ambiguity=risk.get("ambiguity"),
                    )
                )
        else:
            # "not computed", whatever reason follows it.
            computed = str(impact or "").partition(":")[0].strip()
            facts.append(
                Fact((*where, "impact"), computed, "calculation", ambiguity=risk.get("ambiguity"))
            )
    conventions = out.get("qualifications", {}).get("measurement_conventions")
    if conventions is not None:
        facts.append(
            Fact(("qualifications", "measurement conventions"), len(conventions), "completeness")
        )
    return facts


def _review(out: dict[str, Any], _: Stage) -> list[Fact]:
    facts = [
        Fact(("figure", name), _amount(value), "final_output")
        for name, value in out.get("figures", {}).items()
    ]
    if "unpriced_lines" in out:
        facts.append(Fact(("unpriced lines",), out["unpriced_lines"], "final_output"))
    facts += [
        Fact(("open", name), value, "final_output", severity="HIGH")
        for name, value in out.get("open_items", {}).items()
    ]
    facts += [
        Fact(("gate", gate), status, "final_output")
        for gate, status in out.get("gates", {}).items()
    ]
    if "ready_for_g3" in out:
        facts.append(Fact(("ready for G3",), out["ready_for_g3"], "final_output"))
    return facts


EXTRACTORS = {
    "STG-001": _intake,
    "STG-002": _register,
    "STG-003": _views,
    "STG-004": _legend,
    "STG-005": _objects,
    "STG-006": _pipe,
    "STG-007": _takeoff,
    "STG-008": _specification,
    "STG-009": _bill,
    "STG-010": _pricing,
    "STG-011": _risks,
    "STG-012": _review,
}


# --- Differences ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Difference:
    stage_id: str
    what: str
    kind: str  # missing | wrong | extra
    expected: Any
    actual: Any
    classification: str  # a severity, or "TO SETTLE", or "FOR REVIEW"
    dimension: str
    ambiguity: str | None = None
    # The run gives what the other reading of the ambiguity gives.
    other_reading: bool = False

    @property
    def is_defect(self) -> bool:
        return self.classification in SEVERITIES


@dataclass
class StageResult:
    stage_id: str
    stage_name: str
    status: str  # compared | not exported | not compared
    # One entry per scored check: the dimension it counts under, and whether it passed.
    outcomes: list[tuple[str, bool]] = field(default_factory=list)
    differences: list[Difference] = field(default_factory=list)
    reason: str = ""

    @property
    def checks(self) -> int:
        return len(self.outcomes)

    @property
    def passed(self) -> int:
        return sum(1 for _, ok in self.outcomes if ok)


@dataclass
class Result:
    test_case_id: str
    package_status: str
    stages: list[StageResult]

    @property
    def differences(self) -> list[Difference]:
        return [one for stage in self.stages for one in stage.differences]

    def defects(self) -> dict[str, int]:
        found: dict[str, int] = dict.fromkeys(SEVERITIES, 0)
        for one in self.differences:
            if one.is_defect:
                found[one.classification] += 1
        return found

    def first_stage_that_differs(self) -> str | None:
        return next(
            (s.stage_id for s in self.stages if any(d.is_defect for d in s.differences)), None
        )


def _same(expected: Fact, actual: Any) -> bool:
    if expected.tolerance_absolute is not None:
        want, got = _number(expected.value), _number(actual)
        if want is None or got is None:
            return want is None and got is None
        return abs(got - want) <= expected.tolerance_absolute + 1e-9
    if expected.tolerance_percent is not None:
        want, got = _number(expected.value), _number(actual)
        if want is None or got is None:
            return want is None and got is None and expected.value == actual
        if want == 0:
            return got == 0
        return abs(got - want) / abs(want) * 100 <= expected.tolerance_percent
    want, got = _number(expected.value), _number(actual)
    if (
        want is not None
        and got is not None
        and not isinstance(expected.value, (bool, str))
        and not isinstance(actual, (bool, str))
    ):
        return want == got
    if isinstance(expected.value, str) or isinstance(actual, str):
        return _text(expected.value) == _text(actual)
    return bool(expected.value == actual)


def _resized(
    expected: dict[tuple[str, ...], Fact], actual: dict[tuple[str, ...], Fact]
) -> dict[tuple[str, ...], tuple[tuple[str, ...], Fact]]:
    """Expected items the run has at another size and in the same quantity: the expected
    key to the run's key. Only where one of each is left over, so nothing is guessed."""
    wanted: dict[tuple[str, ...], list[tuple[str, ...]]] = {}
    given: dict[tuple[str, ...], list[tuple[str, ...]]] = {}
    for facts, other, into in ((expected, actual, wanted), (actual, expected, given)):
        for key, fact in facts.items():
            if key not in other and fact.identity is not None and fact.ambiguity is None:
                into.setdefault(fact.identity, []).append(key)
    return {
        keys[0]: (given[identity][0], expected[keys[0]])
        for identity, keys in wanted.items()
        if len(keys) == 1
        and len(given.get(identity, [])) == 1
        and _same(expected[keys[0]], actual[given[identity][0]].value)
    }


def _other_readings(stage: Stage, expected: dict[tuple[str, ...], Fact]) -> None:
    """Mark the facts the package gives another value for under an ambiguity
    (`alternatives`: the ambiguity, and each fact's label to its other value)."""
    labels = {fact.label(): key for key, fact in expected.items()}
    for other in stage.expected_output.get("alternatives", []):
        for label, value in other.get("values", {}).items():
            key = labels.get(label)
            if key is None:
                raise ValueError(f"{stage.stage_id}: no expected value is labelled {label!r}")
            fact = expected[key]
            expected[key] = replace(
                fact,
                ambiguity=str(other["ambiguity"]),
                alternatives=(*fact.alternatives, value),
            )


def _equivalents(stage: Stage, expected: dict[tuple[str, ...], Fact]) -> None:
    """Mark the citations a reviewer accepts in place of the package's own
    (`equivalent_evidence`: what it is of, by its label, and the citations accepted)."""
    labels = {fact.label(): key for key, fact in expected.items() if fact.dimension == "evidence"}
    for one in stage.expected_output.get("equivalent_evidence", []):
        what = str(one.get("what"))
        key = labels.get(what)
        if key is None:
            raise ValueError(f"{stage.stage_id}: no expected evidence is labelled {what!r}")
        fact = expected[key]
        accepted = tuple(_cited(value) for value in one.get("accepted", []))
        expected[key] = replace(fact, equivalents=(*fact.equivalents, *accepted))


def compare(package: Package, run: Run) -> Result:
    stages = []
    for stage in package.stages:
        extract = EXTRACTORS.get(stage.stage_id)
        if extract is None:
            stages.append(
                StageResult(
                    stage.stage_id,
                    stage.stage_name,
                    "not compared",
                    reason="the comparison for this stage is not built",
                )
            )
            continue
        if stage.stage_id not in run:
            stages.append(
                StageResult(
                    stage.stage_id,
                    stage.stage_name,
                    "not exported",
                    reason="the run has no output for this stage",
                )
            )
            continue
        result = StageResult(stage.stage_id, stage.stage_name, "compared")
        expected = {fact.key: fact for fact in extract(stage.expected_output, stage)}
        actual = {fact.key: fact for fact in extract(run[stage.stage_id], stage)}
        _other_readings(stage, expected)
        _equivalents(stage, expected)
        resized = _resized(expected, actual)
        for other, fact in resized.values():
            measured = fact.tolerance_percent is not None
            result.outcomes.append((fact.dimension, False))
            result.differences.append(
                Difference(
                    stage.stage_id,
                    " / ".join(part for part in (*(fact.identity or ()), "size") if part),
                    "wrong",
                    fact.size,
                    actual[other].size,
                    # The wrong size of pipe is the wrong pipe; of a counted item, an
                    # attribute of the right item.
                    "HIGH" if measured else "MEDIUM",
                    fact.dimension,
                )
            )
        taken = {other for other, _ in resized.values()}
        for key, fact in expected.items():
            if key in resized:
                continue
            if fact.of is not None and (fact.of in resized or fact.of not in actual):
                continue  # the item itself is the difference, and is reported once
            found = actual.get(key)
            if found is not None and (_same(fact, found.value) or found.value in fact.equivalents):
                result.outcomes.append((fact.dimension, True))
                continue
            settle = fact.ambiguity is not None
            reading = found is not None and any(
                _same(replace(fact, value=value), found.value) for value in fact.alternatives
            )
            if fact.alternatives and not reading:
                settle = False  # neither reading of the ambiguity gives this
            # Something missing is a failure of completeness, whatever it is of, but a
            # value that cites nothing is a failure of evidence. A difference on an
            # ambiguity is not scored either way.
            missing = found is None and fact.dimension != "evidence"
            dimension = "completeness" if missing else fact.dimension
            if not settle:
                result.outcomes.append((dimension, False))
            result.differences.append(
                Difference(
                    stage.stage_id,
                    fact.label(),
                    "missing" if found is None else "wrong",
                    fact.value,
                    None if found is None else found.value,
                    "TO SETTLE" if settle else (fact.severity or stage.severity_if_incorrect),
                    dimension,
                    fact.ambiguity if settle else None,
                    reading,
                )
            )
        for key, fact in actual.items():
            if fact.of is not None and (fact.of not in expected or fact.of in taken):
                continue
            if fact.dimension == "evidence":
                continue  # compared where the reference gives it, and not otherwise
            if key not in expected and key not in taken:
                result.differences.append(
                    Difference(
                        stage.stage_id,
                        fact.label(),
                        "extra",
                        None,
                        fact.value,
                        "FOR REVIEW",
                        fact.dimension,
                    )
                )
        stages.append(result)
    return Result(package.test_case_id, package.status, stages)


# --- Score ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Dimension:
    key: str
    weight: int
    checks: int
    passed: int
    reason: str = ""  # why it is not measured

    @property
    def score(self) -> float | None:
        return self.passed / self.checks if self.checks else None


@dataclass(frozen=True)
class Score:
    dimensions: tuple[Dimension, ...]

    @property
    def measured_weight(self) -> int:
        return sum(d.weight for d in self.dimensions if d.score is not None)

    @property
    def overall(self) -> float | None:
        """The weighted score over the dimensions that were measured, or None if none was.
        It is of `measured_weight` percent of the whole, which the report states."""
        if not self.measured_weight:
            return None
        return (
            sum(d.weight * d.score for d in self.dimensions if d.score is not None)
            / self.measured_weight
        )


def score(result: Result) -> Score:
    checks = dict.fromkeys(WEIGHTS, 0)
    passed = dict.fromkeys(WEIGHTS, 0)
    for stage in result.stages:
        for dimension, ok in stage.outcomes:
            checks[dimension] += 1
            passed[dimension] += ok
    return Score(
        tuple(
            Dimension(
                key,
                weight,
                checks[key],
                passed[key],
                "" if checks[key] else NOT_BUILT.get(key, "nothing of it was compared"),
            )
            for key, weight in WEIGHTS.items()
        )
    )


# --- Report ---------------------------------------------------------------------------------


def _shown(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def report(result: Result, scored: Score) -> str:
    defects = result.defects()
    lines = [
        f"# Golden reference comparison: {result.test_case_id}",
        "",
        f"- Package status: {result.package_status or 'not stated'}",
        "- Defects: " + ", ".join(f"{defects[s]} {s.lower()}" for s in SEVERITIES),
        f"- To settle: {sum(1 for d in result.differences if d.classification == 'TO SETTLE')}",
        f"- For review (in the run, not in the reference): "
        f"{sum(1 for d in result.differences if d.classification == 'FOR REVIEW')}",
        f"- First stage with a defect: {result.first_stage_that_differs() or 'none'}",
    ]
    overall = scored.overall
    lines.append(
        "- Score: not measured"
        if overall is None
        else f"- Score: {overall:.1%} over the {scored.measured_weight}% of the weights that "
        "were measured"
    )
    lines += [
        "",
        "## Stages",
        "",
        "| Stage | Status | Checks | Passed | Defects |",
        "|---|---|---:|---:|---:|",
    ]
    for stage in result.stages:
        if stage.status == "compared":
            count = sum(1 for d in stage.differences if d.is_defect)
            lines.append(
                f"| {stage.stage_id} {stage.stage_name} | compared | {stage.checks} | "
                f"{stage.passed} | {count} |"
            )
        else:
            lines.append(
                f"| {stage.stage_id} {stage.stage_name} | **{stage.status}**: {stage.reason} "
                "| | | |"
            )
    lines += [
        "",
        "## Score",
        "",
        "| Dimension | Weight | Checks | Passed | Score |",
        "|---|---:|---:|---:|---|",
    ]
    for dimension in scored.dimensions:
        shown = (
            f"not measured: {dimension.reason}"
            if dimension.score is None
            else f"{dimension.score:.1%}"
        )
        lines.append(
            f"| {DIMENSION_LABELS[dimension.key]} | {dimension.weight}% | {dimension.checks} | "
            f"{dimension.passed} | {shown} |"
        )
    order: dict[str, int] = {name: index for index, name in enumerate(SEVERITIES)}
    for title, classes in (
        ("Defects", SEVERITIES),
        ("To settle", ("TO SETTLE",)),
        ("For review", ("FOR REVIEW",)),
    ):
        found = [d for d in result.differences if d.classification in classes]
        if not found:
            continue
        lines += [
            "",
            f"## {title}",
            "",
            "| Stage | What | Kind | Expected | Run | Class |",
            "|---|---|---|---|---|---|",
        ]
        for one in sorted(found, key=lambda d: (order.get(d.classification, 9), d.stage_id)):
            klass = one.classification + (
                f" ({one.ambiguity}{': the other reading' if one.other_reading else ''})"
                if one.ambiguity
                else ""
            )
            lines.append(
                f"| {one.stage_id} | {one.what} | {one.kind} | {_shown(one.expected)} | "
                f"{_shown(one.actual)} | {klass} |"
            )
    lines += [
        "",
        "> Compared: exact, tolerance and completeness, on stages 1 to 12, and evidence where "
        "the package gives a sheet, a clause or a source. Not compared: wording (semantic), "
        "evidence written as prose, and positions. A stage that was not compared has not "
        "passed.",
        "",
    ]
    return "\n".join(lines)
