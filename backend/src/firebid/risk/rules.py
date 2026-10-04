"""The rules that find a bid's scope gaps and risks (FR-RSK-01, 02, 03).

* **Checklist.** Each item of the configured checklist, for each system the bid has, with a
  status proposed from the scope matrix and the takeoff. What neither settles is open.
* **Design responsibility.** Sentences of the specification that put design and build, shop
  drawings, hydraulic calculations or the engagement of a Qualified Person on the
  contractor, each cited to its clause.
* **Execution.** Basement levels, installation heights above a threshold, high-rise
  logistics, and what clauses and drawing notes say of night work, occupied buildings,
  shutdowns, congested ceilings and access. Each with its evidence.

A finding is a proposal: a person resolves a checklist item and decides a risk's treatment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CONFIG = Path(__file__).resolve().parents[3] / "config" / "risk.yaml"
RULES_VERSION = "risk-rules-1"
RESOLVED = ("included", "excluded", "by_others", "clarified")
STATUSES = ("open", *RESOLVED)
TREATMENTS = ("price", "qualify", "clarify", "accept")
SENTENCE = re.compile(r"(?<=[.;])\s+")


@lru_cache(maxsize=2)
def settings(path: Path = CONFIG) -> dict[str, Any]:
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


# --- The scope-gap checklist (FR-RSK-01) ------------------------------------------------------


@dataclass(frozen=True)
class ScopeFact:
    """A row of the scope matrix, as the checklist reads it."""

    system: str
    kind: str  # obligation | interface
    key: str
    status: str  # included | excluded | by_others | unclear
    clause: str | None = None
    quote: str | None = None


@dataclass(frozen=True)
class Check:
    system: str
    key: str
    label: str
    proposed: str  # open, or a resolved status
    basis: str
    evidence: tuple[dict[str, Any], ...] = ()


def checklist(
    systems: list[str],
    scope: list[ScopeFact],
    takeoff: dict[str, Decimal],
    config: dict[str, Any] | None = None,
) -> list[Check]:
    """Every checklist item for every system, with the status the scope matrix and the
    takeoff propose for it."""
    items = (config if config is not None else settings()).get("checklist") or []
    found = []
    for system in systems:
        rows = [row for row in scope if row.system == system]
        for item in items:
            limited = item.get("systems")
            if limited and system not in limited:
                continue
            found.append(_check(system, item, rows, takeoff))
    return found


def _check(
    system: str, item: dict[str, Any], rows: list[ScopeFact], takeoff: dict[str, Decimal]
) -> Check:
    key, label = str(item["key"]), str(item.get("label") or item["key"])
    cited: list[ScopeFact] = []
    interface = item.get("scope_interface")
    if interface:
        cited = [r for r in rows if r.kind == "interface" and r.key == interface]
    elif item.get("scope_obligations"):
        wanted = set(item["scope_obligations"])
        cited = [r for r in rows if r.kind == "obligation" and r.key in wanted]
    if cited:
        statuses = {row.status for row in cited}
        evidence = tuple(
            {
                "kind": "clause" if row.clause else "scope_row",
                "label": f"Specification clause {row.clause}"
                if row.clause
                else f"Scope matrix: {row.key.replace('_', ' ')}",
                "quote": row.quote or "",
            }
            for row in cited
        )
        if len(statuses) == 1 and "unclear" not in statuses:
            status = next(iter(statuses))
            return Check(
                system, key, label, status, f"the scope matrix has it {_words(status)}", evidence
            )
        return Check(
            system,
            key,
            label,
            "open",
            "the scope matrix is unclear on it"
            if statuses == {"unclear"}
            else "the scope matrix's rows do not agree on it",
            evidence,
        )
    counted = {kind: takeoff[kind] for kind in item.get("takeoff_types") or [] if takeoff.get(kind)}
    if counted:
        described = ", ".join(
            f"{format(count.normalize(), 'f')} {kind.replace('_', ' ')}"
            for kind, count in counted.items()
        )
        return Check(
            system,
            key,
            label,
            "included",
            f"the takeoff counts {described}",
            ({"kind": "takeoff", "label": f"Takeoff: {described}", "quote": ""},),
        )
    return Check(
        system,
        key,
        label,
        "open",
        "neither the scope matrix nor the takeoff settles it",
    )


def _words(status: str) -> str:
    return status.replace("_", " ")


# --- Design responsibility (FR-RSK-02) --------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """A risk as the rules find it, with what it rests on."""

    key: str  # stable within a bid
    category: str  # design_responsibility | execution
    kind: str
    title: str
    description: str
    evidence: tuple[dict[str, Any], ...]
    treatment: str
    level: str | None = None
    multiplier: str | None = None  # the labour multiplier its impact is worked out with
    detail: dict[str, Any] = field(default_factory=dict)


def sentences(text: str) -> list[str]:
    return [part.strip() for part in SENTENCE.split(text) if part.strip()]


def design_risks(
    clauses: list[tuple[str, str, str]], config: dict[str, Any] | None = None
) -> list[Finding]:
    """What the specification's clauses put on the contractor by way of design, one risk a
    kind, citing every clause that says so."""
    rules = (config if config is not None else settings()).get("design_responsibility") or []
    found = []
    for rule in rules:
        pattern = re.compile(str(rule["match"]), re.IGNORECASE)
        cited: list[dict[str, Any]] = []
        for number, heading, text in clauses:
            for sentence in sentences(f"{heading}. {text}" if heading and not text else text):
                if pattern.search(sentence):
                    cited.append(
                        {
                            "kind": "clause",
                            "label": f"Specification clause {number}",
                            "clause": number,
                            "quote": sentence,
                        }
                    )
                    break
        if not cited:
            continue
        label = str(rule.get("label") or rule["key"])
        numbers = ", ".join(str(item["clause"]) for item in cited)
        found.append(
            Finding(
                key=f"design:{rule['key']}",
                category="design_responsibility",
                kind=str(rule["key"]),
                title=f"{label}: the contractor's responsibility",
                description=(
                    f"The specification (clause {numbers}) puts {label.lower()} on the contractor."
                ),
                evidence=tuple(cited),
                treatment=str(rule.get("treatment") or "qualify"),
            )
        )
    return found


# --- Execution risks (FR-RSK-03) --------------------------------------------------------------


@dataclass(frozen=True)
class Words:
    """Words the rules read for execution conditions: a clause, or a note on a sheet."""

    kind: str  # clause | sheet
    label: str
    text: str
    level: str | None = None


def execution_risks(
    levels: list[str],
    ceiling_heights: dict[str | None, tuple[Decimal, str]],
    levels_served: tuple[Decimal, str] | None,
    words: list[Words],
    config: dict[str, Any] | None = None,
) -> list[Finding]:
    """Execution conditions with their evidence: from the levels the drawings show, the
    heights and levels the bid's parameters state, and what clauses and notes say."""
    full = config if config is not None else settings()
    rules = full.get("execution") or {}
    treatment = str((full.get("treatments") or {}).get("execution") or "price")
    labels = rules.get("labels") or {}
    multipliers = rules.get("multipliers") or {}
    found: list[Finding] = []

    basement = rules.get("basement_levels")
    below = [level for level in levels if basement and re.match(str(basement), level.strip(), re.I)]
    if below:
        found.append(
            Finding(
                key="execution:basement",
                category="execution",
                kind="basement",
                title=f"{labels.get('basement', 'Basement levels')}: {', '.join(below)}",
                description=f"Work on {len(below)} basement level(s): {', '.join(below)}.",
                evidence=tuple(
                    {"kind": "level", "label": f"Level {level} on the drawings", "quote": ""}
                    for level in below
                ),
                treatment=treatment,
                multiplier=multipliers.get("basement"),
                detail={"levels": below},
            )
        )

    threshold = rules.get("work_at_height_above_mm")
    for level, (height, source) in sorted(ceiling_heights.items(), key=lambda item: item[0] or ""):
        if threshold is None or height <= Decimal(str(threshold)):
            continue
        where = level or "the whole bid"
        found.append(
            Finding(
                key=f"execution:work_at_height:{level or ''}",
                category="execution",
                kind="work_at_height",
                title=f"{labels.get('work_at_height', 'Work at height')}: {where}",
                description=(
                    f"Installation at {format(height.normalize(), 'f')} mm on {where}, above "
                    f"{threshold} mm."
                ),
                evidence=(
                    {
                        "kind": "parameter",
                        "label": f"Ceiling height, {where}",
                        "quote": f"{format(height.normalize(), 'f')} mm ({source})",
                    },
                ),
                treatment=treatment,
                level=level,
                multiplier="height",
                detail={"height_mm": str(height)},
            )
        )

    high = rules.get("high_rise_from_levels")
    if high is not None and levels_served is not None and levels_served[0] >= Decimal(str(high)):
        served = format(levels_served[0].normalize(), "f")
        found.append(
            Finding(
                key="execution:high_rise",
                category="execution",
                kind="high_rise",
                title=f"{labels.get('high_rise', 'High-rise logistics')}: {served} levels",
                description=f"The building serves {served} levels, at or above {high}.",
                evidence=(
                    {
                        "kind": "parameter",
                        "label": "Levels served",
                        "quote": f"{served} ({levels_served[1]})",
                    },
                ),
                treatment=treatment,
                multiplier=multipliers.get("high_rise"),
            )
        )

    for rule in rules.get("wording") or []:
        pattern = re.compile(str(rule["match"]), re.IGNORECASE)
        cited = []
        for item in words:
            for sentence in sentences(item.text):
                if pattern.search(sentence):
                    cited.append({"kind": item.kind, "label": item.label, "quote": sentence})
                    break
        if not cited:
            continue
        label = str(rule.get("label") or rule["key"])
        found.append(
            Finding(
                key=f"execution:{rule['key']}",
                category="execution",
                kind=str(rule["key"]),
                title=label,
                description=f"{label}, as stated in {', '.join(str(c['label']) for c in cited)}.",
                evidence=tuple(cited),
                treatment=treatment,
                multiplier=rule.get("multiplier"),
            )
        )
    return found
