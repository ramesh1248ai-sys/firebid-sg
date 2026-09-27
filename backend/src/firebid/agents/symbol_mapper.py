"""`symbol_mapper`: say which canonical object a legend row's symbol is (FR-VIS-02).

Asked only about rows the keyword rules could not decide, and given the row as an image (the
symbol and its description, rendered from the extracted geometry, never the tender file) plus
the description text and the types it may choose from. L1 (draft): its answer is a proposal,
and every proposal is confirmed by a person in the mapping screen, so it never escalates on
confidence alone; the confidence is shown to that person instead.
"""

from __future__ import annotations

from typing import Any

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
from firebid.ai_gateway.types import GenerationRequest, ImagePart, Message, TextPart

ROUTE = "symbol_map"


class LegendRowInput(BaseModel):
    description: str
    image_png: bytes
    # (key, label) of every type the row may be mapped to.
    choices: list[tuple[str, str]]
    consultant: str | None = None


class SymbolProposal(BaseModel):
    # None when the row is not a fire protection object the library holds.
    object_type: str | None
    attributes: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class SymbolMapper:
    name = "symbol_mapper"
    route = ROUTE
    # Every mapping is confirmed by a person whatever the confidence (the mapping screen is
    # the human step), so low confidence is shown to them rather than raised as a task.
    confidence_threshold = 0.0

    def __init__(self, router: Router) -> None:
        self._router = router
        self.tools = ToolRegistry()
        self.tools.register(
            AgentTool(
                name="map_symbol",
                description="Say which canonical object a drawing legend's symbol is.",
                level=AutonomyLevel.DRAFT,
            )
        )

    def run(self, request: AgentInput) -> AgentResult[SymbolProposal]:
        payload = request.payload
        if not isinstance(payload, LegendRowInput):
            raise TypeError(f"{self.name} needs a LegendRowInput, got {type(payload).__name__}")
        choices = "\n".join(f"- `{key}`: {label}" for key, label in payload.choices)
        parts: list[TextPart | ImagePart] = [
            ImagePart("image/png", payload.image_png),
            TextPart(
                f"Legend description: {payload.description}\n\n"
                f"Choose object_type from these keys, or null if none fits:\n{choices}"
            ),
        ]
        response = self._router.generate(
            ROUTE,
            GenerationRequest(
                messages=(Message(role="user", parts=tuple(parts)),),
                output_model=SymbolProposal,
            ),
        )
        proposal = response.parsed
        if not isinstance(proposal, SymbolProposal):
            raise TypeError("the gateway returned no parsed symbol proposal")
        allowed = {key for key, _ in payload.choices}
        if proposal.object_type is not None and proposal.object_type not in allowed:
            # An answer outside the library is no answer: a person maps it from scratch.
            proposal = SymbolProposal(
                object_type=None,
                confidence=0.0,
                reason=f"the model chose {proposal.object_type!r}, which is not in the library",
            )
        return AgentResult(
            output=proposal,
            assertions=[
                Assertion(
                    field="object_type",
                    value=proposal.object_type,
                    confidence=proposal.confidence if proposal.object_type else 0.0,
                )
            ],
            provider=response.provider,
            model=response.model,
            prompt_version=response.prompt_version,
        )
