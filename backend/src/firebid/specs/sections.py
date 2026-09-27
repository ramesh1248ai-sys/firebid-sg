"""Which clauses are about which fire protection system (P1-06 build item 2).

A top-level section's heading decides its system, and every clause under it inherits that
system. Headings the rules place firmly in another trade (electrical, air-conditioning,
plumbing) are `other`. Headings neither way are `unknown`: the model is asked about those
(route `spec_section_find`), and until it answers they are not read.
"""

from __future__ import annotations

import re

from firebid.specs.clauses import Clause

SYSTEMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("sprinkler", re.compile(r"\bSPRINKLER", re.I)),
    ("hose_reel", re.compile(r"\bHOSE\s*REEL", re.I)),
    ("hydrant", re.compile(r"\bHYDRANT", re.I)),
    ("wet_riser", re.compile(r"\bWET\s+RISER", re.I)),
    ("dry_riser", re.compile(r"\bDRY\s+RISER", re.I)),
    ("fire_pump", re.compile(r"\bFIRE\s+PUMP", re.I)),
    ("fire_protection", re.compile(r"\bFIRE\s+(PROTECTION|FIGHTING)", re.I)),
)
OTHER_TRADES = re.compile(
    r"\b(ELECTRICAL|LOW\s+VOLTAGE|LIGHTING|AIR[-\s]?CONDITIONING|ACMV|VENTILATION|PLUMBING|"
    r"SANITARY|LIFTS?|ESCALATOR|GAS|BUILDING\s+AUTOMATION|FIRE\s+ALARM)\b",
    re.I,
)
# Sections that apply to every system: their clauses stay with the fire protection scope.
GENERAL = re.compile(r"\b(GENERAL|PRELIMINAR|SCOPE|STANDARDS?)\b", re.I)


def system_of_heading(heading: str) -> str:
    """`sprinkler`, `hose_reel` ... from a section heading; `general`, `other` or `unknown`."""
    if OTHER_TRADES.search(heading) and not re.search(
        r"SPRINKLER|HOSE|HYDRANT|RISER", heading, re.I
    ):
        return "other"
    for system, pattern in SYSTEMS:
        if pattern.search(heading):
            return system
    if GENERAL.search(heading):
        return "general"
    return "unknown"


def systems(clauses: list[Clause], decided: dict[str, str] | None = None) -> dict[str, str]:
    """Each clause's system, by its top-level section. `decided` holds a person's or the
    model's answer for top-level sections the rules left unknown."""
    by_section: dict[str, str] = {}
    for clause in clauses:
        if clause.level == 1:
            known = (decided or {}).get(clause.number)
            by_section[clause.number] = known or system_of_heading(clause.heading)
    result = {}
    for clause in clauses:
        top = clause.number.split(".", 1)[0]
        result[clause.number] = by_section.get(top, "unknown")
    return result


def unknown_sections(clauses: list[Clause], decided: dict[str, str] | None = None) -> list[Clause]:
    placed = systems(clauses, decided)
    return [c for c in clauses if c.level == 1 and placed[c.number] == "unknown"]
