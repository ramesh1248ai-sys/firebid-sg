"""Client BOQ lines to measured lines, by rule first (FR-BOQ-02).

Deterministic first: what a line is (a pendent head, a gate valve, a pipe, a drop, a riser)
and its sizes are read from the words on both sides, and a client line is matched when
exactly one measured line is the same kind, the same size and a compatible unit. Provisional
and lump sums are matched to nothing: nothing measured is a provisional sum.

What the rules cannot settle (no candidate, or more than one) is left for the model, and
every proposal, the rules' included, is confirmed by a person.

Pure: lines in, proposals out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pendent", re.compile(r"\bpendent\b", re.I)),
    ("upright", re.compile(r"\bupright\b", re.I)),
    ("sidewall", re.compile(r"\bside\s?wall\b", re.I)),
    ("concealed", re.compile(r"\bconcealed\b", re.I)),
    ("gate_valve", re.compile(r"\bgate valve\b", re.I)),
    ("check_valve", re.compile(r"\b(check|non[- ]return) valve\b", re.I)),
    ("butterfly_valve", re.compile(r"\bbutterfly valve\b", re.I)),
    ("flow_switch", re.compile(r"\bflow switch\b", re.I)),
    ("reducer", re.compile(r"\breducer\b", re.I)),
    ("tee", re.compile(r"\btee\b", re.I)),
    ("elbow", re.compile(r"\belbow\b|\bbend\b", re.I)),
    ("coupling", re.compile(r"\bcoupling\b", re.I)),
    ("drop", re.compile(r"\bdrops?\b", re.I)),
    ("riser", re.compile(r"\briser\b", re.I)),
    ("pipe", re.compile(r"\bpipe(work)?\b|\bdia(meter)?\b", re.I)),
)
SIZE = re.compile(r"(?:\bDN\s?(\d{2,3})\b|\b(\d{2,3})\s?(?:mm|dia)\b)", re.I)
UNITS = {
    "nr": "count",
    "no": "count",
    "no.": "count",
    "nos": "count",
    "each": "count",
    "m": "length",
    "lm": "length",
    "m.": "length",
    "metre": "length",
}
SPRINKLERS = {"pendent", "upright", "sidewall", "concealed"}


@dataclass(frozen=True)
class Measured:
    key: str
    description: str
    unit: str


@dataclass(frozen=True)
class Client:
    ref: str
    description: str
    unit: str | None
    kind: str  # line | provisional | lump_sum


@dataclass(frozen=True)
class Proposal:
    ref: str
    maps_to: str | None
    confidence: float
    reason: str


def kind_of(text: str) -> str | None:
    """The first kind named: a head before its "pipe", a drop before the pipe it is."""
    for name, pattern in KINDS:
        if pattern.search(text):
            return name
    return None


PAIR = re.compile(r"\b(\d{2,3})\s?[xX\u00d7]\s?(\d{2,3})\s?mm\b")


def sizes_of(text: str) -> set[int]:
    """Nominal sizes stated: "150 mm", "DN150", "150 dia", and both of "150 x 100 mm"."""
    found = {int(a or b) for a, b in SIZE.findall(text)}
    for a, b in PAIR.findall(text):
        found |= {int(a), int(b)}
    return found


def _unit(unit: str | None) -> str | None:
    return UNITS.get((unit or "").strip().lower())


def _same_kind(client: str | None, measured: str | None) -> bool:
    # The same kind only: a client "pipe" line is plain pipe, not a drop or a riser, which
    # are lines of their own.
    return client is not None and client == measured


def match(clients: list[Client], measured: list[Measured]) -> tuple[list[Proposal], list[Client]]:
    """Proposals the rules are sure of, and the client lines left for the model."""
    proposals: list[Proposal] = []
    left: list[Client] = []
    features = [
        (m, kind_of(m.description), sizes_of(m.description), _unit(m.unit)) for m in measured
    ]
    for client in clients:
        if client.kind in ("provisional", "lump_sum"):
            proposals.append(
                Proposal(
                    client.ref,
                    None,
                    0.95,
                    f"a {client.kind.replace('_', ' ')}: nothing is measured",
                )
            )
            continue
        kind = kind_of(client.description)
        sizes = sizes_of(client.description)
        unit = _unit(client.unit)
        candidates = [
            m
            for m, m_kind, m_sizes, m_unit in features
            if _same_kind(kind, m_kind)
            and (not sizes or not m_sizes or m_sizes <= sizes or sizes <= m_sizes)
            and (unit is None or m_unit is None or unit == m_unit)
            and (kind in SPRINKLERS or not (sizes and m_sizes) or bool(sizes & m_sizes))
        ]
        if len(candidates) == 1:
            proposals.append(
                Proposal(
                    client.ref,
                    candidates[0].key,
                    0.9,
                    f"same kind ({kind}), size and unit as {candidates[0].description!r}",
                )
            )
        else:
            left.append(client)
    return proposals, left
