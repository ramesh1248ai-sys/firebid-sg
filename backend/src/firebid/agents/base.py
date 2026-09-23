"""The agent contract (requirements §8.4) and the autonomy levels (§8.2).

An agent here is a plain typed function that calls a gateway route. No framework, so the
Phase 4 orchestration decision can wrap this rather than fight it (ADR-007).

What the contract insists on, because each one has been a real failure somewhere:

* **Typed input and output**, validated before anything is persisted.
* **Evidence and confidence per assertion.** An estimator has to be able to ask "why do you
  think that" and get an answer in two clicks (NFR-10).
* **An idempotency key**, so a re-run produces no second proposal.
* **Escalation, never a guess.** On low confidence, invalid output or exhausted retries, the
  agent raises a `HumanTask` with its context instead of writing something plausible.
* **Proposals, not facts.** Output is persisted as a proposal carrying its provenance, and
  never overwrites baselined or approved data.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Protocol

from pydantic import BaseModel

from firebid.domain.evidence import EvidenceRecord


class AutonomyLevel(IntEnum):
    """How far an agent may act on its own (requirements §8.2)."""

    INFORM = 0  # analyses and flags only
    DRAFT = 1  # produces drafts a person must approve
    BOUNDED_ACTION = 2  # reversible, pre-approved, logged, undoable
    COMMIT = 3  # irreversible or external. Never permitted.


class AutonomyRefused(Exception):
    """A tool declared a level the platform does not allow."""


@dataclass(frozen=True)
class AgentTool:
    """One thing an agent can do, with the autonomy it needs to do it."""

    name: str
    description: str
    level: AutonomyLevel


class ToolRegistry:
    """Tools an agent may use. L3 is refused at registration, not at call time.

    Refusing early matters: a tool that only fails when invoked is a tool that ships, sits
    unused in testing, and fires in front of a client.
    """

    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> AgentTool:
        if tool.level >= AutonomyLevel.COMMIT:
            raise AutonomyRefused(
                f"tool '{tool.name}' declares autonomy L{int(tool.level)} (commit), which is "
                "never permitted: irreversible and external actions stay with a person "
                "(requirements §8.2)"
            )
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> AgentTool:
        return self._tools[name]

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def names(self) -> list[str]:
        return sorted(self._tools)


class Assertion(BaseModel):
    """One thing an agent claims, with how sure it is and what it read.

    Confidence is the agent's own reading, not a quality score for the drawing.
    """

    field: str
    value: str | None
    confidence: float
    evidence: EvidenceRecord | None = None

    def is_confident(self, threshold: float) -> bool:
        return self.confidence >= threshold


@dataclass(frozen=True)
class AgentInput:
    """What every agent is given, whatever it does."""

    bid_id: uuid.UUID
    idempotency_key: str
    payload: BaseModel
    actor_label: str = "agent"
    run_budget_sgd: str | None = None
    owner_email: str | None = None


@dataclass
class AgentResult[Output: BaseModel]:
    """What an agent returns: a typed output, and the assertions behind it."""

    output: Output
    assertions: Sequence[Assertion] = field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None

    def low_confidence(self, threshold: float) -> list[Assertion]:
        return [a for a in self.assertions if not a.is_confident(threshold)]


class Agent[Output: BaseModel](Protocol):
    """A plain function with a name, a route and a confidence bar."""

    name: str
    route: str
    confidence_threshold: float
    tools: ToolRegistry

    def run(self, request: AgentInput) -> AgentResult[Output]: ...
