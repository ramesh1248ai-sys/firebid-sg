"""Checking a citation against the clause it names (FR-SPEC-05, P1-06 build item 4).

Deterministic: the cited clause must exist in the specification's clause tree, and its text
must contain the value's key words, compared after normalising case, punctuation and
spacing. `black_steel` needs "black" and "steel"; "Schedule 40" needs "schedule" and "40";
`threaded` is supported by "screwed" or "threaded". A value the clause does not support is
not trusted: its confidence falls and it is flagged for a person, with the reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from firebid.specs.clauses import Clause, find, full_text

# What a value may be written as in a clause.
SYNONYMS: dict[str, tuple[tuple[str, ...], ...]] = {
    "threaded": (("screwed",), ("threaded",)),
    "grooved": (("grooved",),),
    "flanged": (("flanged",),),
    "welded": (("welded",),),
    "galvanised_steel": (("galvanised",), ("galvanized",)),
    "black_steel": (("black", "steel"),),
    "ductile_iron": (("ductile", "iron"),),
    "stainless_steel": (("stainless", "steel"),),
    "quick": (("quick", "response"), ("fast", "response")),
    "standard": (("standard", "response"),),
}
FLAGGED_CONFIDENCE = 0.2


@dataclass(frozen=True)
class Check:
    ok: bool
    reason: str
    matched: tuple[str, ...] = ()


def tokens(text: str) -> list[str]:
    # Letters and digits apart: "K80" states 80, "68°C" states 68.
    return re.findall(r"[a-z]+|[0-9]+", text.lower())


def check(clauses: list[Clause], clause_number: str, value: str) -> Check:
    cited = find(clauses, clause_number)
    if cited is None:
        return Check(False, f"clause {clause_number} is not in the specification")
    words = set(tokens(full_text(cited)))
    for option in SYNONYMS.get(value, (tuple(tokens(value.replace("_", " "))),)):
        if option and all(word in words for word in option):
            return Check(True, "the clause states it", option)
    return Check(False, f"clause {clause_number} does not state {value!r}")


def adjusted(confidence: float, result: Check) -> float:
    """A failed check leaves the attribute at a low confidence, for a person to decide."""
    return confidence if result.ok else min(confidence, FLAGGED_CONFIDENCE)
