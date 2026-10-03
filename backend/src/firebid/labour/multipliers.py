"""Multipliers on baseline hours for site conditions (FR-LAB-02).

The catalogue is configuration (`config/labour.yaml`): installation height bands, access,
MEP congestion, basement, an occupied or live building, night work, high-rise logistics.
Each has a value, a source and a rationale; one without a source is refused when the
catalogue is read. A multiplier is applied to a bid or one of its levels only once an
estimator confirms it, and a labour line shows each one it carries, never a product alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from firebid.labour.productivity import Unsourced

CONFIG = Path(__file__).resolve().parents[3] / "config" / "labour.yaml"


@lru_cache(maxsize=2)
def settings(path: Path = CONFIG) -> dict[str, Any]:
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


@dataclass(frozen=True)
class Multiplier:
    key: str
    label: str
    kind: str  # height | condition
    value: Decimal
    source: str
    rationale: str
    up_to_mm: int | None = None  # a height band's upper limit; None for the last band

    def as_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "kind": self.kind,
            "value": str(self.value),
            "source": self.source,
            "rationale": self.rationale,
            "up_to_mm": self.up_to_mm,
        }


def _one(item: dict[str, Any], kind: str) -> Multiplier:
    key = str(item.get("key") or "")
    if not str(item.get("source") or "").strip():
        raise Unsourced(f"multiplier {key!r} has no source")
    if not str(item.get("rationale") or "").strip():
        raise Unsourced(f"multiplier {key!r} has no rationale")
    value = Decimal(str(item.get("value")))
    if value <= 0:
        raise ValueError(f"multiplier {key!r} is not more than zero")
    limit = item.get("up_to_mm")
    return Multiplier(
        key=key,
        label=str(item.get("label") or key),
        kind=kind,
        value=value,
        source=str(item["source"]).strip(),
        rationale=str(item["rationale"]).strip(),
        up_to_mm=int(limit) if limit is not None else None,
    )


def catalogue(config: dict[str, Any] | None = None) -> list[Multiplier]:
    """Every multiplier, height bands first in order of height. Refuses one unsourced."""
    found = (config if config is not None else settings()).get("multipliers") or {}
    bands = [_one(item, "height") for item in found.get("height_bands") or []]
    bands.sort(key=lambda band: band.up_to_mm if band.up_to_mm is not None else 10**9)
    conditions = [_one(item, "condition") for item in found.get("conditions") or []]
    keys = [m.key for m in (*bands, *conditions)]
    if len(set(keys)) != len(keys):
        raise ValueError("two multipliers share a key")
    return [*bands, *conditions]


def by_key(config: dict[str, Any] | None = None) -> dict[str, Multiplier]:
    return {m.key: m for m in catalogue(config)}


def height_band(height_mm: Decimal | float, items: list[Multiplier]) -> Multiplier | None:
    """The band an installation height falls in: the first whose limit it does not pass."""
    bands = [m for m in items if m.kind == "height"]
    for band in bands:
        if band.up_to_mm is None or Decimal(str(height_mm)) <= band.up_to_mm:
            return band
    return bands[-1] if bands else None


@dataclass(frozen=True)
class Condition:
    """A multiplier confirmed for a bid (level None) or for one of its levels."""

    key: str
    level: str | None
    confirmed_by: str
    basis: str = ""


@dataclass(frozen=True)
class Applied:
    key: str
    label: str
    value: Decimal
    source: str
    rationale: str
    scope: str  # "the whole bid", or the level
    confirmed_by: str
    basis: str = ""

    def as_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "value": str(self.value),
            "source": self.source,
            "rationale": self.rationale,
            "scope": self.scope,
            "confirmed_by": self.confirmed_by,
            "basis": self.basis,
        }


def applied_to(
    level: str | None, conditions: list[Condition], items: dict[str, Multiplier]
) -> list[Applied]:
    """The multipliers a line on `level` carries: those confirmed for its level and for the
    whole bid. A level's own entry stands in for the bid's for the same multiplier, and a
    line carries one height band at most, its level's before the bid's."""
    chosen: dict[str, Condition] = {}
    for condition in conditions:
        if condition.key not in items:
            continue
        if condition.level is not None and condition.level != level:
            continue
        held = chosen.get(condition.key)
        if held is None or (held.level is None and condition.level is not None):
            chosen[condition.key] = condition
    heights = [c for c in chosen.values() if items[c.key].kind == "height"]
    if len(heights) > 1:
        own = [c for c in heights if c.level is not None]
        keep = (own or heights)[0]
        for condition in heights:
            if condition is not keep:
                del chosen[condition.key]
    order = list(items)
    return [
        Applied(
            key=c.key,
            label=items[c.key].label,
            value=items[c.key].value,
            source=items[c.key].source,
            rationale=items[c.key].rationale,
            scope=c.level or "the whole bid",
            confirmed_by=c.confirmed_by,
            basis=c.basis,
        )
        for c in sorted(chosen.values(), key=lambda c: order.index(c.key))
    ]


def _plain(value: Decimal) -> str:
    """A figure as a person writes it: 5200, not 5200.000."""
    return format(Decimal(str(value)).normalize(), "f")


@dataclass(frozen=True)
class Proposal:
    key: str
    level: str | None
    basis: str


def proposals(
    ceiling_heights: dict[str | None, tuple[Decimal, str]],
    levels: list[str],
    levels_served: tuple[Decimal, str] | None,
    config: dict[str, Any] | None = None,
) -> list[Proposal]:
    """What a bid's parameters suggest, for an estimator to confirm: a height band where a
    ceiling height is stated, basement for a level named as one, and high-rise logistics
    where the building serves enough levels. Nothing here is applied until confirmed."""
    found = config if config is not None else settings()
    items = catalogue(found)
    rules = found.get("proposals") or {}
    out: list[Proposal] = []
    for level, (height, source) in ceiling_heights.items():
        band = height_band(height, items)
        if band is not None:
            out.append(Proposal(band.key, level, f"ceiling height {_plain(height)} mm ({source})"))
    pattern = rules.get("basement_levels")
    if pattern and any(m.key == "basement" for m in items):
        for level in levels:
            if re.match(str(pattern), level.strip(), re.IGNORECASE):
                out.append(Proposal("basement", level, f"the level is named {level}"))
    threshold = rules.get("high_rise_from_levels")
    if (
        threshold is not None
        and levels_served is not None
        and levels_served[0] >= Decimal(str(threshold))
        and any(m.key == "high_rise" for m in items)
    ):
        out.append(
            Proposal(
                "high_rise",
                None,
                f"{_plain(levels_served[0])} levels served ({levels_served[1]}); "
                f"high-rise from {threshold}",
            )
        )
    return out
