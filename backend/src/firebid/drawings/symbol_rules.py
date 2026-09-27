"""Keyword rules for legend descriptions: the deterministic first try (P1-04).

`PENDENT SPRINKLER` needs no model. A rule proposes a type, recorded with the rule file's
version; a person still confirms it. When rules for different types match one description,
none applies: an ambiguous row goes to the model, and then to a person.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[3] / "config" / "symbol_rules.yaml"


@dataclass(frozen=True)
class Rule:
    pattern: re.Pattern[str]
    object_type: str


@dataclass(frozen=True)
class RuleProposal:
    object_type: str
    rule: str
    rule_version: str


@dataclass(frozen=True)
class RuleSet:
    rules: tuple[Rule, ...]
    version: str

    def propose(self, description: str) -> RuleProposal | None:
        text = normalise(description)
        matched = [rule for rule in self.rules if rule.pattern.search(text)]
        types = {rule.object_type for rule in matched}
        if len(types) != 1:
            return None
        return RuleProposal(matched[0].object_type, matched[0].pattern.pattern, self.version)


def normalise(description: str) -> str:
    return " ".join(description.upper().split())


@lru_cache(maxsize=4)
def load(path: Path = CONFIG) -> RuleSet:
    raw = path.read_bytes()
    data = yaml.safe_load(raw)
    rules = tuple(
        Rule(re.compile(item["match"]), str(item["type"])) for item in data.get("rules", [])
    )
    return RuleSet(rules, "rules-" + hashlib.sha256(raw).hexdigest()[:12])
