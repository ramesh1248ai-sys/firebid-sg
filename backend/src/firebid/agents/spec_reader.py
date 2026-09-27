"""`spec_attribute_extractor` and `spec_section_finder`: the model's part in P1-06.

Asked only about what the rules could not read (`specs.attributes`, `specs.sections`), and
given the specification's own clauses, never anything else. The specification goes first,
as a cache-hinted prefix, because every call about one specification repeats it.

Every attribute must cite a clause: the output schema makes the clause number required, and
`specs.citations` then checks it against the clause text, so an answer that cites the wrong
clause is flagged rather than believed. L1 (draft): every answer is a proposal a person
confirms, so neither agent escalates on confidence alone.
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

ATTRIBUTE_ROUTE = "spec_attribute_extract"
SECTION_ROUTE = "spec_section_find"

Attribute = Literal[
    "pipe_material",
    "pipe_standard",
    "pipe_class",
    "joining_method",
    "sprinkler_type",
    "response",
    "k_factor",
    "temperature_rating_c",
    "finish",
    "approved_make",
]
System = Literal[
    "sprinkler",
    "hose_reel",
    "hydrant",
    "wet_riser",
    "dry_riser",
    "fire_pump",
    "fire_protection",
    "general",
    "other",
]


def specification_text(clauses: list[tuple[str, str, str]]) -> str:
    """The specification as the model sees it: one clause a line, number first."""
    return "\n".join(f"{number} {heading} {text}".strip() for number, heading, text in clauses)


class SpecInput(BaseModel):
    clauses: list[tuple[str, str, str]]  # (number, heading, text), the whole specification
    system: str
    clause_numbers: list[str]  # the clauses to read


class SpecAttribute(BaseModel):
    attribute: Attribute
    value: str
    clause: str = Field(min_length=1, description="the clause number the value is stated in")
    quote: str = Field(description="the words of the clause that state it")
    dn_min: int | None = None
    dn_max: int | None = None
    condition: str | None = Field(default=None, description="where it applies, if limited")
    confidence: float = Field(ge=0.0, le=1.0)


class SpecAttributes(BaseModel):
    attributes: list[SpecAttribute]


class SectionsInput(BaseModel):
    headings: list[tuple[str, str]]  # (number, heading) of top-level sections


class SectionAnswer(BaseModel):
    number: str
    system: System
    confidence: float = Field(ge=0.0, le=1.0)


class SectionAnswers(BaseModel):
    sections: list[SectionAnswer]


class _Agent:
    confidence_threshold = 0.0  # every answer is confirmed by a person anyway

    def __init__(self, router: Router) -> None:
        self._router = router
        self.tools = ToolRegistry()
        self.tools.register(
            AgentTool(name=self.name, description=self.__doc__ or "", level=AutonomyLevel.DRAFT)
        )

    name = "agent"
    route = ""


class SpecAttributeExtractor(_Agent):
    """Read the attributes takeoff needs from specification clauses, citing each."""

    name = "spec_attribute_extractor"
    route = ATTRIBUTE_ROUTE

    def run(self, request: AgentInput) -> AgentResult[SpecAttributes]:
        payload = request.payload
        if not isinstance(payload, SpecInput):
            raise TypeError(f"{self.name} needs a SpecInput, got {type(payload).__name__}")
        parts = (
            TextPart("The specification:\n" + specification_text(payload.clauses), cache=True),
            TextPart(
                f"For the {payload.system.replace('_', ' ')} system, read clauses "
                f"{', '.join(payload.clause_numbers)} and return every attribute they state."
            ),
        )
        response = self._router.generate(
            ATTRIBUTE_ROUTE,
            GenerationRequest(
                messages=(Message(role="user", parts=parts),), output_model=SpecAttributes
            ),
        )
        found = response.parsed
        if not isinstance(found, SpecAttributes):
            raise TypeError("the gateway returned no parsed attributes")
        return AgentResult(
            output=found,
            assertions=[
                Assertion(field=item.attribute, value=item.value, confidence=item.confidence)
                for item in found.attributes
            ],
            provider=response.provider,
            model=response.model,
            prompt_version=response.prompt_version,
        )


class SpecSectionFinder(_Agent):
    """Say which fire protection system each unplaced specification section is about."""

    name = "spec_section_finder"
    route = SECTION_ROUTE

    def run(self, request: AgentInput) -> AgentResult[SectionAnswers]:
        payload = request.payload
        if not isinstance(payload, SectionsInput):
            raise TypeError(f"{self.name} needs a SectionsInput, got {type(payload).__name__}")
        listing = "\n".join(f"{number} {heading}" for number, heading in payload.headings)
        response = self._router.generate(
            SECTION_ROUTE,
            GenerationRequest(
                messages=(Message.user(f"Section headings:\n{listing}"),),
                output_model=SectionAnswers,
            ),
        )
        found = response.parsed
        if not isinstance(found, SectionAnswers):
            raise TypeError("the gateway returned no parsed sections")
        return AgentResult(
            output=found,
            assertions=[
                Assertion(field=item.number, value=item.system, confidence=item.confidence)
                for item in found.sections
            ],
            provider=response.provider,
            model=response.model,
            prompt_version=response.prompt_version,
        )
