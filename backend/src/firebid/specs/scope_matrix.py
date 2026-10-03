"""The scope and interface matrix: what is in the contractor's scope, per system (FR-SPEC-04).

A row is an obligation category or an interface with another trade, for one fire protection
system. Its status is one of:

* **included:** the specification puts it on the fire protection contractor;
* **by others:** the specification gives it to another trade or to the main contractor;
* **excluded:** the specification takes it out of this contract;
* **unclear:** the specification speaks of it without saying whose it is, says both, or does
  not speak of it at all.

The interfaces looked for, and the words that name each, are configuration
(`config/scope_matrix.yaml`): they are the company's own list, to be confirmed by the Design
Manager. A row cites the clause its status was read from. Every row is a proposal: the
estimator edits the status and confirms the matrix.

Pure: clauses, obligations and the interface list in; rows out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from firebid.specs import obligations
from firebid.specs.attributes import sentences
from firebid.specs.clauses import Clause

CONFIG = Path(__file__).resolve().parents[3] / "config" / "scope_matrix.yaml"
STATUSES = ("included", "excluded", "by_others", "unclear")
# Sections that are not one system: other trades, and those that speak for every system.
SHARED = ("general", "fire_protection")
NOT_SYSTEMS = ("other", "unknown", *SHARED)

BY_OTHERS = re.compile(
    r"\bby\s+(?:the\s+)?(?:others|main\s+contractor|builder|electrical\s+(?:sub[-\s]?)?contractor|"
    r"plumbing\s+(?:sub[-\s]?)?contractor|ACMV\s+(?:sub[-\s]?)?contractor|fire\s+alarm\s+"
    r"(?:sub[-\s]?)?contractor|employer|client|other\s+trades?)\b",
    re.I,
)
EXCLUDED = re.compile(
    r"\b(excluded|not\s+included|is\s+not\s+part\s+of|does\s+not\s+form\s+part|outside\s+the\s+scope)\b",
    re.I,
)
INCLUDED = re.compile(
    r"\b(shall\s+(?:provide|supply|install|carry\s+out|include|allow\s+for|be\s+responsible)|"
    r"is\s+included|are\s+included|deemed\s+(?:to\s+be\s+)?included|by\s+(?:the\s+)?(?:fire\s+protection\s+)?"
    r"(?:sub[-\s]?)?contractor|by\s+this\s+contractor|under\s+this\s+contract)\b",
    re.I,
)


@dataclass(frozen=True)
class Interface:
    key: str
    label: str
    pattern: re.Pattern[str]
    systems: tuple[str, ...]  # empty: every system the specification has


def interfaces(path: Path = CONFIG) -> list[Interface]:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [
        Interface(
            key=str(item["key"]),
            label=str(item["label"]),
            pattern=re.compile(str(item["match"]), re.I),
            systems=tuple(item.get("systems") or ()),
        )
        for item in data.get("interfaces", [])
    ]


@dataclass(frozen=True)
class Row:
    system: str
    kind: str  # obligation | interface
    key: str
    label: str
    status: str
    clause: str | None
    quote: str | None
    reason: str


def status_of(sentence: str) -> str:
    """Whose a sentence says something is.

    A sentence that names a party, or excludes the thing, says so. One that says two things
    at once is unclear. One that names nobody but obliges ("shall be painted") is the
    contractor's, as everything a specification obliges without saying otherwise is; one
    that neither names nor obliges is unclear.
    """
    said = {
        name
        for name, pattern in (
            ("by_others", BY_OTHERS),
            ("excluded", EXCLUDED),
            ("included", INCLUDED),
        )
        if pattern.search(sentence)
    }
    if len(said) == 1:
        return next(iter(said))
    if said:
        return "unclear"
    return "included" if obligations.OBLIGES.search(sentence) else "unclear"


def systems_of(systems: dict[str, str]) -> list[str]:
    """The fire protection systems the specification has sections for, in reading order."""
    seen: list[str] = []
    for system in systems.values():
        if system not in NOT_SYSTEMS and system not in seen:
            seen.append(system)
    return seen


def build(
    clauses: list[Clause],
    systems: dict[str, str],
    found: list[obligations.Obligation],
    wanted: list[Interface] | None = None,
) -> list[Row]:
    """A row for each obligation category and each interface, for each system.

    A clause in a general section (testing, scope, preliminaries) speaks for every system;
    a clause in a system's own section speaks for that system and takes precedence.
    """
    wanted = interfaces() if wanted is None else wanted
    rows: list[Row] = []
    for system in systems_of(systems):
        own = [c for c in clauses if systems.get(c.number) == system]
        shared = [c for c in clauses if systems.get(c.number) in SHARED]
        for category in obligations.CATEGORY_KEYS:
            stated = [o for o in found if o.category == category and o.system == system] or [
                o
                for o in found
                if o.category == category and o.system in ("general", "fire_protection")
            ]
            if stated:
                first = stated[0]
                rows.append(
                    Row(
                        system,
                        "obligation",
                        category,
                        obligations.LABELS[category],
                        status_of(first.quote),
                        first.clause,
                        first.quote,
                        "the specification obliges it",
                    )
                )
            else:
                rows.append(
                    Row(
                        system,
                        "obligation",
                        category,
                        obligations.LABELS[category],
                        "unclear",
                        None,
                        None,
                        "the specification does not mention it",
                    )
                )
        for interface in wanted:
            if interface.systems and system not in interface.systems:
                continue
            hit = _first(own, interface.pattern) or _first(shared, interface.pattern)
            if hit is None:
                rows.append(
                    Row(
                        system,
                        "interface",
                        interface.key,
                        interface.label,
                        "unclear",
                        None,
                        None,
                        "the specification does not mention it",
                    )
                )
                continue
            clause, sentence = hit
            status = status_of(sentence)
            rows.append(
                Row(
                    system,
                    "interface",
                    interface.key,
                    interface.label,
                    status,
                    clause.number,
                    sentence,
                    "the clause says whose it is"
                    if status != "unclear"
                    else "the clause does not say whose it is",
                )
            )
    return rows


def _first(clauses: list[Clause], pattern: re.Pattern[str]) -> tuple[Clause, str] | None:
    for clause in clauses:
        for sentence in sentences(clause.text):
            if pattern.search(sentence):
                return clause, sentence
    return None
