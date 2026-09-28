"""The client's quantities against the measured ones (FR-BOQ-03), and the conventions they
were measured by (FR-BOQ-06).

Variance is measured minus client, as a percentage of the client's quantity, rounded half up
to one decimal place: a client's 120 m against a measured 128.4 m is +7.0%. Over the
threshold either way, it is a clarification candidate (P2-06 raises the clarification).

Pure: numbers and settings in, rows and text out.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CONFIG = Path(__file__).resolve().parents[3] / "config" / "boq.yaml"


@lru_cache(maxsize=2)
def settings(path: Path = CONFIG) -> dict[str, Any]:
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def threshold() -> Decimal:
    return Decimal(str(settings().get("variance_threshold_percent", 5)))


@dataclass(frozen=True)
class Variance:
    difference: Decimal
    percent: Decimal | None  # None when the client gave no quantity (or zero)
    flagged: bool


def variance(
    client: Decimal | None, measured: Decimal | None, limit: Decimal | None = None
) -> Variance:
    """Measured against the client's quantity; flagged over the threshold either way."""
    limit = threshold() if limit is None else limit
    measured = measured or Decimal(0)
    if client is None or client == 0:
        return Variance(measured, None, measured != 0)
    difference = (measured - client).quantize(Decimal("0.001"))
    percent = (difference / client * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return Variance(difference, percent, abs(percent) > limit)


def conventions() -> dict[str, dict[str, Any]]:
    return dict(settings().get("conventions") or {})


def defaults() -> dict[str, str]:
    return {name: str(option["default"]) for name, option in conventions().items()}


def validate(chosen: dict[str, str]) -> dict[str, str]:
    """The full set, defaults filled in; an unknown setting or option is refused."""
    known = conventions()
    for name, value in chosen.items():
        if name not in known:
            raise ValueError(f"unknown convention {name!r}")
        if value not in known[name]["options"]:
            raise ValueError(f"{name}: {value!r} is not one of {', '.join(known[name]['options'])}")
    return {**defaults(), **chosen}


def qualification_text(chosen: dict[str, str]) -> str:
    """The measurement conventions, worded for the tender qualifications."""
    full = validate(chosen)
    known = conventions()
    lines = [
        f"{index}. {known[name]['options'][value]}"
        for index, (name, value) in enumerate(full.items(), start=1)
    ]
    return "Measurement conventions\n" + "\n".join(lines)
