"""From a flagged issue to a clarification draft (FR-RFI-02, 06, 07).

A **candidate** is something the platform flagged that only the client can settle: a
conflict between the specification and the drawings, a variance between the client's bill
and what was measured, or a scope row the specification leaves unclear. Each carries the
evidence it rests on.

A **draft** is composed from one candidate, or from a confirmed group of related ones. It
has every field a clarification needs, and at least one evidence reference: the draft's
model refuses to exist without one (guardrail 5), so a draft with no evidence is rejected
where it is made, and never saved. Options are recommendations for the client to decide
on, and are worded so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

CONFIG = Path(__file__).resolve().parents[3] / "config" / "clarifications.yaml"
KINDS = ("spec_issue", "boq_variance", "scope_row", "missing_information")
REVIEWERS = ("bid_manager", "design_manager")
RECOMMENDATION = "Recommendation: "


@lru_cache(maxsize=2)
def settings(path: Path = CONFIG) -> dict[str, Any]:
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


class EvidenceRef(BaseModel):
    """Where a statement in a clarification can be checked: a clause, a sheet, a bill line
    or a takeoff item, with the words or figures found there."""

    kind: str = Field(pattern="^(clause|sheet|boq_line|client_boq_line|qto_item|scope_row)$")
    label: str = Field(min_length=1, max_length=300)
    quote: str = Field(default="", max_length=2000)
    link: str | None = Field(default=None, max_length=500)
    revision: str | None = Field(default=None, max_length=40)


class Option(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    # FR-RFI-06: an option is never more than a recommendation.
    recommendation: bool = True

    @field_validator("recommendation")
    @classmethod
    def _only_a_recommendation(cls, value: bool) -> bool:
        if not value:
            raise ValueError("an option is a recommendation only")
        return value


class SheetRef(BaseModel):
    sheet_number: str = Field(min_length=1, max_length=120)
    revision: str | None = Field(default=None, max_length=40)
    sheet_id: str | None = None


class Draft(BaseModel):
    """A clarification as drafted. `evidence` cannot be empty."""

    subject: str = Field(min_length=1, max_length=300)
    project: str = Field(min_length=1, max_length=300)
    level_grid: str = Field(default="", max_length=200)
    sheets: list[SheetRef] = []
    problem: str = Field(min_length=1, max_length=6000)
    evidence: list[EvidenceRef] = Field(min_length=1)
    options: list[Option] = []
    cost_impact: str = Field(default="", max_length=2000)
    programme_impact: str = Field(default="", max_length=2000)
    required_reviewer: str = Field(pattern="^(bid_manager|design_manager)$")
    engineering_content: bool = False
    engineering_reason: str = ""


@dataclass(frozen=True)
class Candidate:
    kind: str
    ref: str  # stable within the bid: a candidate is in one clarification at most
    subject: str
    problem: str
    evidence: tuple[EvidenceRef, ...]
    system: str = ""
    level_grid: str = ""
    sheets: tuple[SheetRef, ...] = ()
    options: tuple[str, ...] = ()
    cost_impact: str = ""
    programme_impact: str = ""
    # What the issue is about, for the engineering rule: "pipe_material", "quantity" ...
    topic: str = ""
    # What related candidates share: they are proposed as one clarification.
    group_key: str = ""

    def as_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ref": self.ref,
            "subject": self.subject,
            "problem": self.problem,
            "evidence": [item.model_dump() for item in self.evidence],
            "system": self.system,
            "level_grid": self.level_grid,
            "sheets": [sheet.model_dump() for sheet in self.sheets],
            "options": list(self.options),
            "topic": self.topic,
            "group_key": self.group_key,
        }


def engineering(
    candidates: list[Candidate], words: str = "", config: dict[str, Any] | None = None
) -> tuple[bool, str]:
    """Whether a clarification has engineering, fire-safety or structural content, and why:
    a candidate about an engineering subject, or wording that names one (FR-RFI-06)."""
    rules = (config if config is not None else settings()).get("engineering") or {}
    subjects = {str(item) for item in rules.get("subjects") or []}
    for candidate in candidates:
        if candidate.topic in subjects:
            return True, f"it concerns {candidate.topic.replace('_', ' ')}"
    text = " ".join([words, *(c.subject + " " + c.problem for c in candidates)]).lower()
    for word in rules.get("words") or []:
        if re.search(rf"(?<![a-z]){re.escape(str(word).lower())}(?![a-z])", text):
            return True, f"it mentions {word}"
    return False, ""


def compose(candidates: list[Candidate], project: str) -> Draft:
    """One draft from one candidate or a confirmed group. Raises pydantic's
    ``ValidationError`` when there is nothing to cite: such a draft is not made."""
    if not candidates:
        raise ValueError("a clarification is drafted from at least one flagged issue")
    first = candidates[0]
    if len(candidates) == 1:
        subject, problem = first.subject, first.problem
    else:
        subject = _common_subject(candidates)
        problem = "\n".join(
            f"{index}. {candidate.problem}" for index, candidate in enumerate(candidates, start=1)
        )
    evidence: list[EvidenceRef] = []
    for candidate in candidates:
        for item in candidate.evidence:
            if item not in evidence:
                evidence.append(item)
    sheets: list[SheetRef] = []
    for candidate in candidates:
        for sheet in candidate.sheets:
            if sheet not in sheets:
                sheets.append(sheet)
    places = list(dict.fromkeys(c.level_grid for c in candidates if c.level_grid))
    options: list[str] = []
    for candidate in candidates:
        for option in candidate.options:
            if option not in options:
                options.append(option)
    is_engineering, why = engineering(candidates)
    return Draft(
        subject=subject[:300],
        project=project,
        level_grid="; ".join(places)[:200],
        sheets=sheets,
        problem=problem,
        evidence=evidence,
        options=[Option(text=as_recommendation(option)) for option in options],
        cost_impact="; ".join(dict.fromkeys(c.cost_impact for c in candidates if c.cost_impact)),
        programme_impact="; ".join(
            dict.fromkeys(c.programme_impact for c in candidates if c.programme_impact)
        ),
        required_reviewer="design_manager" if is_engineering else "bid_manager",
        engineering_content=is_engineering,
        engineering_reason=why,
    )


def as_recommendation(text: str) -> str:
    """An option's words, said as a recommendation."""
    words = text.strip()
    return words if words.lower().startswith("recommendation") else RECOMMENDATION + words


def _common_subject(candidates: list[Candidate]) -> str:
    systems = list(dict.fromkeys(c.system for c in candidates if c.system))
    sheets = list(dict.fromkeys(s.sheet_number for c in candidates for s in c.sheets))
    about = systems[0].replace("_", " ") if len(systems) == 1 else "fire protection"
    where = f" on {sheets[0]}" if len(sheets) == 1 else ""
    return f"{len(candidates)} queries on the {about} installation{where}"


@dataclass
class Group:
    key: str
    reason: str
    candidates: list[Candidate] = field(default_factory=list)


def propose_groups(candidates: list[Candidate]) -> list[Group]:
    """Related candidates, proposed as one clarification each: those that share a group key
    (the same sheet and system, or the same section of the bill). A person confirms a group
    before it is drafted; a candidate with no relatives is a group of one."""
    groups: dict[str, Group] = {}
    for candidate in candidates:
        key = candidate.group_key or f"{candidate.kind}:{candidate.ref}"
        group = groups.setdefault(key, Group(key=key, reason=""))
        group.candidates.append(candidate)
    for group in groups.values():
        count = len(group.candidates)
        group.reason = (
            f"{count} issues about {group.key.replace('|', ', ')}" if count > 1 else "on its own"
        )
    return list(groups.values())
