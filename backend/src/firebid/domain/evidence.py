"""Lineage and evidence records.

``SourceRef`` is the lineage every extracted item carries (FR-DOC-07). ``EvidenceRecord``
holds the full field set of requirements Appendix B and is stored as JSONB.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from firebid.domain.values import CalculationMethod, ExtractionMethod


class Region(BaseModel):
    """A rectangle on a sheet, in paper millimetres."""

    model_config = ConfigDict(frozen=True)

    x_min_mm: float
    y_min_mm: float
    x_max_mm: float
    y_max_mm: float


class SourceRef(BaseModel):
    """Where something came from: the lineage required by FR-DOC-07."""

    model_config = ConfigDict(frozen=True)

    document_id: UUID
    sheet_id: UUID | None = None
    sheet_number: str | None = None
    revision_label: str | None = None
    page_or_layout: str | None = None
    region: Region | None = None


class Location(BaseModel):
    """Where in the building, as an estimator reads it."""

    model_config = ConfigDict(frozen=True)

    level: str | None = None
    zone: str | None = None
    grid_from: str | None = None
    grid_to: str | None = None


class RunMetadata(BaseModel):
    """Which run produced this, so a result can be reproduced or explained."""

    model_config = ConfigDict(frozen=True)

    agent_run_id: UUID | None = None
    model: str | None = None
    prompt_version: str | None = None
    rule_set_version: str | None = None


class EvidenceRecord(BaseModel):
    """Requirements Appendix B, in full. Every QTO item must have one."""

    model_config = ConfigDict(frozen=True)

    qto_human_id: str
    bid_human_id: str
    project_name: str
    item_description: str
    classification: str
    attributes: dict[str, str] = Field(default_factory=dict)
    quantity: Decimal
    unit: str
    quantity_note: str | None = None
    source: SourceRef
    location: Location = Field(default_factory=Location)
    geometry_reference: str | None = None
    detection_method: ExtractionMethod
    calculation_method: CalculationMethod
    calculation_note: str | None = None
    evidence_links: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    verification_status: str
    verified_by: str | None = None
    verified_at: datetime | None = None
    linked_boq_line: str | None = None
    run_metadata: RunMetadata = Field(default_factory=RunMetadata)

    def missing_mandatory_fields(self) -> list[str]:
        """Fields FR-QTO-09 requires. The completeness check at G1 uses this.

        Every Appendix B field a quantity has when it is taken off. The linked BOQ line and
        the verifier come later (P1-09, and a person's verification), so they are not here.
        """
        missing: list[str] = []
        for name in ("qto_human_id", "bid_human_id", "project_name", "item_description"):
            if not str(getattr(self, name)).strip():
                missing.append(name)
        if not self.classification.strip():
            missing.append("classification")
        if not self.unit.strip():
            missing.append("unit")
        if not self.location.level:
            missing.append("location.level")
        if not self.calculation_note:
            missing.append("calculation_note")
        if not (self.run_metadata.agent_run_id or self.run_metadata.rule_set_version):
            missing.append("run_metadata")
        if not self.geometry_reference:
            missing.append("geometry_reference")
        if not self.evidence_links:
            missing.append("evidence_links")
        if not self.source.sheet_id and not self.source.sheet_number:
            missing.append("source.sheet")
        if not self.source.revision_label:
            missing.append("source.revision_label")
        return missing
