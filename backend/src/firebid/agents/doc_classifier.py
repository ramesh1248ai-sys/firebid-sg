"""`document_classifier`: say what kind of tender document a file is (FR-DOC-02).

Asked only about files the rules could not place, and given only the digest the sandbox
extracted: the opening text, sheet names and header rows, never the file. L1 (draft): its
answer is a proposal, and below its threshold a person decides.
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
from firebid.ingest.classification import DocType

ROUTE = "doc_classify"


class DocumentDigest(BaseModel):
    filename: str
    kind: str
    text: str = ""
    sheet_names: list[str] = []
    header_rows: list[list[str]] = []
    rules_said: str | None = None


class DocumentType(BaseModel):
    doc_type: DocType
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class DocumentClassifier:
    name = "document_classifier"
    route = ROUTE
    # A misfiled document is found when someone looks for it; a misfiled BOQ is priced from
    # the wrong file. High enough that anything doubtful reaches a person.
    confidence_threshold = 0.8

    def __init__(self, router: Router) -> None:
        self._router = router
        self.tools = ToolRegistry()
        self.tools.register(
            AgentTool(
                name="classify_document",
                description="Say which kind of tender document a file is.",
                level=AutonomyLevel.DRAFT,
            )
        )

    def run(self, request: AgentInput) -> AgentResult[DocumentType]:
        payload = request.payload
        if not isinstance(payload, DocumentDigest):
            raise TypeError(f"{self.name} needs a DocumentDigest, got {type(payload).__name__}")

        lines = [f"File name: {payload.filename}", f"File type: {payload.kind}"]
        if payload.sheet_names:
            lines.append("Worksheets: " + ", ".join(payload.sheet_names))
        for row in payload.header_rows[:5]:
            lines.append("Header row: " + " | ".join(row))
        if payload.rules_said:
            lines.append(f"The rules were unsure, suggesting: {payload.rules_said}")
        lines.append("Opening text:\n" + payload.text[:6000])

        response = self._router.generate(
            ROUTE,
            GenerationRequest(
                messages=(Message(role="user", parts=(TextPart("\n".join(lines)),)),),
                output_model=DocumentType,
            ),
        )
        answer = response.parsed
        if not isinstance(answer, DocumentType):
            raise TypeError("the gateway returned no parsed document type")
        return AgentResult(
            output=answer,
            assertions=[
                Assertion(
                    field="doc_type", value=str(answer.doc_type), confidence=answer.confidence
                )
            ],
            provider=response.provider,
            model=response.model,
            prompt_version=response.prompt_version,
        )
