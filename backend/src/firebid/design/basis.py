"""The design basis: the rules a layout follows and the criteria the tender states (P1-12).

* **Rules** come from `config/design_rules.yaml`: the seed of an organisation's set, every
  value "to be confirmed" until a senior estimator confirms it.
* **Criteria** are read from a sheet's notes, where a design-intent tender states them
  ("MAXIMUM SPACING: (4M X 3M)", "MAXIMUM AREA COVERAGE PER SPRINKLER - 12M²"). Each is a
  proposal citing the sheet and the words it was read from; a person confirms which applies
  before any layout is made. Where the notes state none, the rules' default stands, and
  says so.

Pure: text and YAML in, criteria and rules out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from firebid.design.layout import Criterion, HeadRule, Rules
from firebid.design.rooms import Settings

SEED = Path(__file__).resolve().parents[3] / "config" / "design_rules.yaml"


def load_rules(data: dict[str, Any] | None = None, *, version: int = 1) -> Rules:
    """The design rules from their YAML form (the seed, or a stored version)."""
    data = data if data is not None else seed()
    default = data["default_head"]
    return Rules(
        version=version,
        status=str(data.get("status", "to be confirmed")),
        grid_mm=(int(data["grid_mm"][0]), int(data["grid_mm"][1])),
        grid_from_m2=float(data["grid_from_m2"]),
        omit_names=tuple(str(p) for p in data.get("omit_names", [])),
        head_rules=tuple(
            HeadRule(
                when_name=rule.get("when_name"),
                when_level=rule.get("when_level"),
                object_type=str(rule["object_type"]),
                temperature_c=int(rule["temperature_c"]),
                note=str(rule.get("note", "")),
            )
            for rule in data.get("head_rules", [])
        ),
        default_head=HeadRule(
            None,
            None,
            str(default["object_type"]),
            int(default["temperature_c"]),
            str(default.get("note", "")),
        ),
        range_limits=tuple((int(dn), int(most)) for dn, most in data["range_limits"]),
        feed_reach_mm=int(data["feed_reach_mm"]),
        remote_feed_mm_per_head=int(data["remote_feed_mm_per_head"]),
        remote_feed_dn=int(data["remote_feed_dn"]),
    )


def seed() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(SEED.read_text(encoding="utf-8"))
    return loaded


def space_settings(data: dict[str, Any] | None = None) -> Settings:
    values = (data if data is not None else seed()).get("spaces", {})
    return Settings(**{name: float(value) for name, value in values.items()})


def default_criterion(data: dict[str, Any] | None = None) -> Criterion:
    item = (data if data is not None else seed())["default_criterion"]
    return Criterion(
        key=str(item["key"]),
        title=str(item["title"]),
        max_area_m2=float(item["max_area_m2"]),
        max_spacing_mm=(int(item["max_spacing_mm"][0]), int(item["max_spacing_mm"][1])),
        source=f"design rules default ({(data or seed()).get('status', 'to be confirmed')})",
    )


@dataclass(frozen=True)
class NoteLine:
    text: str
    x: float
    y: float
    height: float


RULE_KEY = "sprinkler_layout"


def rule_record(criterion: Criterion, rules: Rules) -> dict[str, Any]:
    """The rule a layout followed, as a rule-derived quantity records it (FR-QTO-03's form).

    The rule and its version, and every input with its value and where it came from: the
    criterion from the tender's notes (or the rules' default), and the rules' own grid.
    """
    rule = f"design rule v{rules.version} ({rules.status})"
    return {
        "rule_key": RULE_KEY,
        "rule_version": rules.version,
        "rule_status": rules.status,
        "criterion": criterion.key,
        "criterion_title": criterion.title,
        "inputs": [
            {"name": "max_area_m2", "value": criterion.max_area_m2, "source": criterion.source},
            {
                "name": "max_spacing_along_mm",
                "value": float(criterion.max_spacing_mm[0]),
                "source": criterion.source,
            },
            {
                "name": "max_spacing_across_mm",
                "value": float(criterion.max_spacing_mm[1]),
                "source": criterion.source,
            },
            {"name": "grid_along_mm", "value": float(rules.grid_mm[0]), "source": rule},
            {"name": "grid_across_mm", "value": float(rules.grid_mm[1]), "source": rule},
            {"name": "grid_from_m2", "value": rules.grid_from_m2, "source": rule},
        ],
    }


def criterion_json(criterion: Criterion) -> dict[str, Any]:
    return {
        "key": criterion.key,
        "title": criterion.title,
        "max_area_m2": criterion.max_area_m2,
        "max_spacing_mm": list(criterion.max_spacing_mm),
        "source": criterion.source,
        "k_factor": criterion.k_factor,
        "response": criterion.response,
    }


def criterion_from_json(data: dict[str, Any]) -> Criterion:
    spacing = data["max_spacing_mm"]
    return Criterion(
        key=str(data["key"]),
        title=str(data["title"]),
        max_area_m2=float(data["max_area_m2"]),
        max_spacing_mm=(int(spacing[0]), int(spacing[1])),
        source=str(data["source"]),
        k_factor=data.get("k_factor"),
        response=data.get("response"),
    )


# A note that leaves the design to the contractor.
INTENT = re.compile(
    r"DESIGN\s+INTENT|FURTHER\s+DEVELOPMENT\s+AND\s+DETAILED\s+DESIGN"
    r"|CONTRACTOR\s+SHALL\s+(?:BE\s+RESPONSIBLE\s+FOR\s+(?:THE\s+)?|UNDERTAKE\s+(?:THE\s+)?)"
    r"(?:FURTHER\s+|DETAILED\s+)*DESIGN|INDICATIVE\s+ONLY",
    re.IGNORECASE,
)


def design_intent(lines: list[NoteLine]) -> str | None:
    """The words of a note that leaves the design to the contractor, if the sheet has one."""
    for line in sorted(lines, key=lambda item: (round(item.y, 1), item.x)):
        if INTENT.search(line.text):
            return line.text.strip()
    return None


# --- Criteria from the notes -------------------------------------------------------------------

SPACING = re.compile(
    r"MAX(?:IMUM)?\.?\s+SPACING\s*[:\-]?\s*\(?\s*(\d+(?:\.\d+)?)\s*M\s*"
    r"[Xx×]\s*(\d+(?:\.\d+)?)\s*M",  # noqa: RUF001
    re.IGNORECASE,
)
AREA = re.compile(
    r"MAX(?:IMUM)?\.?\s+AREA\s+(?:OF\s+)?COVERAGE\s+PER\s+(?:SPRINKLER|HEAD)\s*[:\-]?\s*"
    r"(\d+(?:\.\d+)?)\s*M",
    re.IGNORECASE,
)
K_FACTOR = re.compile(r"K[\s-]*FACTOR\s*[:\-]?\s*([\d.]+(?:\s*\(\d+\))?)", re.IGNORECASE)
RESPONSE = re.compile(r"SPRINKLER\s+TYPE\s*[:\-]?\s*(.+)$", re.IGNORECASE)
# A line that names what the following values are for.
HEADING = re.compile(r"CRITERIA|LAYER|HAZARD|CEILING HEIGHT|AREA OF\b(?!\s+OPERATION)", re.I)


def criteria_from_notes(lines: list[NoteLine], sheet: str) -> list[Criterion]:
    """The design criteria a sheet's notes state, each citing the words it was read from.

    The notes are read as a column: from each line naming a spacing, upwards to the nearest
    heading ("SPRINKLER DESIGN CRITERIA FOR ...", "EXPOSED LAYER OF ...") for its title, and
    on to the area per head. A criterion needs both a spacing and an area: either alone is
    not enough to lay out heads.
    """
    ordered = sorted(lines, key=lambda line: (round(line.y, 1), line.x))
    found: list[Criterion] = []
    for line in ordered:
        spacing = SPACING.search(line.text)
        if not spacing:
            continue
        column = [other for other in ordered if abs(other.x - line.x) < 4 * max(line.height, 1.0)]
        position = column.index(line)
        title = next(
            (c.text for c in reversed(column[:position]) if HEADING.search(c.text)), "criterion"
        )
        # The block: from its title down to the next title (or six lines on).
        start = next(
            (i for i in range(position, -1, -1) if HEADING.search(column[i].text)), position
        )
        end = next(
            (
                i
                for i in range(position + 1, min(len(column), position + 8))
                if HEADING.search(column[i].text)
            ),
            min(len(column), position + 8),
        )
        block = [c.text for c in column[start:end]]
        area = next((AREA.search(t) for t in block if AREA.search(t)), None)
        if area is None:
            continue
        k = next((K_FACTOR.search(t) for t in block if K_FACTOR.search(t)), None)
        response = next((RESPONSE.search(t) for t in block if RESPONSE.search(t)), None)
        along = round(float(spacing.group(1)) * 1000)
        across = round(float(spacing.group(2)) * 1000)
        found.append(
            Criterion(
                key=f"note_{len(found) + 1}",
                title=title.strip().rstrip(":"),
                max_area_m2=float(area.group(1)),
                max_spacing_mm=(max(along, across), min(along, across)),
                source=f"sheet {sheet} notes: '{line.text.strip()}', '{area.group(0).strip()}'",
                k_factor=k.group(1).strip() if k else None,
                response=response.group(1).strip() if response else None,
            )
        )
    return found
