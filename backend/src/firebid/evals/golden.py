"""A run's stage outputs against a Golden Reference Work Product Package (FR-LRN-01).

`docs/plan/TEST_STRATEGY.md` says what a package is and how it is compared. This is the
comparison: exact (type A), tolerance (C) and completeness (E), for stages 1 to 7. Semantic
(B) and evidence (D) comparison, and stages 8 to 12, are not built: what is not compared is
reported as not compared, never as passed.

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
shown with the ambiguity's ID, and neither a defect nor a pass.

Pure: a package and a run in; differences, a score and a report out.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
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
    "evidence": "evidence comparison (type D) is not built",
    "final_output": "stage 12 is not compared yet",
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
    severity: Severity | None = None  # None: the stage's own
    ambiguity: str | None = None
    # For an item that has a size: the size, and what the item is without it. The same item
    # at another size is one difference (its size), not one item missing and another extra.
    size: str | None = None
    identity: tuple[str, ...] | None = None

    def label(self) -> str:
        return " / ".join(part for part in self.key if part)


def _text(value: Any) -> str:
    return " ".join(str(value if value is not None else "").split()).casefold()


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except TypeError, ValueError:
        return None


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
    return [
        Fact((_text(row.get("description")), "object type"), row.get("object_type"), "completeness")
        for row in out.get("rows", [])
    ]


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
    totals: dict[tuple[str, ...], tuple[float, str, bool, str | None]] = {}
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
            totals[key] = (
                quantity + float(item.get("quantity", 0)),
                unit,
                derived,
                item.get("ambiguity") or ambiguity,
            )
    return [
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


EXTRACTORS = {
    "STG-001": _intake,
    "STG-002": _register,
    "STG-003": _views,
    "STG-004": _legend,
    "STG-005": _objects,
    "STG-006": _pipe,
    "STG-007": _takeoff,
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
    if expected.tolerance_percent is not None:
        want, got = _number(expected.value), _number(actual)
        if want is None or got is None:
            return False
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
            found = actual.get(key)
            if found is not None and _same(fact, found.value):
                result.outcomes.append((fact.dimension, True))
                continue
            settle = fact.ambiguity is not None
            # Something missing is a failure of completeness, whatever it is of. A
            # difference on an ambiguity is not scored either way.
            dimension = "completeness" if found is None else fact.dimension
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
                    fact.ambiguity,
                )
            )
        for key, fact in actual.items():
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
            klass = one.classification + (f" ({one.ambiguity})" if one.ambiguity else "")
            lines.append(
                f"| {one.stage_id} | {one.what} | {one.kind} | {_shown(one.expected)} | "
                f"{_shown(one.actual)} | {klass} |"
            )
    lines += [
        "",
        "> Compared: exact, tolerance and completeness, on stages 1 to 7. Not compared: "
        "wording (semantic), evidence, positions, and stages 8 to 12. A stage that was not "
        "compared has not passed.",
        "",
    ]
    return "\n".join(lines)
