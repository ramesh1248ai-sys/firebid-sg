"""The model's part in reading a client's BOQ (FR-BOQ-02). L1 (draft): every answer is a
proposal a person confirms.

* `boq_column_reader`: which row is a bill's header, and which columns hold which field,
  when the header words did not say (`boq.reader.Ambiguous`).
* `boq_mapper`: which measured line each client line is, for the lines the rules left
  (`boq.matching`), in batches. An answer naming a line that does not exist is no answer.
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

MAPPING_ROUTE = "boq_mapping"
COLUMNS_ROUTE = "boq_columns"
FIELDS = ("item", "description", "unit", "quantity", "rate", "amount")


# --- Columns --------------------------------------------------------------------------------


class SheetPreview(BaseModel):
    sheet: str
    rows: list[list[str]]


class ColumnProposal(BaseModel):
    is_bill: bool = True
    header_row: int | None = Field(default=None, ge=1)
    columns: dict[str, int] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class BoqColumnReader:
    name = "boq_column_reader"
    route = COLUMNS_ROUTE
    confidence_threshold = 0.0  # a person confirms every column mapping

    def __init__(self, router: Router) -> None:
        self._router = router
        self.tools = ToolRegistry()
        self.tools.register(
            AgentTool(
                name="propose_columns",
                description="Say which columns of a client's bill hold which field.",
                level=AutonomyLevel.DRAFT,
            )
        )

    def run(self, request: AgentInput) -> AgentResult[ColumnProposal]:
        payload = request.payload
        if not isinstance(payload, SheetPreview):
            raise TypeError(f"{self.name} needs a SheetPreview, got {type(payload).__name__}")
        rows = "\n".join(
            f"{index}: " + " | ".join(cell or "·" for cell in row)
            for index, row in enumerate(payload.rows, start=1)
        )
        response = self._router.generate(
            COLUMNS_ROUTE,
            GenerationRequest(
                messages=(
                    Message(
                        role="user",
                        parts=(TextPart(f"Sheet {payload.sheet!r}, top rows:\n{rows}"),),
                    ),
                ),
                output_model=ColumnProposal,
            ),
        )
        proposal = response.parsed
        if not isinstance(proposal, ColumnProposal):
            raise TypeError("the gateway returned no parsed column proposal")
        width = max((len(row) for row in payload.rows), default=0)
        known = {
            name: column
            for name, column in proposal.columns.items()
            if name in FIELDS and 1 <= column <= width
        }
        if proposal.is_bill and not {"description", "quantity"} <= known.keys():
            proposal = ColumnProposal(
                is_bill=proposal.is_bill,
                header_row=proposal.header_row,
                columns=known,
                confidence=0.0,
                reason="no description or quantity column was named: a person reads it",
            )
        else:
            proposal = proposal.model_copy(update={"columns": known})
        return AgentResult(
            output=proposal,
            assertions=[
                Assertion(field="columns", value=proposal.columns, confidence=proposal.confidence)
            ],
            provider=response.provider,
            model=response.model,
            prompt_version=response.prompt_version,
        )


# --- Mapping --------------------------------------------------------------------------------


class ClientLineIn(BaseModel):
    ref: str
    section: str | None
    description: str
    unit: str | None
    quantity: str | None


class MeasuredLineIn(BaseModel):
    key: str
    description: str
    unit: str
    quantity: str


class MappingBatch(BaseModel):
    client: list[ClientLineIn]
    measured: list[MeasuredLineIn]


class LineMapping(BaseModel):
    line: str
    maps_to: str | None
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class MappingAnswer(BaseModel):
    mappings: list[LineMapping]


class BoqMapper:
    name = "boq_mapper"
    route = MAPPING_ROUTE
    confidence_threshold = 0.0  # a person confirms every mapping

    def __init__(self, router: Router) -> None:
        self._router = router
        self.tools = ToolRegistry()
        self.tools.register(
            AgentTool(
                name="map_boq_lines",
                description="Say which measured line each client BOQ line is.",
                level=AutonomyLevel.DRAFT,
            )
        )

    def run(self, request: AgentInput) -> AgentResult[MappingAnswer]:
        payload = request.payload
        if not isinstance(payload, MappingBatch):
            raise TypeError(f"{self.name} needs a MappingBatch, got {type(payload).__name__}")
        measured = "\n".join(
            f"- `{m.key}`: {m.description} ({m.quantity} {m.unit})" for m in payload.measured
        )
        client = "\n".join(
            f"- `{c.ref}` [{c.section or 'no section'}]: {c.description}"
            f" ({c.quantity or '-'} {c.unit or ''})"
            for c in payload.client
        )
        response = self._router.generate(
            MAPPING_ROUTE,
            GenerationRequest(
                messages=(
                    Message(
                        role="user",
                        parts=(
                            TextPart(f"Measured lines:\n{measured}", cache=True),
                            TextPart(f"Client lines to map:\n{client}"),
                        ),
                    ),
                ),
                output_model=MappingAnswer,
            ),
        )
        answer = response.parsed
        if not isinstance(answer, MappingAnswer):
            raise TypeError("the gateway returned no parsed mapping answer")
        keys = {m.key for m in payload.measured}
        refs = {c.ref for c in payload.client}
        cleaned = []
        for mapping in answer.mappings:
            if mapping.line not in refs:
                continue  # a line it was not asked about
            if mapping.maps_to is not None and mapping.maps_to not in keys:
                mapping = LineMapping(
                    line=mapping.line,
                    maps_to=None,
                    confidence=0.0,
                    reason=f"the model named {mapping.maps_to!r}, which is not a measured line",
                )
            cleaned.append(mapping)
        result = MappingAnswer(mappings=cleaned)
        return AgentResult(
            output=result,
            assertions=[
                Assertion(field=f"maps_to[{m.line}]", value=m.maps_to, confidence=m.confidence)
                for m in cleaned
            ],
            provider=response.provider,
            model=response.model,
            prompt_version=response.prompt_version,
        )
