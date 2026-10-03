"""Measurement rules as data: what is not drawn, calculated the same way every time (FR-QTO-03,
04, 10; FR-ADM-02).

A rule is a key, a version and a definition. Its inputs come from the bid's parameters (an
estimator enters a level's ceiling height, or the platform reads it from a sheet note), and
only failing those from the rule's own defaults, which say so. Every result records the
rule and version, and each input with its value and source, so a person can see exactly how
a hidden quantity was reached (FR-QTO-03) and reproduce it.

Arithmetic is integer millimetres and Decimal percentages (guardrail 3).

Pure: rules and parameters in, quantities out.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

import yaml

SEED = Path(__file__).resolve().parents[3] / "config" / "measurement_rules.yaml"


@dataclass(frozen=True)
class Rule:
    key: str
    version: int
    title: str
    definition: dict[str, Any]
    status: str = "to be confirmed"


@dataclass(frozen=True)
class Parameter:
    """One input a rule may use: a value, where it applies, and where it came from."""

    name: str
    value: float
    source: str  # "entered by Esther Tan", "sheet FP-L05-201 note 'CEILING HEIGHT 2750'"
    level: str | None = None


@dataclass(frozen=True)
class Input:
    name: str
    value: float
    source: str

    def as_json(self) -> dict[str, Any]:
        return {"name": self.name, "value": self.value, "source": self.source}


@dataclass(frozen=True)
class Derived:
    """A rule's result: the quantity, and everything needed to reproduce it."""

    rule: Rule
    value: int  # millimetres, or a count
    inputs: tuple[Input, ...] = field(default_factory=tuple)

    def as_json(self) -> dict[str, Any]:
        return {
            "rule_key": self.rule.key,
            "rule_version": self.rule.version,
            "rule_status": self.rule.status,
            "inputs": [item.as_json() for item in self.inputs],
            "value": self.value,
        }


def seed_rules(path: Path = SEED) -> list[Rule]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        Rule(item["key"], 1, item["title"], dict(item["definition"]), item.get("status", ""))
        for item in data["rules"]
    ]


def value_of(name: str, rule: Rule, parameters: list[Parameter], level: str | None) -> Input:
    """A level's own parameter, else the bid's, else the rule's default (and it says so)."""
    for wanted in (level, None):
        found = [p for p in parameters if p.name == name and p.level == wanted]
        if found:
            chosen = found[-1]
            return Input(name, chosen.value, chosen.source)
    default = rule.definition.get("defaults", {}).get(name)
    if default is None:
        raise KeyError(f"rule {rule.key} v{rule.version} has no value for {name}")
    return Input(name, float(default), f"rule default ({rule.status})")


def drop_length(rule: Rule, parameters: list[Parameter], level: str | None) -> Derived:
    """Branch elevation minus ceiling height minus sprinkler setting, per head."""
    branch = value_of("branch_elevation_mm", rule, parameters, level)
    ceiling = value_of("ceiling_height_mm", rule, parameters, level)
    setting = value_of("sprinkler_setting_mm", rule, parameters, level)
    length = max(0, round(branch.value - ceiling.value - setting.value))
    return Derived(rule, length, (branch, ceiling, setting))


def riser_length(rule: Rule, parameters: list[Parameter], level: str | None) -> Derived:
    """Floor-to-floor height times levels served."""
    height = value_of("floor_to_floor_mm", rule, parameters, level)
    served = value_of("levels_served", rule, parameters, level)
    return Derived(rule, round(height.value * served.value), (height, served))


def level_parameters(sheet_number: str, marks: Sequence[Any]) -> list[Parameter]:
    """Each level's floor-to-floor height, from the levels a sheet names with their floor
    levels (`drawings.equipment.level_marks`, lowest first): the height from one to the
    next above it. The riser rule then measures a rising main level by level (P2-01)."""
    return [
        Parameter(
            "floor_to_floor_mm",
            float(above.elevation_mm - low.elevation_mm),
            f"level schedule on sheet {sheet_number}: '{low.text}' to '{above.text}'",
            low.level,
        )
        for low, above in pairwise(marks)
        if above.elevation_mm > low.elevation_mm
    ]


def couplings(rule: Rule, length_mm: int, connected_ends: int) -> Derived:
    """One between each pair of random lengths, and one per end that meets a fitting."""
    random_length = int(rule.definition.get("random_length_mm", 6000))
    per_end = int(rule.definition.get("per_connected_end", 1))
    joints = max(0, math.ceil(length_mm / random_length) - 1) if length_mm > 0 else 0
    return Derived(
        rule,
        joints + per_end * connected_ends,
        (
            Input("length_mm", float(length_mm), "measured run"),
            Input("random_length_mm", float(random_length), f"rule ({rule.status})"),
            Input("connected_ends", float(connected_ends), "pipe network"),
        ),
    )


def allowance_percent(rule: Rule | None, item_class: str) -> Decimal | None:
    """The allowance for an item class, kept apart from the net quantity (FR-QTO-10)."""
    if rule is None:
        return None
    value = rule.definition.get("percent", {}).get(item_class)
    return Decimal(str(value)) if value is not None else None


def adjusted(net: Decimal, percent: Decimal | None) -> Decimal:
    """Net plus its allowance, for display beside the net: never stored in its place."""
    if percent is None:
        return net
    return (net * (Decimal(100) + percent) / Decimal(100)).quantize(Decimal("0.001"))
