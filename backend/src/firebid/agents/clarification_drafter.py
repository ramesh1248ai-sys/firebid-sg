"""`clarification_drafter`: the model's part in wording a tender clarification (P2-06).

The draft is composed by rule from the flagged issue and its evidence. The model is asked,
by a person, only to word it better, and is given the draft and its evidence, numbered. Its
answer must say which evidence it relied on: the output model refuses an answer that cites
none (guardrail 5), so such a draft never reaches the service, and nothing is saved. Options
stay recommendations. L1 (draft): a person reviews every clarification before it is issued.
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

ROUTE = "clarification_draft"


class DraftInput(BaseModel):
    subject: str
    problem: str
    options: list[str]
    evidence: list[str]  # numbered by position


class DraftWording(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    problem: str = Field(min_length=1, max_length=6000)
    options: list[str] = Field(default_factory=list, max_length=6)
    # The evidence it relied on, by number. A draft with none is not a draft.
    evidence: list[int] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class ClarificationDrafter:
    """Word a tender clarification clearly, from its draft and the evidence it cites."""

    name = "clarification_drafter"
    route = ROUTE
    confidence_threshold = 0.0  # a person reviews every clarification

    def __init__(self, router: Router) -> None:
        self._router = router
        self.tools = ToolRegistry()
        self.tools.register(
            AgentTool(name=self.name, description=self.__doc__ or "", level=AutonomyLevel.DRAFT)
        )

    def run(self, request: AgentInput) -> AgentResult[DraftWording]:
        payload = request.payload
        if not isinstance(payload, DraftInput):
            raise TypeError(f"{self.name} needs a DraftInput, got {type(payload).__name__}")
        evidence = "\n".join(f"[{index}] {item}" for index, item in enumerate(payload.evidence))
        options = "\n".join(f"- {option}" for option in payload.options) or "(none)"
        parts = (
            TextPart("The evidence:\n" + evidence, cache=True),
            TextPart(
                f"Subject: {payload.subject}\n\nQuery:\n{payload.problem}\n\nOptions:\n{options}"
            ),
        )
        response = self._router.generate(
            ROUTE,
            GenerationRequest(
                messages=(Message(role="user", parts=parts),), output_model=DraftWording
            ),
        )
        found = response.parsed
        if not isinstance(found, DraftWording):
            raise TypeError("the gateway returned no parsed clarification wording")
        return AgentResult(
            output=found,
            assertions=[
                Assertion(field="subject", value=found.subject, confidence=found.confidence)
            ],
            provider=response.provider,
            model=response.model,
            prompt_version=response.prompt_version,
        )
