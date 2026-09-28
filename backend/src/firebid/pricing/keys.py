"""What a rate is for: the item key a BOQ line and a rate library entry share.

A key is six parts: canonical type, nominal size, material, schedule, joining and brand. A
part nobody specified is blank. Two keys are the same item only when every part is equal,
blanks included: a blank against a value is a partial match, for a person to decide.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields

NOT_SPECIFIED = ("", "not specified", "n/a", "na", "-", "none", "tbc")
PARTS = ("type", "dn", "material", "schedule", "joining", "brand")

# Units as a quantity surveyor and the takeoff write them.
UNITS = {
    "no": "no",
    "nr": "no",
    "nos": "no",
    "no.": "no",
    "each": "no",
    "ea": "no",
    "pc": "no",
    "pcs": "no",
    "set": "set",
    "sets": "set",
    "m": "m",
    "lm": "m",
    "m run": "m",
    "metre": "m",
    "metres": "m",
    "m2": "m2",
    "sum": "sum",
    "ls": "sum",
    "lot": "lot",
}


def unit_of(unit: str | None) -> str | None:
    """The canonical unit, or None when it is not one a rate can be quoted in."""
    if unit is None:
        return None
    return UNITS.get(" ".join(unit.lower().split()))


def _part(value: object) -> str:
    text = " ".join(str(value or "").split()).lower()
    return "" if text in NOT_SPECIFIED else text


def _size(value: object) -> str:
    """`150`, `DN150`, `150 mm`, `DN100xDN50`, `100 x 50` -> `150` or `100x50`."""
    text = _part(value)
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    return "x".join(numbers) if numbers else text


@dataclass(frozen=True)
class ItemKey:
    type: str
    dn: str = ""
    material: str = ""
    schedule: str = ""
    joining: str = ""
    brand: str = ""

    @classmethod
    def of(cls, **parts: object) -> ItemKey:
        return cls(
            type=_part(parts.get("type")).replace(" ", "_"),
            dn=_size(parts.get("dn")),
            material=_part(parts.get("material")),
            schedule=_part(parts.get("schedule")),
            joining=_part(parts.get("joining")),
            brand=_part(parts.get("brand")),
        )

    @classmethod
    def from_item(cls, item_type: str, attributes: Mapping[str, str]) -> ItemKey:
        """A takeoff item's key: its type (a fitting's own kind folded in) and attributes."""
        kind = _part(item_type)
        fitting = _part(attributes.get("fitting"))
        if kind == "fitting" and fitting:
            kind = f"fitting_{fitting}"
        return cls.of(
            type=kind,
            dn=attributes.get("nominal_diameter_mm") or attributes.get("size"),
            material=attributes.get("pipe_material") or attributes.get("material"),
            schedule=attributes.get("pipe_class") or attributes.get("schedule"),
            joining=attributes.get("joining_method") or attributes.get("joining"),
            brand=attributes.get("brand"),
        )

    @classmethod
    def common(cls, keys: Iterable[ItemKey]) -> ItemKey | None:
        """What a set of items agree on: a part they disagree on is blank."""
        found = list(keys)
        if not found:
            return None
        values = {
            name: {getattr(key, name) for key in found} for name in (f.name for f in fields(cls))
        }
        return cls(**{name: next(iter(v)) if len(v) == 1 else "" for name, v in values.items()})

    @classmethod
    def parse(cls, text: str) -> ItemKey:
        return cls(*(text.split("|") + [""] * len(PARTS))[: len(PARTS)])

    def text(self) -> str:
        return "|".join(getattr(self, name) for name in PARTS)

    def parts(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in PARTS}

    def partial(self, other: ItemKey) -> bool:
        """The same kind and size of item, differing (or unstated) in something else."""
        if self == other or not self.type or self.type != other.type:
            return False
        return not (self.dn and other.dn and self.dn != other.dn)

    def label(self) -> str:
        words = [self.type.replace("_", " ")]
        if self.dn:
            words.append(f"DN{self.dn}")
        words += [
            value for value in (self.material, self.schedule, self.joining, self.brand) if value
        ]
        return ", ".join(words)
