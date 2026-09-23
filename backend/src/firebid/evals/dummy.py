"""A predictor that returns the truth, wrong by exactly as much as you ask for.

It exists to exercise the harness: a runner, a report and a regression gate are all untestable
without something whose accuracy you can dial. Injecting a known regression and watching the
gate fail is the only way to know the gate works.

It is the one predictor allowed to read the answers. Everything else implementing `Predictor`
must derive its answer from the drawings.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from firebid.evals.prediction import (
    BoqMappingPrediction,
    CountPrediction,
    LengthPrediction,
    SheetPrediction,
    TenderPrediction,
)
from firebid.evals.schema import RevisionStatus, TenderTruth


@dataclass
class DummyPredictor:
    """Truth, degraded on purpose.

    * `count_error` — fraction each count is out by, e.g. 0.05 reports 95 where 100 exist.
    * `length_error` — the same for pipe lengths.
    * `miss_rate` — the chance a sheet's findings are dropped entirely.
    * `duplicate_recall` — the fraction of seeded duplicates it notices.
    * `confidence` — what it claims, regardless of whether it is right. Setting this to 1.0
      with a non-zero error is how an overconfident model is simulated for calibration.
    """

    name: str = "dummy"
    version: str = "1"
    count_error: float = 0.0
    length_error: float = 0.0
    miss_rate: float = 0.0
    duplicate_recall: float = 1.0
    boq_accuracy: float = 1.0
    confidence: float = 1.0
    seed: int = 0

    def predict(self, truth: TenderTruth) -> TenderPrediction:
        # Seeded per tender, so a run is reproducible and two runs of the same predictor on
        # the same set give the same report.
        rng = random.Random(f"{self.seed}:{truth.tender_id}")  # noqa: S311  # not cryptography

        sheets: list[SheetPrediction] = []
        for sheet in truth.sheets:
            dropped = rng.random() < self.miss_rate

            counts = tuple(
                CountPrediction(
                    object_type=entry.object_type,
                    count=0 if dropped else _degrade(entry.count, self.count_error),
                    confidence=self.confidence,
                )
                for entry in sheet.counts
            )
            lengths = tuple(
                LengthPrediction(
                    nominal_diameter_mm=entry.nominal_diameter_mm,
                    length_mm=0 if dropped else _degrade(entry.length_mm, self.length_error),
                    confidence=self.confidence,
                )
                for entry in sheet.pipe_lengths
            )
            duplicates = tuple(
                source for source in sheet.duplicates_of if rng.random() < self.duplicate_recall
            )
            sheets.append(
                SheetPrediction(
                    sheet_number=sheet.sheet_number,
                    revision=sheet.revision,
                    status=sheet.status if not dropped else RevisionStatus.CURRENT,
                    counts=counts,
                    pipe_lengths=lengths,
                    duplicates_of=duplicates,
                )
            )

        mappings = tuple(
            BoqMappingPrediction(
                line_reference=line.line_reference,
                maps_to=line.maps_to if rng.random() < self.boq_accuracy else None,
                confidence=self.confidence,
            )
            for line in truth.boq_lines
        )
        return TenderPrediction(
            tender_id=truth.tender_id, sheets=tuple(sheets), boq_mappings=mappings
        )


def _degrade(value: int, error: float) -> int:
    """Under-report by `error`.

    Rounds rather than truncates. Truncating made the knob lie: `count_error=0.001` on a
    48-head sheet dropped a whole head, which is a 2% error, and on a 6-head sheet 16%. A
    fraction too small to move an integer count should move nothing, and does.
    """
    if error <= 0:
        return value
    return max(0, round(value * (1.0 - error)))
