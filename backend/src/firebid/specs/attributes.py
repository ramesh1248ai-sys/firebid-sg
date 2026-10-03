"""The attributes takeoff needs, read from clauses by rule (P1-06 build item 3, rules first).

What is read, per fire protection system:

* pipe material (`black_steel`, `galvanised_steel`, `ductile_iron`, `copper`, `cpvc`,
  `stainless_steel`), standard (BS EN 10255, ASTM A53 Grade B, BS EN 545 ...) and class or
  schedule (Heavy, Medium, Schedule 40 ...);
* joining method (`threaded`, `grooved`, `flanged`, `welded`);
* sprinkler type, response, K-factor, temperature rating and finish;
* hanger spacing by pipe size (`hanger_spacing_mm`), and whether seismic restraint is
  required (`seismic_restraint`), which takeoff derives supports from (P2-01);
* approved makes, kept for later phases.

A clause is read sentence by sentence. A sentence's size range (`up to and including DN 50`,
`65 mm and above`, `≤ 50`, `DN 100 and above`, `throughout`) applies to what it says; a
place (`within the basement car park`) is kept as a condition. Every attribute cites the
clause it came from, with the sentence as its quote.

What the rules do not read is left for the model (route `spec_attribute_extract`). Every
answer, rule or model, is checked against its clause (`specs.citations`) and is a proposal
until a person confirms it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from firebid.specs.clauses import Clause

# Bump when rules change what they read.
RULES_VERSION = "spec-rules-2"


@dataclass(frozen=True)
class Extracted:
    system: str
    attribute: str
    value: str
    clause: str
    quote: str
    dn_min: int | None = None
    dn_max: int | None = None
    condition: str | None = None
    method: str = "rule"
    confidence: float = 0.9


MATERIALS = (
    ("galvanised_steel", re.compile(r"\bgalvani[sz]ed\b(?:\s+(?:mild\s+)?steel)?", re.I)),
    ("black_steel", re.compile(r"\bblack\s+(?:mild\s+)?steel\b", re.I)),
    ("ductile_iron", re.compile(r"\bductile\s+iron\b", re.I)),
    ("stainless_steel", re.compile(r"\bstainless\s+steel\b", re.I)),
    ("copper", re.compile(r"\bcopper\b(?!\s+conductor)", re.I)),
    ("cpvc", re.compile(r"\bC-?PVC\b", re.I)),
)
STANDARDS = re.compile(
    r"\b(BS\s*EN\s*\d{3,5}|BS\s*\d{3,5}|ASTM\s*A\s*\d{2,3}(?:\s+Grade\s+[A-Z])?|SS\s*\d{3})",
    re.I,
)
CLASSES = re.compile(r"\b(Heavy|Medium|Light)\s+grade\b|\b(Schedule|Sch\.?)\s*(\d{2,3})\b", re.I)
JOINING = (
    ("threaded", re.compile(r"\b(screwed|threaded)\b", re.I)),
    ("grooved", re.compile(r"\b(?:roll[-\s])?grooved\b", re.I)),
    ("flanged", re.compile(r"\bflanged\b", re.I)),
    ("welded", re.compile(r"\bwelded\b", re.I)),
)
SPRINKLER_TYPES = ("pendent", "upright", "sidewall", "concealed")
K_FACTOR = re.compile(r"\bK[-\s]?(?:factor\s+of\s+)?K?\s?(\d{2,3})\b", re.I)
TEMPERATURE = re.compile(r"\b(\d{2,3})\s*°\s*C\b|\b(\d{2,3})\s*deg(?:rees)?\s*C\b", re.I)
RESPONSE = re.compile(r"\b(quick|standard|fast)\s+response\b", re.I)
FINISHES = ("chrome", "white", "brass", "black", "natural")
MAKES = re.compile(r"\bapproved\s+(?:makes?|manufacturers?|brands?)\s*:?\s*(.+)$", re.I)
PLACE = re.compile(
    r"\b(?:within|in)\s+(?:the\s+)?((?:basement\s+|open\s+|multi-storey\s+)?"
    r"(?:car\s*park|plant\s+rooms?|kitchens?|external\s+areas?|roof))\b",
    re.I,
)

# Supports. A hanger sentence gives a spacing ("at 3.0 m centres", "at intervals not
# exceeding 4000 mm"); a restraint sentence says whether seismic bracing is required.
SUPPORTS = re.compile(r"\b(hangers?|supports?|brackets?)\b", re.I)
SPACING = re.compile(
    r"(?:\b(?:centres|centers|intervals|spacing|spaced|apart)\b[^.;]*?"
    r"(?P<after>\d+(?:\.\d+)?)\s*(?P<after_unit>mm|m)\b)|"
    r"(?:(?P<before>\d+(?:\.\d+)?)\s*(?P<before_unit>mm|m)\s+"
    r"(?:centres|centers|intervals|spacing|apart|c/c)\b)",
    re.I,
)
SEISMIC = re.compile(r"\b(?:seismic|sway)\s+(?:restraints?|brac(?:ing|es?))\b", re.I)
NOT_REQUIRED = re.compile(r"\b(?:not\s+(?:be\s+)?required|no\s+seismic)\b", re.I)

# Size ranges, in the words consultants use. Each gives (dn_min, dn_max).
_UP_TO = re.compile(
    r"(?:\bup\s+to\s+and\s+including|\bup\s+to|\bnot\s+exceeding|≤|<=)\s*(?:DN\s*)?(\d{2,3})"
    r"\s*(?:mm)?",
    re.I,
)
_AND_ABOVE = re.compile(
    r"\b(?:DN\s*)?(\d{2,3})\s*(?:mm)?\s*(?:and\s+(?:above|larger|over))|"
    r"(?:≥|>=)\s*(?:DN\s*)?(\d{2,3})",
    re.I,
)
_THROUGHOUT = re.compile(r"\bthroughout\b|\ball\s+sizes\b", re.I)
_SENTENCES = re.compile(r"(?<=[.;])\s+(?=[A-Z])")


def sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCES.split(text) if part.strip()]


def size_range(sentence: str) -> tuple[int | None, int | None]:
    up_to = _UP_TO.search(sentence)
    above = _AND_ABOVE.search(sentence)
    if up_to and not above:
        return None, int(up_to.group(1))
    if above and not up_to:
        return int(above.group(1) or above.group(2)), None
    return None, None


def spacing_mm(sentence: str) -> tuple[int, str] | None:
    """A hanger spacing in millimetres, and the words it was written in."""
    match = SPACING.search(sentence)
    if match is None:
        return None
    figure = match.group("after") or match.group("before")
    unit = (match.group("after_unit") or match.group("before_unit")).lower()
    value = float(figure) * (1000 if unit == "m" else 1)
    return (round(value), match.group(0)) if value >= 500 else None


def read(clause: Clause, system: str) -> list[Extracted]:
    """Everything the rules can read from one clause of a known system."""
    if system in ("other", "unknown", "general"):
        return []
    found: list[Extracted] = []
    for sentence in sentences(clause.text):
        dn_min, dn_max = size_range(sentence)
        place = PLACE.search(sentence)
        condition = " ".join(place.group(1).lower().split()) if place else None

        def add(
            attribute: str,
            value: str,
            confidence: float = 0.9,
            sentence: str = sentence,
            dn_min: int | None = dn_min,
            dn_max: int | None = dn_max,
            condition: str | None = condition,
        ) -> None:
            found.append(
                Extracted(
                    system,
                    attribute,
                    value,
                    clause.number,
                    sentence,
                    dn_min,
                    dn_max,
                    condition,
                    "rule",
                    confidence,
                )
            )

        if SEISMIC.search(sentence):
            add(
                "seismic_restraint", "not_required" if NOT_REQUIRED.search(sentence) else "required"
            )
            continue
        if SUPPORTS.search(sentence):
            # A support sentence is about supports: its sizes are pipe sizes only once the
            # spacing's own figure is set aside, and a "galvanised hanger" is not pipe.
            spacing = spacing_mm(sentence)
            if spacing is not None:
                low, high = size_range(sentence.replace(spacing[1], " "))
                add("hanger_spacing_mm", str(spacing[0]), dn_min=low, dn_max=high)
            continue
        for value, pattern in MATERIALS:
            if pattern.search(sentence) and not (
                value == "galvanised_steel" and re.search(r"\btrunking\b", sentence, re.I)
            ):
                add("pipe_material", value)
                break
        standard = STANDARDS.search(sentence)
        if standard and re.search(r"\b(pipe|pipework|mains?)\b", sentence, re.I):
            add(
                "pipe_standard",
                " ".join(standard.group(1).upper().split()).replace("GRADE", "Grade"),
            )
        grade = CLASSES.search(sentence)
        if grade:
            add(
                "pipe_class",
                grade.group(1).title() if grade.group(1) else f"Schedule {grade.group(3)}",
            )
        for value, pattern in JOINING:
            if pattern.search(sentence) and re.search(
                r"\b(join|joint|fitting|coupling)", sentence, re.I
            ):
                add("joining_method", value)
        if system == "sprinkler" and re.search(r"\bsprinkler", sentence, re.I):
            for kind in SPRINKLER_TYPES:
                if re.search(rf"\b{kind}\b", sentence, re.I):
                    add("sprinkler_type", kind)
            response = RESPONSE.search(sentence)
            if response:
                add("response", response.group(1).lower().replace("fast", "quick"))
            k_factor = K_FACTOR.search(sentence)
            if k_factor:
                add("k_factor", k_factor.group(1))
            temperature = TEMPERATURE.search(sentence)
            if temperature:
                add("temperature_rating_c", temperature.group(1) or temperature.group(2))
            for finish in FINISHES:
                if re.search(rf"\b{finish}\s+(?:plated\s+)?finish\b", sentence, re.I):
                    add("finish", finish)
        makes = MAKES.search(sentence)
        if makes:
            for make in re.split(r",|\bor\b|\band\b", makes.group(1).rstrip(".")):
                if make.strip():
                    add("approved_make", make.strip(), 0.85)
    return found


def extract(clauses: list[Clause], systems: dict[str, str]) -> list[Extracted]:
    """Everything the rules can read from a specification's fire protection clauses."""
    found: list[Extracted] = []
    for clause in clauses:
        if clause.text:
            found.extend(read(clause, systems.get(clause.number, "unknown")))
    return found


def unread(clauses: list[Clause], systems: dict[str, str], found: list[Extracted]) -> list[Clause]:
    """Fire protection clauses with text the rules read nothing from: for the model."""
    read_from = {item.clause for item in found}
    return [
        clause
        for clause in clauses
        if clause.text
        and systems.get(clause.number) not in ("other", "unknown", "general")
        and clause.number not in read_from
    ]
