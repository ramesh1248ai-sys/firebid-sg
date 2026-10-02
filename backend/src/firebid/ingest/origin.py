"""Whose document is it? A proposal from a file's path, for a person to confirm (FR-DOC-10).

A tender folder mixes what the client issued with the company's own marked-up drawings and
earlier responses. Only client-issued documents may feed the registers, revision control and
takeoff, so every uploaded file carries an origin. This module proposes one from the file's
path within the folder, by the rules in `config/intake.yaml`.

Pure: a path and rules in, a proposal out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path, PurePosixPath

import yaml

CONFIG = Path(__file__).resolve().parents[3] / "config" / "intake.yaml"


class Origin(StrEnum):
    TENDER = "tender"  # client-issued: read, registered, taken off
    WORKING = "working"  # the company's own working documents: kept, not read
    REFERENCE = "reference"  # earlier responses, reviews, registers: kept, not read
    IGNORED = "ignored"  # not a document: reported, never stored


# What a stored document may be. An ignored file is never stored.
STORED = (str(Origin.TENDER), str(Origin.WORKING), str(Origin.REFERENCE))


@dataclass(frozen=True)
class Rule:
    origin: Origin
    reason: str
    patterns: tuple[re.Pattern[str], ...]


@dataclass(frozen=True)
class Proposal:
    origin: Origin
    reason: str


TENDER = Proposal(Origin.TENDER, "no rule says otherwise, so it is taken as client-issued")


def clean_path(raw: str) -> str:
    """A relative path as a label: forward slashes, no traversal, no leading slash."""
    pure = PurePosixPath(raw.replace("\\", "/"))
    parts = [part for part in pure.parts if part not in ("..", "/", ".")]
    return "/".join(parts)


@lru_cache(maxsize=4)
def rules(path: Path = CONFIG) -> tuple[Rule, ...]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return tuple(
        Rule(
            Origin(item["origin"]),
            str(item["reason"]),
            tuple(re.compile(pattern, re.IGNORECASE) for pattern in item["patterns"]),
        )
        for item in data.get("rules", [])
    )


def propose(path: str, rule_set: tuple[Rule, ...] | None = None) -> Proposal:
    """The origin a path suggests: the first rule that matches, else a tender document."""
    cleaned = clean_path(path)
    for rule in rule_set if rule_set is not None else rules():
        if any(pattern.search(cleaned) for pattern in rule.patterns):
            return Proposal(rule.origin, rule.reason)
    return TENDER
