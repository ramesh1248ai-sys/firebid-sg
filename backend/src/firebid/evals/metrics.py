"""The Phase 1 KPIs from requirements §14, computed the same way every time.

**Undefined is not zero, and it is not one.** Every formula in §14 divides by a verified
count, so a sheet with nothing to find has no score. Returning 1.0 would let empty sheets
flatter the average; returning 0.0 would punish the platform for correctly finding nothing.
Both are wrong in ways nobody notices until a target is missed and the number cannot be
explained. So an undefined metric is `None`, it is excluded from aggregates, and the count of
exclusions is reported alongside the result.

**Two of these are offline approximations**, and say so: `false_detection_rate` is defined in
§14 by what a human rejects, which needs the workbench (P1-08), so here it measures detections
in excess of truth. `qto_effort` cannot be computed from drawings at all and is not here.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from firebid.evals.prediction import TenderPrediction
from firebid.evals.schema import ObjectType, TenderTruth

# Confidence bins for calibration. Ten is the usual choice and keeps each bin readable.
CALIBRATION_BINS = 10


@dataclass(frozen=True)
class MetricValue:
    """One measurement, or an honest absence of one."""

    name: str
    value: float | None
    # What the ratio was computed from, so a reader can see 2/3 rather than only 0.667.
    numerator: float = 0.0
    denominator: float = 0.0
    undefined_reason: str | None = None

    @property
    def defined(self) -> bool:
        return self.value is not None

    def __str__(self) -> str:
        if self.value is None:
            return f"{self.name}: not measurable ({self.undefined_reason})"
        return f"{self.name}: {self.value:.4f} ({self.numerator:g}/{self.denominator:g})"


@dataclass
class Aggregate:
    """A metric across many sheets or tenders, with what it had to leave out."""

    name: str
    value: float | None
    samples: int = 0
    excluded: int = 0
    excluded_reasons: dict[str, int] = field(default_factory=dict)

    def __str__(self) -> str:
        if self.value is None:
            return f"{self.name}: not measurable (0 of {self.excluded} samples usable)"
        note = f", {self.excluded} excluded" if self.excluded else ""
        return f"{self.name}: {self.value:.4f} over {self.samples} sample(s){note}"


def undefined(name: str, reason: str) -> MetricValue:
    return MetricValue(name=name, value=None, undefined_reason=reason)


def aggregate(name: str, values: Iterable[MetricValue]) -> Aggregate:
    """Mean of the defined values. Undefined ones are counted, not silently dropped."""
    usable: list[float] = []
    excluded = 0
    reasons: dict[str, int] = {}
    for measurement in values:
        if measurement.value is None:
            excluded += 1
            key = measurement.undefined_reason or "undefined"
            reasons[key] = reasons.get(key, 0) + 1
        else:
            usable.append(measurement.value)

    return Aggregate(
        name=name,
        value=sum(usable) / len(usable) if usable else None,
        samples=len(usable),
        excluded=excluded,
        excluded_reasons=reasons,
    )


SPRINKLERS = (
    ObjectType.SPRINKLER_PENDENT,
    ObjectType.SPRINKLER_UPRIGHT,
    ObjectType.SPRINKLER_SIDEWALL,
    ObjectType.SPRINKLER_CONCEALED,
)


# The Phase 2 equipment and valve sets (FR-VIS-04, FR-QTO-06).
EQUIPMENT = (
    ObjectType.FIRE_PUMP,
    ObjectType.JOCKEY_PUMP,
    ObjectType.PUMP_CONTROLLER,
    ObjectType.FIRE_WATER_TANK,
    ObjectType.BREECHING_INLET,
    ObjectType.LANDING_VALVE,
    ObjectType.HYDRANT,
    ObjectType.HOSE_REEL,
    ObjectType.TEST_HEADER,
    ObjectType.DRY_PIPE_VALVE_SET,
    ObjectType.PRE_ACTION_VALVE_SET,
    ObjectType.DELUGE_VALVE_SET,
    ObjectType.AIR_COMPRESSOR,
)


# ---------------------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------------------


def sprinkler_count_accuracy(truth: TenderTruth, prediction: TenderPrediction) -> list[MetricValue]:
    """§14: 1 - |AI - verified| / verified, per sheet.

    Clamped at zero: a prediction of 300 against a verified 100 is simply wrong, not -2.0
    wrong, and letting one sheet go that far negative would drag a whole tender's mean into
    nonsense.
    """
    results: list[MetricValue] = []
    for sheet in truth.current_sheets():
        name = f"sprinkler_count_accuracy[{sheet.sheet_number}]"
        verified = sum(sheet.count_of(kind) for kind in SPRINKLERS)
        if verified == 0:
            results.append(undefined(name, "no sprinklers on this sheet"))
            continue
        guessed = _predicted_sprinklers(prediction, sheet.sheet_number)
        accuracy = max(0.0, 1.0 - abs(guessed - verified) / verified)
        results.append(
            MetricValue(name=name, value=accuracy, numerator=guessed, denominator=verified)
        )
    return results


def _predicted_sprinklers(prediction: TenderPrediction, sheet_number: str) -> int:
    sheet = prediction.sheet(sheet_number)
    if sheet is None:
        return 0
    return sum(sheet.count_of(kind) for kind in SPRINKLERS)


def equipment_count_accuracy(truth: TenderTruth, prediction: TenderPrediction) -> list[MetricValue]:
    """1 - |AI - verified| / verified, per sheet and per type of equipment (FR-VIS-04).

    A type at a time, not the sheet's equipment added up: a pump room with one pump too
    many and one tank too few has not counted its equipment correctly.
    """
    results: list[MetricValue] = []
    for sheet in truth.current_sheets():
        predicted = prediction.sheet(sheet.sheet_number)
        for kind in EQUIPMENT:
            verified = sheet.count_of(kind)
            if verified == 0:
                continue
            guessed = predicted.count_of(kind) if predicted is not None else 0
            results.append(
                MetricValue(
                    name=f"equipment_count_accuracy[{sheet.sheet_number}/{kind}]",
                    value=max(0.0, 1.0 - abs(guessed - verified) / verified),
                    numerator=guessed,
                    denominator=verified,
                )
            )
    if not results:
        results.append(undefined("equipment_count_accuracy", "no equipment in this tender"))
    return results


def pipe_length_error(truth: TenderTruth, prediction: TenderPrediction) -> list[MetricValue]:
    """§14: |AI - verified| / verified, by diameter, per sheet.

    This is an **error**, not an accuracy: the ±5% target means smaller is better. Named so
    nobody reads 0.04 as a bad score.

    Not-to-scale sheets are excluded rather than scored: a sheet drawn to no scale has no
    measurable length, so a miss there is not a failure to measure.
    """
    results: list[MetricValue] = []
    for sheet in truth.current_sheets():
        for entry in sheet.pipe_lengths:
            name = f"pipe_length_error[{sheet.sheet_number}/DN{entry.nominal_diameter_mm}]"
            if sheet.not_to_scale:
                results.append(undefined(name, "sheet is not to scale"))
                continue
            verified = entry.length_mm
            if verified == 0:
                results.append(undefined(name, "no pipe at this diameter"))
                continue
            predicted_sheet = prediction.sheet(sheet.sheet_number)
            guessed = (
                predicted_sheet.length_at(entry.nominal_diameter_mm)
                if predicted_sheet is not None
                else 0
            )
            results.append(
                MetricValue(
                    name=name,
                    value=abs(guessed - verified) / verified,
                    numerator=abs(guessed - verified),
                    denominator=verified,
                )
            )
    return results


def missed_item_rate(truth: TenderTruth, prediction: TenderPrediction) -> MetricValue:
    """§14: verified items not detected / verified items. Lower is better."""
    verified = 0
    missed = 0
    for sheet in truth.current_sheets():
        predicted_sheet = prediction.sheet(sheet.sheet_number)
        for entry in sheet.counts:
            verified += entry.count
            found = predicted_sheet.count_of(entry.object_type) if predicted_sheet else 0
            missed += max(entry.count - found, 0)

    if verified == 0:
        return undefined("missed_item_rate", "no verified items in this tender")
    return MetricValue(
        name="missed_item_rate",
        value=missed / verified,
        numerator=missed,
        denominator=verified,
    )


def false_detection_rate(truth: TenderTruth, prediction: TenderPrediction) -> MetricValue:
    """Detections in excess of what was verified, / detections. Lower is better.

    §14 defines this by what a person **rejects**, which needs the review workbench (P1-08).
    Offline, excess over truth is the closest honest proxy, and it is not the same number: a
    detection that is wrong but that a reviewer would have accepted counts here and would not
    count there.
    """
    detected = 0
    excess = 0
    for sheet in prediction.sheets:
        truth_sheet = next(
            (
                candidate
                for candidate in truth.current_sheets()
                if candidate.sheet_number == sheet.sheet_number
            ),
            None,
        )
        for entry in sheet.counts:
            detected += entry.count
            verified = truth_sheet.count_of(entry.object_type) if truth_sheet else 0
            excess += max(entry.count - verified, 0)

    if detected == 0:
        return undefined("false_detection_rate", "nothing was detected")
    return MetricValue(
        name="false_detection_rate",
        value=excess / detected,
        numerator=excess,
        denominator=detected,
    )


# ---------------------------------------------------------------------------------------
# Sheets and duplicates
# ---------------------------------------------------------------------------------------


def duplicate_detection_rate(truth: TenderTruth, prediction: TenderPrediction) -> MetricValue:
    """§14: duplicates flagged / total duplicates. Higher is better."""
    expected: set[tuple[str, str]] = {
        (sheet.sheet_number, source) for sheet in truth.sheets for source in sheet.duplicates_of
    }
    if not expected:
        return undefined("duplicate_detection_rate", "no duplicates seeded in this tender")

    flagged: set[tuple[str, str]] = {
        (sheet.sheet_number, source)
        for sheet in prediction.sheets
        for source in sheet.duplicates_of
    }
    found = len(expected & flagged)
    return MetricValue(
        name="duplicate_detection_rate",
        value=found / len(expected),
        numerator=found,
        denominator=len(expected),
    )


def sheet_classification_accuracy(truth: TenderTruth, prediction: TenderPrediction) -> MetricValue:
    """Sheet number, revision and current/superseded, all three right, / sheets.

    All three together rather than separately: a sheet read as the right number at the wrong
    revision is not two-thirds correct, it is the wrong drawing.
    """
    if not truth.sheets:
        return undefined("sheet_classification_accuracy", "no sheets in this tender")

    correct = 0
    for sheet in truth.sheets:
        predicted = prediction.sheet(sheet.sheet_number, sheet.revision)
        if predicted is not None and predicted.status is sheet.status:
            correct += 1
    return MetricValue(
        name="sheet_classification_accuracy",
        value=correct / len(truth.sheets),
        numerator=correct,
        denominator=len(truth.sheets),
    )


def drawing_number_accuracy(truth: TenderTruth, prediction: TenderPrediction) -> MetricValue:
    """Sheets whose drawing number was read / sheets (FR-DOC-02: target ≥ 95% on vector).

    Matched by value, not by file: a register is right when it holds the right numbers, and a
    golden set records numbers, not which file each came from.
    """
    if not truth.sheets:
        return undefined("drawing_number_accuracy", "no sheets in this tender")
    correct = sum(1 for sheet in truth.sheets if prediction.sheet(sheet.sheet_number) is not None)
    return MetricValue(
        name="drawing_number_accuracy",
        value=correct / len(truth.sheets),
        numerator=correct,
        denominator=len(truth.sheets),
    )


def revision_accuracy(truth: TenderTruth, prediction: TenderPrediction) -> MetricValue:
    """Sheets read at the right revision / sheets (FR-DOC-02: target ≥ 95% on vector)."""
    if not truth.sheets:
        return undefined("revision_accuracy", "no sheets in this tender")
    correct = sum(
        1
        for sheet in truth.sheets
        if prediction.sheet(sheet.sheet_number, sheet.revision) is not None
    )
    return MetricValue(
        name="revision_accuracy",
        value=correct / len(truth.sheets),
        numerator=correct,
        denominator=len(truth.sheets),
    )


# ---------------------------------------------------------------------------------------
# BOQ
# ---------------------------------------------------------------------------------------


def boq_mapping_accuracy(truth: TenderTruth, prediction: TenderPrediction) -> MetricValue:
    """Lines mapped to the right object type / lines.

    A line that maps to nothing — preliminaries, a PC sum — is scored: predicting "nothing"
    for it is correct, and predicting an object type for it is wrong. Skipping those would
    hide the model's habit of forcing a mapping onto every line.
    """
    if not truth.boq_lines:
        return undefined("boq_mapping_accuracy", "no BOQ lines in this tender")

    predicted_by_reference = {
        mapping.line_reference: mapping.maps_to for mapping in prediction.boq_mappings
    }
    correct = sum(
        1
        for line in truth.boq_lines
        if predicted_by_reference.get(line.line_reference) == line.maps_to
    )
    return MetricValue(
        name="boq_mapping_accuracy",
        value=correct / len(truth.boq_lines),
        numerator=correct,
        denominator=len(truth.boq_lines),
    )


# ---------------------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReliabilityBin:
    lower: float
    upper: float
    count: int
    mean_confidence: float
    accuracy: float

    def __str__(self) -> str:
        return (
            f"[{self.lower:.1f}-{self.upper:.1f}) n={self.count} "
            f"said {self.mean_confidence:.2f}, was right {self.accuracy:.2f}"
        )


def calibration(
    outcomes: Sequence[tuple[float, bool]], bins: int = CALIBRATION_BINS
) -> tuple[MetricValue, list[ReliabilityBin]]:
    """Expected calibration error, plus the bins it came from.

    A model that says 0.9 should be right about 90% of the time. ECE is the average gap
    between what it said and what happened, weighted by how many claims fell in each bin.
    Zero is perfect.

    This matters more than raw accuracy for an escalation threshold: if the model's 0.9 is
    really 0.6, a threshold set at 0.85 is passing through work nobody checks.
    """
    if not outcomes:
        return undefined("calibration_error", "no confidence-bearing claims"), []

    edges = [index / bins for index in range(bins + 1)]
    reliability: list[ReliabilityBin] = []
    total_gap = 0.0

    for index in range(bins):
        lower, upper = edges[index], edges[index + 1]
        # The last bin includes 1.0, which would otherwise fall outside every bin.
        in_bin = [
            (confidence, correct)
            for confidence, correct in outcomes
            if lower <= confidence < upper or (index == bins - 1 and confidence == 1.0)
        ]
        if not in_bin:
            continue
        mean_confidence = sum(confidence for confidence, _ in in_bin) / len(in_bin)
        accuracy = sum(1 for _, correct in in_bin if correct) / len(in_bin)
        reliability.append(
            ReliabilityBin(
                lower=lower,
                upper=upper,
                count=len(in_bin),
                mean_confidence=mean_confidence,
                accuracy=accuracy,
            )
        )
        total_gap += len(in_bin) * abs(mean_confidence - accuracy)

    return (
        MetricValue(
            name="calibration_error",
            value=total_gap / len(outcomes),
            numerator=total_gap,
            denominator=len(outcomes),
        ),
        reliability,
    )


def count_outcomes(truth: TenderTruth, prediction: TenderPrediction) -> list[tuple[float, bool]]:
    """Confidence-bearing claims and whether each was right, for calibration.

    One claim per sheet and object type: the model said `count` with `confidence`, and it was
    either the verified number or it was not.
    """
    outcomes: list[tuple[float, bool]] = []
    for sheet in prediction.sheets:
        truth_sheet = next(
            (
                candidate
                for candidate in truth.current_sheets()
                if candidate.sheet_number == sheet.sheet_number
            ),
            None,
        )
        for entry in sheet.counts:
            verified = truth_sheet.count_of(entry.object_type) if truth_sheet else 0
            outcomes.append((entry.confidence, entry.count == verified))
    return outcomes


# ---------------------------------------------------------------------------------------
# One tender, all metrics
# ---------------------------------------------------------------------------------------


@dataclass
class TenderScore:
    """Every Phase 1 metric for one tender."""

    tender_id: str
    consultant: str
    input_class: str
    sprinkler_count_accuracy: Aggregate
    pipe_length_error: Aggregate
    equipment_count_accuracy: Aggregate
    missed_item_rate: MetricValue
    false_detection_rate: MetricValue
    duplicate_detection_rate: MetricValue
    sheet_classification_accuracy: MetricValue
    drawing_number_accuracy: MetricValue
    revision_accuracy: MetricValue
    boq_mapping_accuracy: MetricValue
    calibration_error: MetricValue
    reliability: list[ReliabilityBin] = field(default_factory=list)

    def named_values(self) -> dict[str, float | None]:
        """Flat name-to-value, which is what baselines and reports compare."""
        return {
            "sprinkler_count_accuracy": self.sprinkler_count_accuracy.value,
            "pipe_length_error": self.pipe_length_error.value,
            "equipment_count_accuracy": self.equipment_count_accuracy.value,
            "missed_item_rate": self.missed_item_rate.value,
            "false_detection_rate": self.false_detection_rate.value,
            "duplicate_detection_rate": self.duplicate_detection_rate.value,
            "sheet_classification_accuracy": self.sheet_classification_accuracy.value,
            "drawing_number_accuracy": self.drawing_number_accuracy.value,
            "revision_accuracy": self.revision_accuracy.value,
            "boq_mapping_accuracy": self.boq_mapping_accuracy.value,
            "calibration_error": self.calibration_error.value,
        }


def score_tender(truth: TenderTruth, prediction: TenderPrediction) -> TenderScore:
    calibration_value, reliability = calibration(count_outcomes(truth, prediction))
    return TenderScore(
        tender_id=truth.tender_id,
        consultant=truth.consultant,
        input_class=str(truth.input_class),
        sprinkler_count_accuracy=aggregate(
            "sprinkler_count_accuracy", sprinkler_count_accuracy(truth, prediction)
        ),
        pipe_length_error=aggregate("pipe_length_error", pipe_length_error(truth, prediction)),
        equipment_count_accuracy=aggregate(
            "equipment_count_accuracy", equipment_count_accuracy(truth, prediction)
        ),
        missed_item_rate=missed_item_rate(truth, prediction),
        false_detection_rate=false_detection_rate(truth, prediction),
        duplicate_detection_rate=duplicate_detection_rate(truth, prediction),
        sheet_classification_accuracy=sheet_classification_accuracy(truth, prediction),
        drawing_number_accuracy=drawing_number_accuracy(truth, prediction),
        revision_accuracy=revision_accuracy(truth, prediction),
        boq_mapping_accuracy=boq_mapping_accuracy(truth, prediction),
        calibration_error=calibration_value,
        reliability=reliability,
    )


# Whether a bigger number is better, which the regression gate needs to know.
HIGHER_IS_BETTER = {
    "sprinkler_count_accuracy": True,
    "pipe_length_error": False,
    "equipment_count_accuracy": True,
    "missed_item_rate": False,
    "false_detection_rate": False,
    "duplicate_detection_rate": True,
    "sheet_classification_accuracy": True,
    "drawing_number_accuracy": True,
    "revision_accuracy": True,
    "boq_mapping_accuracy": True,
    "calibration_error": False,
}

# Targets from §14, for the report to show what "good" is. Not enforced by the gate: the gate
# guards against getting worse, targets say whether it is good enough yet.
PHASE_1_TARGETS = {
    "sprinkler_count_accuracy": 0.98,
    "pipe_length_error": 0.05,
    "missed_item_rate": 0.05,
    "false_detection_rate": 0.05,
    "duplicate_detection_rate": 0.95,
    # FR-DOC-02, on vector title blocks.
    "drawing_number_accuracy": 0.95,
    "revision_accuracy": 0.95,
}
