"""The model's part in pricing (FR-CST-01). L1 (draft): which rate library entry a BOQ line
is, when no entry has exactly its key. It chooses only among the candidates it is given, is
shown descriptions and keys but never a rate, and an estimator confirms every choice. The
price is always the entry's.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from firebid.agents.base import (
    AgentInput,
    AgentResult,
    AgentTool,
    Assertion,
    AutonomyLevel,
    ToolRegistry,
)
from firebid.ai_gateway.router import Router
from firebid.ai_gateway.types import GenerationRequest, Message, TextPart

ROUTE = "rate_match"


class CandidateIn(BaseModel):
    id: str
    description: str
    key: str
    source_type: str


class LineIn(BaseModel):
    ref: str
    description: str
    unit: str
    key: str
    candidates: list[CandidateIn]


class RateMatchBatch(BaseModel):
    lines: list[LineIn]


class RateChoice(BaseModel):
    line: str
    entry: str | None
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class RateMatchAnswer(BaseModel):
    choices: list[RateChoice]


class RateMatcher:
    name = "rate_matcher"
    route = ROUTE
    confidence_threshold = 0.0  # an estimator confirms every choice

    def __init__(self, router: Router) -> None:
        self._router = router
        self.tools = ToolRegistry()
        self.tools.register(
            AgentTool(
                name="propose_rate_entry",
                description="Say which rate library entry a BOQ line is, from its candidates.",
                level=AutonomyLevel.DRAFT,
            )
        )

    def run(self, request: AgentInput) -> AgentResult[RateMatchAnswer]:
        payload = request.payload
        if not isinstance(payload, RateMatchBatch):
            raise TypeError(f"{self.name} needs a RateMatchBatch, got {type(payload).__name__}")
        blocks = []
        for line in payload.lines:
            options = "\n".join(
                f"  - `{c.id}`: {c.description} [{c.key}] ({c.source_type})"
                for c in line.candidates
            )
            blocks.append(
                f"Line `{line.ref}`: {line.description} ({line.unit}) [{line.key}]\n{options}"
            )
        response = self._router.generate(
            ROUTE,
            GenerationRequest(
                messages=(Message(role="user", parts=(TextPart("\n\n".join(blocks)),)),),
                output_model=RateMatchAnswer,
            ),
        )
        answer = response.parsed
        if not isinstance(answer, RateMatchAnswer):
            raise TypeError("the gateway returned no parsed rate match answer")
        allowed = {line.ref: {c.id for c in line.candidates} for line in payload.lines}
        cleaned = []
        for choice in answer.choices:
            if choice.line not in allowed:
                continue  # a line it was not asked about
            if choice.entry is not None and choice.entry not in allowed[choice.line]:
                choice = RateChoice(
                    line=choice.line,
                    entry=None,
                    confidence=0.0,
                    reason=f"the model named {choice.entry!r}, which was not a candidate",
                )
            cleaned.append(choice)
        result = RateMatchAnswer(choices=cleaned)
        return AgentResult(
            output=result,
            assertions=[
                Assertion(field=f"entry[{c.line}]", value=c.entry, confidence=c.confidence)
                for c in cleaned
            ],
            provider=response.provider,
            model=response.model,
            prompt_version=response.prompt_version,
        )
