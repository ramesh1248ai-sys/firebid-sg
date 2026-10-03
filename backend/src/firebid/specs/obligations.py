"""The obligations a specification places on the contractor, read from clauses (FR-SPEC-02).

Beyond what is installed, a specification says what must be done: test, flush, paint, label
and commission the installation; use approved makes; warrant it and maintain it through the
defects liability period; hand over spares; train the client's staff; submit drawings,
calculations and product data; and see it through the authority's inspections.

A clause is read sentence by sentence. A sentence is an obligation of a category when it
uses that category's words; a quantity it states (a test pressure, a duration, a period, a
number of sets) is kept with its unit. Every obligation cites its clause, with the sentence
as its quote, and is a proposal until a person confirms it. What the rules do not read is
left for the model (route `spec_obligation_extract`), whose answers are checked against
their clauses the same way.

Pure: clauses in, obligations out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from firebid.specs.attributes import sentences
from firebid.specs.clauses import Clause

RULES_VERSION = "obligation-rules-1"

# Each category, and the words that put a sentence in it. The first category whose words a
# sentence uses is its own; a sentence is read into as many categories as it speaks of.
CATEGORIES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "testing",
        re.compile(
            r"\b(hydrostatic\w*|pressure\s+test\w*|tested\s+(?:at|to)|test\s+pressure)\b", re.I
        ),
    ),
    ("flushing", re.compile(r"\bflush(?:ed|ing)?\b", re.I)),
    ("painting", re.compile(r"\b(paint(?:ed|ing)?|primer|coats?\s+of)\b", re.I)),
    (
        "identification",
        re.compile(
            r"\b(identif\w+|label(?:led|ling|s)?|colour[-\s]?band\w*|name\s*plates?|tagg?ed)\b",
            re.I,
        ),
    ),
    ("commissioning", re.compile(r"\bcommission(?:ed|ing)?\b", re.I)),
    (
        "approved_makes",
        re.compile(r"\b(approved\s+(?:makes?|manufacturers?|brands?|vendors?)|AVL)\b", re.I),
    ),
    ("warranty", re.compile(r"\b(warrant(?:y|ies|ed)?|guarantee[ds]?)\b", re.I)),
    ("defects_liability", re.compile(r"\bdefects?\s+liability\b|\bDLP\b", re.I)),
    ("maintenance", re.compile(r"\b(maintenance|servicing)\b", re.I)),
    ("spares", re.compile(r"\bspares?\b|\bspare\s+(?:parts?|sprinklers?|heads?)\b", re.I)),
    ("training", re.compile(r"\b(training|instruct(?:ion)?s?\s+to|train\s+the)\b", re.I)),
    (
        "submittals",
        re.compile(
            r"\b(submit(?:ted|tal|tals)?|shop\s+drawings?|hydraulic\s+calculations?|product\s+data|as[-\s]built)\b",
            re.I,
        ),
    ),
    (
        "authority",
        re.compile(
            r"\b(SCDF|FSSD|fire\s+safety\s+certificate|FSC|authorit(?:y|ies)|registered\s+inspector|statutory)\b",
            re.I,
        ),
    ),
)
CATEGORY_KEYS = tuple(name for name, _ in CATEGORIES)
LABELS = {
    "testing": "Testing",
    "flushing": "Flushing",
    "painting": "Painting",
    "identification": "Identification",
    "commissioning": "Commissioning",
    "approved_makes": "Approved makes",
    "warranty": "Warranty",
    "defects_liability": "Defects liability period",
    "maintenance": "Maintenance",
    "spares": "Spares",
    "training": "Training",
    "submittals": "Submittals",
    "authority": "Authority inspections and FSC",
}
# A sentence obliges when it says so: "shall", "must", "is to", "are to", "to be".
OBLIGES = re.compile(
    r"\b(shall|must|is\s+to|are\s+to|to\s+be|required\s+to|responsible\s+for)\b", re.I
)

# Quantities, each with the unit it is kept in.
PRESSURE = re.compile(r"(\d+(?:\.\d+)?)\s*(bar|kPa|MPa|psi)\b", re.I)
FACTOR = re.compile(r"(\d+(?:\.\d+)?)\s*times\s+(?:the\s+)?(?:maximum\s+)?working\s+pressure", re.I)
HOURS = re.compile(r"(\d+(?:\.\d+)?)\s*[-\s]?(hours?|hrs?|minutes?|mins?)\b", re.I)
PERIOD = re.compile(r"(\d+)\s*[-\s]?(months?|years?|weeks?|days?)\b", re.I)
SETS = re.compile(r"(\d+)\s*(sets?|copies|nos?\.?|numbers?)\b", re.I)
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|per\s*cent)", re.I)
COATS = re.compile(r"(\d+|one|two|three)\s+coats?\b", re.I)
WORDS = {"one": 1, "two": 2, "three": 3}


@dataclass(frozen=True)
class Obligation:
    system: str
    category: str
    summary: str
    clause: str
    quote: str
    quantities: dict[str, Any] = field(default_factory=dict)
    method: str = "rule"
    confidence: float = 0.85


def _number(text: str) -> float | int:
    value = float(text)
    return int(value) if value.is_integer() else value


def quantities(sentence: str, category: str) -> dict[str, Any]:
    """What a sentence states in numbers, as far as its category makes them mean something."""
    found: dict[str, Any] = {}
    if category in ("testing", "flushing", "commissioning"):
        pressure = PRESSURE.search(sentence)
        if pressure:
            found["pressure"] = _number(pressure.group(1))
            found["pressure_unit"] = pressure.group(2)
        factor = FACTOR.search(sentence)
        if factor:
            found["times_working_pressure"] = _number(factor.group(1))
        duration = HOURS.search(sentence)
        if duration:
            unit = "hours" if duration.group(2).lower().startswith("h") else "minutes"
            found[f"duration_{unit}"] = _number(duration.group(1))
    if category in ("warranty", "defects_liability", "maintenance", "training", "submittals"):
        period = PERIOD.search(sentence)
        if period:
            unit = period.group(2).lower().rstrip("s") + "s"
            found[f"period_{unit}"] = int(period.group(1))
    if category in ("spares", "submittals", "training"):
        sets = SETS.search(sentence)
        if sets:
            found["count"] = int(sets.group(1))
            found["count_of"] = sets.group(2).lower().rstrip(".")
        percent = PERCENT.search(sentence)
        if percent and category == "spares":
            found["percent"] = _number(percent.group(1))
    if category == "painting":
        coats = COATS.search(sentence)
        if coats:
            word = coats.group(1).lower()
            found["coats"] = WORDS.get(word) or int(word)
    return found


def read(clause: Clause, system: str) -> list[Obligation]:
    """Every obligation the rules can read from one clause of a fire protection section."""
    if system in ("other", "unknown") or not clause.text:
        return []
    found: list[Obligation] = []
    for sentence in sentences(clause.text):
        if not OBLIGES.search(sentence):
            continue
        for category, pattern in CATEGORIES:
            if not pattern.search(sentence):
                continue
            # "Defects liability" is its own category: the maintenance a sentence owes
            # during it is not a second obligation of the same words.
            if category == "maintenance" and re.search(r"defects?\s+liability", sentence, re.I):
                continue
            found.append(
                Obligation(
                    system=system,
                    category=category,
                    summary=sentence if len(sentence) <= 240 else sentence[:237] + "...",
                    clause=clause.number,
                    quote=sentence,
                    quantities=quantities(sentence, category),
                )
            )
    return found


def extract(clauses: list[Clause], systems: dict[str, str]) -> list[Obligation]:
    found: list[Obligation] = []
    for clause in clauses:
        found.extend(read(clause, systems.get(clause.number, "unknown")))
    return found


def unread(clauses: list[Clause], systems: dict[str, str], found: list[Obligation]) -> list[Clause]:
    """Fire protection clauses that oblige something the rules put in no category."""
    read_from = {item.clause for item in found}
    return [
        clause
        for clause in clauses
        if clause.text
        and systems.get(clause.number) not in ("other", "unknown")
        and clause.number not in read_from
        and OBLIGES.search(clause.text)
    ]


def quote_holds(clause: Clause | None, quote: str) -> tuple[bool, str]:
    """Whether a citation resolves: the clause exists and says the words quoted."""
    if clause is None:
        return False, "the clause is not in the specification"
    squashed = " ".join(quote.split()).lower()
    if squashed and squashed in " ".join(f"{clause.heading} {clause.text}".split()).lower():
        return True, "the clause states it"
    return False, f"clause {clause.number} does not contain the words quoted"
