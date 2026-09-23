"""What a predictor claims about a tender, in the same shape as the truth it is scored against.

Later steps implement `Predictor`: P1-02 for sheet classification, P1-04 and P1-05 for counts
and pipe networks. This module exists so those steps have one thing to return and the metrics
have one thing to read.

Confidence appears per assertion rather than per tender, because a calibration figure over a
whole tender says nothing useful — the question is whether the model's 0.9 means 0.9.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from firebid.evals.schema import ObjectType, RevisionStatus, TenderTruth


class CountPrediction(BaseModel):
    model_config = ConfigDict(frozen=True)

    object_type: ObjectType
    count: int = Field(ge=0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class LengthPrediction(BaseModel):
    model_config = ConfigDict(frozen=True)

    nominal_diameter_mm: int = Field(gt=0)
    length_mm: int = Field(ge=0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class SheetPrediction(BaseModel):
    """What the platform thinks one sheet contains and is."""

    model_config = ConfigDict(frozen=True)

    sheet_number: str
    revision: str
    status: RevisionStatus = RevisionStatus.CURRENT
    counts: tuple[CountPrediction, ...] = ()
    pipe_lengths: tuple[LengthPrediction, ...] = ()
    duplicates_of: tuple[str, ...] = ()

    def count_of(self, object_type: ObjectType) -> int:
        return sum(entry.count for entry in self.counts if entry.object_type is object_type)

    def length_at(self, diameter_mm: int) -> int:
        return sum(
            entry.length_mm
            for entry in self.pipe_lengths
            if entry.nominal_diameter_mm == diameter_mm
        )


class BoqMappingPrediction(BaseModel):
    model_config = ConfigDict(frozen=True)

    line_reference: str
    maps_to: ObjectType | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class TenderPrediction(BaseModel):
    """The platform's answer for one tender. Scored against the matching `TenderTruth`."""

    model_config = ConfigDict(frozen=True)

    tender_id: str
    sheets: tuple[SheetPrediction, ...] = ()
    boq_mappings: tuple[BoqMappingPrediction, ...] = ()

    def sheet(self, sheet_number: str, revision: str | None = None) -> SheetPrediction | None:
        for prediction in self.sheets:
            if prediction.sheet_number == sheet_number and (
                revision is None or prediction.revision == revision
            ):
                return prediction
        return None


@runtime_checkable
class Predictor(Protocol):
    """What later steps implement so the harness can score them.

    `name` and `version` land in the report, so a result can be tied to what produced it.
    """

    name: str
    version: str

    def predict(self, truth: TenderTruth) -> TenderPrediction:
        """Answer for one tender.

        The truth is passed in for its *inputs* — sheet numbers, the drawings behind them —
        not its answers. A predictor that reads the counts is measuring nothing, which is
        exactly what the dummy predictor does on purpose and nothing else may.
        """
        ...
