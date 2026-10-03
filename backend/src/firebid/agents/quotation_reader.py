"""`quotation_reader`: the model's part in reading a supplier's quotation (P2-04).

Asked only for what the rules could not read (`pricing.quotation`), and given the file's own
lines, numbered. Every answer names the line it was read from; the service then checks the
value against that line, so an answer the line does not bear out is dropped rather than
believed. A unit price is only ever a figure written on the cited line: the model reads
prices, it never works one out. L1 (draft): every answer is a proposal a person confirms.
"""

from __future__ import annotations

from typing import Literal

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

ROUTE = "quotation_extract"

FieldName = Literal[
    "supplier",
    "quote_number",
    "quote_date",
    "valid_until",
    "currency",
    "delivery_terms",
    "lead_time",
    "exclusion",
]


class QuotationInput(BaseModel):
    lines: list[str]  # the file's lines, cells joined with " | "; a line's number is its index
    wanted: list[str]  # the fields the rules did not read; "lines" for the priced lines


class FieldAnswer(BaseModel):
    name: FieldName
    value: str
    line: int = Field(ge=0)
    confidence: float = Field(ge=0.0, le=1.0)


class LineAnswer(BaseModel):
    description: str
    unit_price: str
    line: int = Field(ge=0)
    brand: str | None = None
    model: str | None = None
    unit: str | None = None
    moq: str | None = None
    lead_time: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class QuotationAnswer(BaseModel):
    fields: list[FieldAnswer] = []
    lines: list[LineAnswer] = []


class QuotationReader:
    """Read a quotation's terms and lines from its file, naming the line each is on."""

    name = "quotation_reader"
    route = ROUTE
    confidence_threshold = 0.0  # a person confirms every answer

    def __init__(self, router: Router) -> None:
        self._router = router
        self.tools = ToolRegistry()
        self.tools.register(
            AgentTool(name=self.name, description=self.__doc__ or "", level=AutonomyLevel.DRAFT)
        )

    def run(self, request: AgentInput) -> AgentResult[QuotationAnswer]:
        payload = request.payload
        if not isinstance(payload, QuotationInput):
            raise TypeError(f"{self.name} needs a QuotationInput, got {type(payload).__name__}")
        numbered = "\n".join(f"{index}: {line}" for index, line in enumerate(payload.lines))
        parts = (
            TextPart("The quotation:\n" + numbered, cache=True),
            TextPart("Read: " + ", ".join(payload.wanted) + "."),
        )
        response = self._router.generate(
            ROUTE,
            GenerationRequest(
                messages=(Message(role="user", parts=parts),), output_model=QuotationAnswer
            ),
        )
        found = response.parsed
        if not isinstance(found, QuotationAnswer):
            raise TypeError("the gateway returned no parsed quotation")
        return AgentResult(
            output=found,
            assertions=[
                Assertion(field=item.name, value=item.value, confidence=item.confidence)
                for item in found.fields
            ]
            + [
                Assertion(field="line", value=item.description, confidence=item.confidence)
                for item in found.lines
            ],
            provider=response.provider,
            model=response.model,
            prompt_version=response.prompt_version,
        )
