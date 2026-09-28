"""What the QTO engine works from and what it produces (P1-07). Plain data, no database.

Detections and runs are P1-05's stored proposals, from Current sheets only, with the view,
level and grid position they were found at. An item draft is a quantity with everything its
evidence record needs: where it came from, how it was calculated, and each attribute's
source.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class Placement:
    """Where a detection or run was found."""

    sheet_id: str
    sheet_number: str
    revision: str
    document_id: str
    view_id: str | None
    view_kind: str | None  # plan | enlarged plan | schematic | section | ...
    level: str | None
    zone: str | None


@dataclass(frozen=True)
class Detection:
    id: str
    at: Placement
    kind: str  # object | riser | drop
    object_type: str
    category: str
    attributes: dict[str, Any]
    x: float
    y: float
    grid_reference: str | None
    grid_index: tuple[float, float] | None
    confidence: float
    method: str
    evidence: dict[str, Any] = field(default_factory=dict)
    rejected: bool = False  # a person said it is not there (P1-08)


@dataclass(frozen=True)
class Run:
    id: str
    at: Placement
    run_class: str  # main | branch
    dn: int | None
    size_status: str
    length_mm: int | None
    points: tuple[tuple[float, float], ...]  # sheet mm, along the run
    grid_reference: str | None
    grid_points: tuple[tuple[float, float] | None, ...]  # the points in grid units
    confidence: float
    labels: tuple[str, ...] = ()
    scale: float | None = None  # drawing mm per sheet mm: the view's verified scale


@dataclass(frozen=True)
class SpecValue:
    """A verified specification attribute, as P1-06's `attributes_for` gives it."""

    value: str
    clause: str
    citation: dict[str, Any]


@dataclass
class ItemDraft:
    key: str  # what the item is and where: stable across recomputes
    item_type: str
    classification: str
    description: str
    attributes: dict[str, dict[str, Any]]  # name -> {value, source, citation?}
    unit: str  # "no" or "m"
    net_quantity: Decimal
    length_mm: int | None
    level: str | None
    zone: str | None
    grid_from: str | None
    grid_to: str | None
    calculation_method: str  # count | centreline_length | rule_derived
    detection_method: str
    confidence: float
    members: list[dict[str, Any]]  # detections and runs it was made from
    sources: list[dict[str, Any]]  # sheets, with revision
    rule: dict[str, Any] | None = None  # rule key, version, status, inputs
    allowance_percent: Decimal | None = None
    geometry: list[dict[str, Any]] = field(default_factory=list)
    note: str | None = None

    def inputs_hash(self) -> str:
        """Everything the quantity depends on: the same hash means the same inputs."""
        payload = {
            "key": self.key,
            "attributes": {k: v.get("value") for k, v in sorted(self.attributes.items())},
            "quantity": str(self.net_quantity),
            "length": self.length_mm,
            "members": sorted(json.dumps(m, sort_keys=True, default=str) for m in self.members),
            "rule": self.rule,
            "allowance": str(self.allowance_percent),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def key_of(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:24]


LEVEL_IN_NUMBER = re.compile(r"(?:^|[-_])(L\d{1,2}|B\d{1,2}|RF)(?=[-_]|$)")


def level_of(sheet_number: str | None, *known: str | None) -> str | None:
    """The first level known (the view's, the title block's), else the one in the number."""
    for level in known:
        if level:
            return level
    match = LEVEL_IN_NUMBER.search(sheet_number or "")
    return match.group(1) if match else None
