"""The golden set format: what "the right answer" looks like for one historical tender.

An estimator fills a spreadsheet; this is what it becomes. Everything a Phase 1 metric needs
is here and nothing else, because every extra field is one more thing for a busy estimator to
get wrong.

**Confidential data never enters git** (guardrail 8). A manifest carries checksums and the
object-storage key for the drawings; the drawings themselves live in the eval bucket. The
manifest is safe to commit — it names sheets and counts, not client content.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InputClass(StrEnum):
    """How the drawings arrived. Accuracy differs sharply between these, so every report is
    sliced by it — a 98% average hiding 60% on scans would be a dangerous number."""

    VECTOR_PDF = "vector_pdf"
    DWG = "dwg"
    RASTER = "raster"
    MIXED = "mixed"


class ObjectType(StrEnum):
    """The canonical fire-protection objects counted (requirements §6.4): Phase 1's, and
    the Phase 2 equipment (FR-VIS-04, FR-QTO-06)."""

    SPRINKLER_PENDENT = "sprinkler_pendent"
    SPRINKLER_UPRIGHT = "sprinkler_upright"
    SPRINKLER_SIDEWALL = "sprinkler_sidewall"
    SPRINKLER_CONCEALED = "sprinkler_concealed"
    HOSE_REEL = "hose_reel"
    LANDING_VALVE = "landing_valve"
    BREECHING_INLET = "breeching_inlet"
    RISING_MAIN = "rising_main"
    FLOW_SWITCH = "flow_switch"
    GATE_VALVE = "gate_valve"
    BUTTERFLY_VALVE = "butterfly_valve"
    CHECK_VALVE = "check_valve"
    ALARM_VALVE = "alarm_valve"
    SPRINKLER_CONTROL_VALVE = "sprinkler_control_valve"
    HYDRANT = "hydrant"
    FIRE_PUMP = "fire_pump"
    JOCKEY_PUMP = "jockey_pump"
    PUMP_CONTROLLER = "pump_controller"
    FIRE_WATER_TANK = "fire_water_tank"
    TEST_HEADER = "test_header"
    DRY_PIPE_VALVE_SET = "dry_pipe_valve_set"
    PRE_ACTION_VALVE_SET = "pre_action_valve_set"
    DELUGE_VALVE_SET = "deluge_valve_set"
    AIR_COMPRESSOR = "air_compressor"


class RevisionStatus(StrEnum):
    CURRENT = "current"
    SUPERSEDED = "superseded"


class ObjectCount(BaseModel):
    """How many of one object type are on one sheet, as verified by an estimator."""

    model_config = ConfigDict(frozen=True)

    object_type: ObjectType
    count: int = Field(ge=0)


class PipeLength(BaseModel):
    """Centreline length at one nominal diameter, on one sheet.

    Millimetres, as integers, matching the domain's `LengthMm`: a float metre value that has
    been through a spreadsheet twice is not the same number it started as.
    """

    model_config = ConfigDict(frozen=True)

    nominal_diameter_mm: int = Field(gt=0)
    length_mm: int = Field(ge=0)


class SheetTruth(BaseModel):
    """The verified answer for one sheet."""

    model_config = ConfigDict(frozen=True)

    sheet_number: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    status: RevisionStatus = RevisionStatus.CURRENT
    input_class: InputClass
    # A sheet drawn to no scale cannot have its lengths measured, so it is excluded from the
    # pipe-length metric rather than scored as a miss.
    not_to_scale: bool = False
    counts: tuple[ObjectCount, ...] = ()
    pipe_lengths: tuple[PipeLength, ...] = ()
    # Sheet numbers this sheet duplicates content from, e.g. an enlarged plan of part of a
    # general arrangement. Seeded duplicates drive the duplicate-detection metric.
    duplicates_of: tuple[str, ...] = ()

    def count_of(self, object_type: ObjectType) -> int:
        return sum(entry.count for entry in self.counts if entry.object_type is object_type)

    def total_objects(self) -> int:
        return sum(entry.count for entry in self.counts)

    def length_at(self, diameter_mm: int) -> int:
        return sum(
            entry.length_mm
            for entry in self.pipe_lengths
            if entry.nominal_diameter_mm == diameter_mm
        )


class BoqLineTruth(BaseModel):
    """One client BOQ line and the quantity an estimator priced it at."""

    model_config = ConfigDict(frozen=True)

    line_reference: str = Field(min_length=1)
    description: str
    unit: str
    quantity: float
    # Which canonical object type this line maps to, where it maps to one. Blank is a real
    # answer: plenty of BOQ lines are preliminaries or provisional sums.
    maps_to: ObjectType | None = None


class TenderTruth(BaseModel):
    """One historical tender, verified. The unit a golden set is made of."""

    model_config = ConfigDict(frozen=True)

    tender_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9._-]+$")
    consultant: str = Field(min_length=1)
    received_on: date | None = None
    input_class: InputClass
    sheets: tuple[SheetTruth, ...]
    boq_lines: tuple[BoqLineTruth, ...] = ()
    notes: str = ""

    @model_validator(mode="after")
    def _check_sheets(self) -> TenderTruth:
        problems: list[str] = []
        seen: set[tuple[str, str]] = set()
        for sheet in self.sheets:
            key = (sheet.sheet_number, sheet.revision)
            if key in seen:
                problems.append(
                    f"sheet {sheet.sheet_number} revision {sheet.revision} appears twice"
                )
            seen.add(key)

        numbers = {sheet.sheet_number for sheet in self.sheets}
        for sheet in self.sheets:
            for source in sheet.duplicates_of:
                if source not in numbers:
                    problems.append(
                        f"sheet {sheet.sheet_number} is marked a duplicate of "
                        f"'{source}', which is not in this tender"
                    )

        # Exactly one current revision per sheet number, which is what the platform must work
        # out for itself later; the golden set has to be unambiguous about it.
        for number in sorted(numbers):
            current = [
                sheet
                for sheet in self.sheets
                if sheet.sheet_number == number and sheet.status is RevisionStatus.CURRENT
            ]
            if len(current) != 1:
                problems.append(
                    f"sheet {number} has {len(current)} current revisions; exactly one is needed"
                )

        if problems:
            raise ValueError("; ".join(problems))
        return self

    def current_sheets(self) -> tuple[SheetTruth, ...]:
        return tuple(sheet for sheet in self.sheets if sheet.status is RevisionStatus.CURRENT)

    def total_objects(self) -> int:
        return sum(sheet.total_objects() for sheet in self.current_sheets())


class SourceFile(BaseModel):
    """A drawing in the eval bucket. Only its checksum and key live in git."""

    model_config = ConfigDict(frozen=True)

    filename: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(ge=0)
    storage_key: str


class TenderManifest(BaseModel):
    """What git holds about a tender: identity, checksums, and where the files are.

    Deliberately free of client content, so committing it discloses nothing.
    """

    model_config = ConfigDict(frozen=True)

    tender_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9._-]+$")
    consultant: str
    input_class: InputClass
    sheet_count: int = Field(ge=0)
    files: tuple[SourceFile, ...] = ()
    truth_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    data_owner: str = ""
    collected_on: date | None = None

    @staticmethod
    def checksum(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()


class GoldenSet(BaseModel):
    """A named collection of tenders, e.g. `synthetic` or `historical-2026`."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    tenders: tuple[TenderTruth, ...]

    def by_input_class(self) -> dict[InputClass, tuple[TenderTruth, ...]]:
        grouped: dict[InputClass, list[TenderTruth]] = {}
        for tender in self.tenders:
            grouped.setdefault(tender.input_class, []).append(tender)
        return {key: tuple(value) for key, value in sorted(grouped.items())}

    def by_consultant(self) -> dict[str, tuple[TenderTruth, ...]]:
        grouped: dict[str, list[TenderTruth]] = {}
        for tender in self.tenders:
            grouped.setdefault(tender.consultant, []).append(tender)
        return {key: tuple(value) for key, value in sorted(grouped.items())}


def diameters_in(tenders: Iterable[TenderTruth]) -> tuple[int, ...]:
    """Every nominal diameter the set mentions, so reports cover them all."""
    found: set[int] = set()
    for tender in tenders:
        for sheet in tender.sheets:
            found.update(entry.nominal_diameter_mm for entry in sheet.pipe_lengths)
    return tuple(sorted(found))
