"""The drawings against the specification: conflicts, missing items, ambiguities (FR-SPEC-03).

Deterministic comparisons of what the two say:

* **conflict:** a note on a drawing states a pipe material, class or joining method for a
  system and size that the specification states differently. A specification clause that
  applies to a place (the basement car park) is compared with the notes of the sheets of
  that place.
* **missing from the drawings:** the specification requires an item (a flow test header, a
  breeching inlet) and the takeoff has none.
* **missing from the specification:** the drawings show a type of item (a sprinkler type, a
  piece of equipment) that no clause of the specification mentions.
* **ambiguous:** a clause leaves the requirement open ("or equal", "as directed", "where
  required"), or the specification gives two values for one thing with nothing to choose
  between them.

A drawing note is read with the specification's own rules (`specs.attributes.read`), so the
two are compared like for like.

Every issue cites both sides: the clause and its words, and the sheet, its revision and the
note or items on it. Where one side is silent, the citation is what was looked through: the
specification revision with no such clause, or the Current sheets with no such item.

Pure: clauses, specification attributes, drawing notes and takeoff in; issues out.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from firebid.specs import attributes
from firebid.specs.clauses import Clause

RULES_VERSION = "crosscheck-rules-1"

COMPARED = ("pipe_material", "pipe_class", "joining_method")
WHAT = {
    "pipe_material": "pipe material",
    "pipe_class": "pipe class or schedule",
    "joining_method": "joining method",
}
SYSTEM_IN_NOTE: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("hose_reel", re.compile(r"\bHOSE\s*REEL", re.I)),
    ("hydrant", re.compile(r"\bHYDRANT", re.I)),
    ("wet_riser", re.compile(r"\b(WET\s+RIS|RISING\s+MAIN)", re.I)),
    ("sprinkler", re.compile(r"\bSPRINKLER", re.I)),
)
# A sentence requires an item when the item is what must be provided: its subject ("A flow
# test header shall be provided ...") or the object of the contractor's duty ("... shall
# provide a breeching inlet"). An item named in passing ("power supply to the fire pump
# shall be provided by others") is not required by that sentence.
SUBJECT = r"^(?:(?:a|an|the|each|every|all|one|two|\d+)\s+)?(?:[\w-]+\s+){{0,2}}?(?:{item})"
SUBJECT_VERB = re.compile(
    r"^[^.;]{0,60}?\b(?:shall\s+be|to\s+be|must\s+be)\s+(?:provided|installed|fitted|supplied)\b",
    re.I,
)
OBJECT = (
    r"\b(?:shall|must)\s+(?:provide|install|supply|furnish)(?:\s+and\s+install)?\s+"
    r"(?:(?:a|an|the|all|\d+)\s+)?(?:[\w-]+\s+){{0,2}}?(?:{item})"
)


def requires(sentence: str, item: re.Pattern[str]) -> bool:
    """Whether the sentence requires the item itself, not something for or near it."""
    subject = re.match(SUBJECT.format(item=item.pattern), sentence.strip(), re.I)
    if subject and SUBJECT_VERB.match(sentence.strip()[subject.end() :]):
        return True
    return re.search(OBJECT.format(item=item.pattern), sentence, re.I) is not None


# The words by which a specification names a type of item the takeoff counts.
ITEM_WORDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "test_header",
        re.compile(r"\b(flow\s+)?test\s+header\b|\bflow\s+test\s+(arrangement|facilit)", re.I),
    ),
    ("breeching_inlet", re.compile(r"\bbreeching\s+inlets?\b", re.I)),
    ("landing_valve", re.compile(r"\blanding\s+valves?\b", re.I)),
    ("hose_reel", re.compile(r"\bhose\s*reels?\b", re.I)),
    ("hydrant", re.compile(r"\b(pillar\s+)?hydrants?\b", re.I)),
    ("fire_pump", re.compile(r"\b(fire|sprinkler|hydrant|duty|standby)\s+pumps?\b", re.I)),
    ("jockey_pump", re.compile(r"\bjockey\s+pumps?\b", re.I)),
    ("pump_controller", re.compile(r"\bpump\s+(controllers?|control\s+panels?)\b", re.I)),
    ("fire_water_tank", re.compile(r"\b(fire\s+)?water\s+(storage\s+)?tanks?\b", re.I)),
    ("flow_switch", re.compile(r"\bflow\s+switch(es)?\b", re.I)),
    ("tamper_switch", re.compile(r"\b(tamper|supervisory)\s+switch(es)?\b", re.I)),
    ("pressure_reducing_valve", re.compile(r"\bpressure\s+reducing\s+valves?\b|\bPRVs?\b", re.I)),
    (
        "installation_control_valve_set",
        re.compile(r"\b(installation\s+control|alarm)\s+valves?\b", re.I),
    ),
    ("subsidiary_control_valve", re.compile(r"\b(subsidiary|zone)\s+(control\s+)?valves?\b", re.I)),
    ("gate_valve", re.compile(r"\bgate\s+valves?\b", re.I)),
    ("butterfly_valve", re.compile(r"\bbutterfly\s+valves?\b", re.I)),
    ("check_valve", re.compile(r"\b(check|non[-\s]?return)\s+valves?\b", re.I)),
    ("test_and_drain_valve", re.compile(r"\btest\s+(and|&)\s+drain\b", re.I)),
    ("air_compressor", re.compile(r"\bair\s+compressors?\b", re.I)),
    ("dry_pipe_valve_set", re.compile(r"\bdry[-\s]?pipe\s+valves?\b", re.I)),
    ("pre_action_valve_set", re.compile(r"\bpre[-\s]?action\s+valves?\b", re.I)),
    ("deluge_valve_set", re.compile(r"\bdeluge\s+valves?\b", re.I)),
    ("sprinkler_pendent", re.compile(r"\bpendent\b", re.I)),
    ("sprinkler_upright", re.compile(r"\bupright\b", re.I)),
    ("sprinkler_sidewall", re.compile(r"\bside[-\s]?wall\b", re.I)),
    ("sprinkler_concealed", re.compile(r"\bconcealed\b", re.I)),
)
# Types the specification must require by name for their absence to be an issue. Valves and
# heads are required by the drawings, not by a clause that happens to describe them.
REQUIRED_BY_CLAUSE = (
    "test_header",
    "breeching_inlet",
    "landing_valve",
    "hose_reel",
    "hydrant",
    "fire_pump",
    "jockey_pump",
    "pump_controller",
    "fire_water_tank",
    "flow_switch",
    "tamper_switch",
    "pressure_reducing_valve",
    "air_compressor",
)
AMBIGUOUS = re.compile(
    r"\b(or\s+equal|or\s+equivalent|or\s+approved\s+equal|as\s+directed|where\s+(?:required|necessary)|"
    r"as\s+(?:required|necessary)|to\s+the\s+(?:approval|satisfaction)\s+of|to\s+be\s+confirmed|TBA|TBC)\b",
    re.I,
)
SEVERITY = {
    "conflict": "high",
    "missing_from_drawings": "high",
    "missing_from_specification": "medium",
    "ambiguous_clause": "low",
    "ambiguous_values": "medium",
}


@dataclass(frozen=True)
class SpecValue:
    """One specification attribute, as cross-checking compares it."""

    system: str
    attribute: str
    value: str
    clause: str
    quote: str
    dn_min: int | None = None
    dn_max: int | None = None
    condition: str | None = None


@dataclass(frozen=True)
class SheetRef:
    sheet_id: str
    sheet_number: str
    revision: str
    title: str = ""
    level: str | None = None

    def cite(self) -> dict[str, Any]:
        return {
            "sheet_id": self.sheet_id,
            "sheet_number": self.sheet_number,
            "revision": self.revision,
        }


@dataclass(frozen=True)
class Note:
    sheet: SheetRef
    text: str
    x: float | None = None
    y: float | None = None


@dataclass(frozen=True)
class SpecRef:
    """The specification that was read: what an issue cites when no clause speaks."""

    document_id: str
    revision_id: str
    revision: str
    title: str = ""

    def cite(self, clause: str | None = None, quote: str | None = None) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "document_revision_id": self.revision_id,
            "revision": self.revision,
            "title": self.title,
            "clause": clause,
            "quote": quote,
        }


@dataclass
class Issue:
    category: str  # conflict | missing | ambiguous
    rule: str
    severity: str
    title: str
    system: str | None
    spec: dict[str, Any]
    drawing: dict[str, Any]
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        """What the issue is, stable across runs: its rule and what both sides cite."""
        material = [
            self.rule,
            self.system,
            self.spec.get("clause"),
            self.drawing.get("sheet_number"),
            self.detail.get("what"),
            self.detail.get("specified"),
            self.detail.get("drawn"),
        ]
        return hashlib.sha256(json.dumps(material, default=str).encode()).hexdigest()[:32]


def _system_of(note: str, default: str) -> str:
    return next((name for name, pattern in SYSTEM_IN_NOTE if pattern.search(note)), default)


def _overlap(a: tuple[int | None, int | None], b: tuple[int | None, int | None]) -> bool:
    low = max(a[0] or 0, b[0] or 0)
    high = min(a[1] if a[1] is not None else 10**6, b[1] if b[1] is not None else 10**6)
    return low <= high


def _applies(condition: str | None, sheet: SheetRef) -> bool:
    """Whether a clause limited to a place speaks of this sheet: every word of the place is
    in the sheet's title."""
    if not condition:
        return True
    words = set(re.findall(r"[a-z]+", sheet.title.lower()))
    return all(word in words for word in re.findall(r"[a-z]+", condition.lower()))


def conflicts(
    values: list[SpecValue], notes: list[Note], reference: SpecRef, default_system: str
) -> list[Issue]:
    issues = []
    for note in notes:
        system = _system_of(note.text, default_system)
        read = attributes.read(Clause("note", "", note.text, 0), system)
        for stated in (item for item in read if item.attribute in COMPARED):
            about = [
                value
                for value in values
                if value.system == system
                and value.attribute == stated.attribute
                and _overlap((value.dn_min, value.dn_max), (stated.dn_min, stated.dn_max))
            ]
            # A clause for this sheet's place is the one that governs it.
            placed = [v for v in about if v.condition and _applies(v.condition, note.sheet)]
            governing = placed or [v for v in about if not v.condition]
            if not governing or any(v.value == stated.value for v in governing):
                continue
            for value in governing:
                what = WHAT[stated.attribute]
                issues.append(
                    Issue(
                        category="conflict",
                        rule=f"conflict:{stated.attribute}",
                        severity=SEVERITY["conflict"],
                        title=(
                            f"{what.capitalize()}: the specification says "
                            f"{value.value.replace('_', ' ')}, sheet {note.sheet.sheet_number} "
                            f"says {stated.value.replace('_', ' ')}"
                        ),
                        system=system,
                        spec=reference.cite(value.clause, value.quote),
                        drawing={
                            **note.sheet.cite(),
                            "note": note.text,
                            "x": note.x,
                            "y": note.y,
                        },
                        detail={
                            "what": stated.attribute,
                            "specified": value.value,
                            "drawn": stated.value,
                            "condition": value.condition,
                            "dn_min": value.dn_min,
                            "dn_max": value.dn_max,
                        },
                    )
                )
    return issues


def _mentions(
    clauses: list[Clause], systems: dict[str, str]
) -> dict[str, list[tuple[Clause, str]]]:
    """Each item type with the fire protection sentences that name it."""
    found: dict[str, list[tuple[Clause, str]]] = {}
    for clause in clauses:
        if systems.get(clause.number) in ("other", "unknown") or not clause.text:
            continue
        for sentence in attributes.sentences(f"{clause.heading}. {clause.text}"):
            for kind, pattern in ITEM_WORDS:
                if pattern.search(sentence):
                    found.setdefault(kind, []).append((clause, sentence))
    return found


def missing(
    clauses: list[Clause],
    systems: dict[str, str],
    drawn: dict[str, list[SheetRef]],
    sheets: list[SheetRef],
    reference: SpecRef,
) -> list[Issue]:
    """Items one side has and the other does not. `drawn` is each item type the takeoff
    counts, with the sheets it is on; `sheets` is every Current sheet that was looked at."""
    issues = []
    mentions = _mentions(clauses, systems)
    examined = {
        "sheet_number": None,
        "sheets": [sheet.cite() for sheet in sheets],
        "note": f"none of the {len(sheets)} Current sheet(s) shows one",
    }
    for kind in REQUIRED_BY_CLAUSE:
        pattern = dict(ITEM_WORDS)[kind]
        required = [(c, s) for c, s in mentions.get(kind, []) if requires(s, pattern)]
        if not required or drawn.get(kind):
            continue
        clause, sentence = required[0]
        issues.append(
            Issue(
                category="missing",
                rule="missing_from_drawings",
                severity=SEVERITY["missing_from_drawings"],
                title=(
                    f"The specification requires a {kind.replace('_', ' ')}; the drawings show none"
                ),
                system=systems.get(clause.number),
                spec=reference.cite(clause.number, sentence),
                drawing=dict(examined),
                detail={"what": kind},
            )
        )
    for kind, where in sorted(drawn.items()):
        if not where or kind in mentions or kind not in {name for name, _ in ITEM_WORDS}:
            continue
        first = where[0]
        issues.append(
            Issue(
                category="missing",
                rule="missing_from_specification",
                severity=SEVERITY["missing_from_specification"],
                title=(
                    f"The drawings show {kind.replace('_', ' ')}, which the specification "
                    "never mentions"
                ),
                system=None,
                spec={**reference.cite(), "note": "no fire protection clause mentions it"},
                drawing={**first.cite(), "sheets": [sheet.cite() for sheet in where]},
                detail={"what": kind, "sheets": len(where)},
            )
        )
    return issues


def ambiguities(
    clauses: list[Clause],
    systems: dict[str, str],
    values: list[SpecValue],
    reference: SpecRef,
    sheets: list[SheetRef],
) -> list[Issue]:
    issues = []
    looked_at = {
        "sheet_number": None,
        "sheets": [sheet.cite() for sheet in sheets],
        "note": "the drawings do not settle it",
    }
    for clause in clauses:
        if systems.get(clause.number) in ("other", "unknown") or not clause.text:
            continue
        for sentence in attributes.sentences(clause.text):
            match = AMBIGUOUS.search(sentence)
            if match is None:
                continue
            issues.append(
                Issue(
                    category="ambiguous",
                    rule="ambiguous_clause",
                    severity=SEVERITY["ambiguous_clause"],
                    title=f"Clause {clause.number} leaves it open: “{match.group(0)}”",
                    system=systems.get(clause.number),
                    spec=reference.cite(clause.number, sentence),
                    drawing=dict(looked_at),
                    detail={"what": match.group(0).lower(), "specified": clause.number},
                )
            )
    # Two values for one thing, the same system, sizes and place: nothing says which.
    groups: dict[tuple[Any, ...], list[SpecValue]] = {}
    for value in values:
        if value.attribute in COMPARED:
            key = (value.system, value.attribute, value.dn_min, value.dn_max, value.condition)
            groups.setdefault(key, []).append(value)
    for (system, attribute, *_), stated in sorted(groups.items(), key=str):
        distinct = sorted({value.value for value in stated})
        if len(distinct) < 2:
            continue
        first = stated[0]
        issues.append(
            Issue(
                category="ambiguous",
                rule="ambiguous_values",
                severity=SEVERITY["ambiguous_values"],
                title=(
                    f"The specification gives {' and '.join(v.replace('_', ' ') for v in distinct)}"
                    f" for the {WHAT[attribute]} of one size range"
                ),
                system=system,
                spec={
                    **reference.cite(first.clause, first.quote),
                    "also": [{"clause": v.clause, "quote": v.quote} for v in stated[1:]],
                },
                drawing=dict(looked_at),
                detail={"what": attribute, "specified": ", ".join(distinct)},
            )
        )
    return issues


def check(
    clauses: list[Clause],
    systems: dict[str, str],
    values: list[SpecValue],
    notes: list[Note],
    drawn: dict[str, list[SheetRef]],
    sheets: list[SheetRef],
    reference: SpecRef,
    default_system: str = "sprinkler",
) -> list[Issue]:
    """Every issue between a specification and the drawings, most severe first."""
    found = [
        *conflicts(values, notes, reference, default_system),
        *missing(clauses, systems, drawn, sheets, reference),
        *ambiguities(clauses, systems, values, reference, sheets),
    ]
    unique: dict[str, Issue] = {}
    for issue in found:
        unique.setdefault(issue.key, issue)
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(unique.values(), key=lambda i: (order[i.severity], i.category, i.title))
