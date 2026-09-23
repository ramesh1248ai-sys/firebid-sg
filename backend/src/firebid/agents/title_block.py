"""`title_block_reader`: read a drawing's title block into structured fields.

The demonstration agent for P0-04, and the one P1-02 reuses for real sheets. It is L1 (draft):
everything it produces is a proposal a person confirms, because a wrong sheet number quietly
corrupts a take-off and may not surface until tender.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from firebid.agents.base import (
    Agent,
    AgentInput,
    AgentResult,
    AgentTool,
    Assertion,
    AutonomyLevel,
    ToolRegistry,
)
from firebid.ai_gateway.router import Router
from firebid.ai_gateway.types import (
    GenerationRequest,
    ImagePart,
    Message,
    TextPart,
)

ROUTE = "title_block_read"


class TitleBlockInput(BaseModel):
    """The cropped title block, as an image, plus any text already extracted from the page."""

    image_png: bytes
    page_text: str = ""
    sheet_hint: str | None = None


class TitleBlock(BaseModel):
    """What a title block holds. Every field is optional: absent beats invented."""

    sheet_number: str | None = None
    sheet_title: str | None = None
    revision: str | None = None
    scale: str | None = None
    drawn_by: str | None = None
    date: str | None = None

    sheet_number_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    sheet_title_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    revision_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    scale_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


# The fields an estimator relies on. The others are useful but not worth stopping for.
CRITICAL = ("sheet_number", "revision")


class TitleBlockReader:
    """An agent is a plain typed function; this class only holds its configuration."""

    name = "title_block_reader"
    route = ROUTE
    # Below this, a person checks it. Set high deliberately: the cost of a wrong sheet number
    # is far higher than the cost of a glance.
    confidence_threshold = 0.85

    def __init__(self, router: Router) -> None:
        self._router = router
        self.tools = ToolRegistry()
        self.tools.register(
            AgentTool(
                name="read_title_block",
                description="Read the fields printed in a drawing's title block.",
                level=AutonomyLevel.DRAFT,
            )
        )

    def run(self, request: AgentInput) -> AgentResult[TitleBlock]:
        payload = request.payload
        if not isinstance(payload, TitleBlockInput):
            raise TypeError(f"{self.name} needs a TitleBlockInput, got {type(payload).__name__}")

        parts: list[TextPart | ImagePart] = [ImagePart("image/png", payload.image_png)]
        if payload.page_text:
            parts.append(TextPart(f"Text already extracted from this page:\n{payload.page_text}"))
        if payload.sheet_hint:
            # A hint, explicitly not an answer: the prompt tells the model not to infer a
            # field from anything but the drawing.
            parts.append(
                TextPart(
                    f"The file was named '{payload.sheet_hint}'. Treat this as unreliable: "
                    "report only what the title block itself shows."
                )
            )

        response = self._router.generate(
            ROUTE,
            GenerationRequest(
                messages=(Message(role="user", parts=tuple(parts)),),
                output_model=TitleBlock,
            ),
        )

        block = response.parsed
        if not isinstance(block, TitleBlock):
            raise TypeError("the gateway returned no parsed title block")

        return AgentResult(
            output=block,
            assertions=_assertions(block),
            provider=response.provider,
            model=response.model,
            prompt_version=response.prompt_version,
        )


def _assertions(block: TitleBlock) -> list[Assertion]:
    """One assertion per critical field, so escalation names what to check.

    An absent field counts as no confidence rather than as a confident blank: the difference
    between "the drawing does not say" and "we did not read it" matters to the person who
    picks this up.
    """
    claims = []
    for field_name in CRITICAL:
        value = getattr(block, field_name)
        confidence = getattr(block, f"{field_name}_confidence", 0.0)
        claims.append(
            Assertion(
                field=field_name,
                value=value,
                confidence=0.0 if value in (None, "") else float(confidence),
            )
        )
    return claims


def build(router: Router) -> Agent[TitleBlock]:
    return TitleBlockReader(router)
