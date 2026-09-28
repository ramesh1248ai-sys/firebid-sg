"""The company's BOQ from verified takeoff (FR-BOQ-01, FR-ADM-03).

A template says how the bill is laid out: a bill per system, groups within it (heads,
pipework, fittings, valves) in order, whether each group rolls up by level or over the whole
building, how each canonical type is described, and the units. Items with the same
description, unit and roll-up place become one line, and the line keeps every item it came
from, which is its trace to evidence (FR-BOQ-05).

Quantities are the items' net quantities, added up in Decimal; the allowance is carried
beside them, never folded in (FR-QTO-10). Conversions are fixed: a QTO count is `nr`, a
length `m`; a template may say otherwise.

Pure: items and a template in, line drafts out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

SEED = Path(__file__).resolve().parents[3] / "config" / "boq_templates.yaml"
NOT_SPECIFIED = "not specified"
PIPE_CLASSES = ("main", "branch", "drop", "riser")
PLACEHOLDER = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True)
class Template:
    key: str
    version: int
    title: str
    definition: dict[str, Any]
    status: str = "to be confirmed"


@dataclass(frozen=True)
class Item:
    """A verified QTO item, as the BOQ needs it."""

    id: str
    human_id: str
    item_type: str
    classification: str
    description: str
    attributes: dict[str, str]
    unit: str
    net_quantity: Decimal
    allowance_percent: Decimal | None
    level: str | None
    system: str = "sprinkler"


@dataclass
class LineDraft:
    section: str
    group: str
    level: str | None
    description: str
    unit: str
    quantity: Decimal
    allowance_percent: Decimal | None
    item_ids: list[str] = field(default_factory=list)
    item_human_ids: list[str] = field(default_factory=list)
    item_no: str = ""


def seed_templates(path: Path = SEED) -> list[Template]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        Template(t["key"], 1, t["title"], dict(t["definition"]), t.get("status", ""))
        for t in data["templates"]
    ]


def describe(item: Item, definition: dict[str, Any]) -> str:
    """The item's words from the template: parts whose attributes are all known."""
    patterns: dict[str, list[str]] = definition.get("descriptions", {})
    parts = patterns.get(item.item_type) or patterns.get(item.classification)
    if parts is None and item.classification in PIPE_CLASSES:
        parts = patterns.get("pipe")
    if parts is None:
        parts = patterns.get("default", ["{label}"])
    known = {k: v.replace("_", " ") for k, v in item.attributes.items() if v and v != NOT_SPECIFIED}
    known["label"] = item.description
    known["class_label"] = str(
        definition.get("class_labels", {}).get(item.classification, item.classification)
    )
    words = []
    for part in parts:
        names = PLACEHOLDER.findall(part)
        if all(name in known for name in names):
            words.append(PLACEHOLDER.sub(lambda m: known[m.group(1)], part))
    text = "".join(words).strip(" ,")
    return text[:1].upper() + text[1:] if text else item.description


def _group_of(item: Item, definition: dict[str, Any]) -> dict[str, Any]:
    klass = item.classification
    for group in definition.get("groups", []):
        if klass in group.get("classes", []):
            return dict(group)
    return {"key": "other", "heading": "Other items", "by": "building"}


def generate(items: list[Item], template: Template) -> list[LineDraft]:
    """One line per description, unit and roll-up place; numbered in template order."""
    definition = template.definition
    units: dict[str, str] = definition.get("units", {})
    sections: dict[str, str] = definition.get("sections", {})
    group_order = [g["key"] for g in definition.get("groups", [])] + ["other"]
    lines: dict[tuple[str, str, str | None, str, str], LineDraft] = {}
    for item in items:
        group = _group_of(item, definition)
        section = sections.get(item.system, item.system.upper())
        level = item.level if group.get("by") == "level" else None
        unit = units.get(item.unit, item.unit)
        description = describe(item, definition)
        key = (section, group["key"], level, description, unit)
        line = lines.get(key)
        if line is None:
            line = lines[key] = LineDraft(
                section=section,
                group=group["heading"],
                level=level,
                description=description,
                unit=unit,
                quantity=Decimal(0),
                allowance_percent=item.allowance_percent,
            )
        line.quantity += item.net_quantity
        if line.allowance_percent != item.allowance_percent:
            line.allowance_percent = None  # items that disagree: no single allowance to show
        line.item_ids.append(item.id)
        line.item_human_ids.append(item.human_id)

    def order(line: LineDraft) -> tuple[Any, ...]:
        section_rank = (
            list(sections.values()).index(line.section) if line.section in sections.values() else 99
        )
        group_key = next(
            (g["key"] for g in definition.get("groups", []) if g["heading"] == line.group),
            "other",
        )
        return (section_rank, group_order.index(group_key), line.level or "", line.description)

    ordered = sorted(lines.values(), key=order)
    numbers: dict[str, int] = {}
    for line in ordered:
        prefix = chr(
            ord("A") + list(dict.fromkeys(other.section for other in ordered)).index(line.section)
        )
        numbers[prefix] = numbers.get(prefix, 0) + 1
        line.item_no = f"{prefix}{numbers[prefix]}"
        line.quantity = line.quantity.quantize(Decimal("0.001"))
        line.item_ids.sort()
        line.item_human_ids.sort()
    return ordered
