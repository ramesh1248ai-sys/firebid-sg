"""Shadow mode (requirements §13.3): an estimator's manual takeoff beside the AI-assisted one.

    firebid-eval shadow --bid <uuid> --workbook takeoff.xlsx --hours 38.5 [--out report.md]

The manual takeoff is the golden-set workbook (`firebid-eval template`), filled in by the
estimator who took the tender off by hand, with the hours it took. The AI-assisted takeoff is
the bid's verified QTO items, and its effort is the time on task the workbench recorded.
The report compares them line by line: each object type's count and each diameter's pipe
length, then the effort, for the QTO effort KPI (§14: 30% less in Phase 1).

A sheet the estimator marked as repeating another (`duplicates_of`) is left out of the
manual totals, as the takeoff counts the installation once.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from firebid.evals.schema import TenderTruth

EFFORT_TARGET = 0.30  # §14: Phase 1 QTO effort reduction
PIPE = "pipe"


@dataclass(frozen=True)
class AiItem:
    """A verified QTO item as shadow mode needs it."""

    item_type: str
    unit: str
    net_quantity: Decimal
    nominal_diameter_mm: int | None = None


@dataclass
class Line:
    kind: str  # count | length
    key: str  # an object type, or a pipe diameter
    manual: float
    ai: float
    unit: str

    @property
    def difference(self) -> float:
        return self.ai - self.manual

    @property
    def percent(self) -> float | None:
        return None if self.manual == 0 else 100.0 * self.difference / self.manual


@dataclass
class ShadowReport:
    tender_id: str
    consultant: str
    lines: list[Line] = field(default_factory=list)
    manual_hours: float | None = None
    ai_hours: float | None = None
    synthetic: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def effort_reduction(self) -> float | None:
        if not self.manual_hours or self.ai_hours is None:
            return None
        return 1.0 - self.ai_hours / self.manual_hours

    def to_json(self) -> dict[str, Any]:
        out = asdict(self)
        out["effort_reduction"] = self.effort_reduction
        return out

    def markdown(self) -> str:
        head = [
            f"# Shadow mode: {self.tender_id}",
            "",
            f"Consultant: {self.consultant}."
            + (
                " **Synthetic tender: this shows the comparison works, not the pilot's result.**"
                if self.synthetic
                else ""
            ),
            "",
            "## Quantities, line by line",
            "",
            "| Item | Manual | AI-assisted | Difference | % |",
            "|---|---|---|---|---|",
        ]
        rows = []
        for line in self.lines:
            label = line.key if line.kind == "count" else f"pipe DN{line.key}"
            percent = "n/a" if line.percent is None else f"{line.percent:+.1f}%"
            rows.append(
                f"| {label} ({line.unit}) | {line.manual:g} | {line.ai:g} | "
                f"{line.difference:+g} | {percent} |"
            )
        effort = ["", "## Effort", ""]
        if self.manual_hours is None:
            effort.append("No manual hours were given.")
        else:
            effort.append(f"Manual takeoff: **{self.manual_hours:g} h**.")
        if self.ai_hours is None or self.ai_hours == 0:
            effort.append(
                "AI-assisted: **no workbench time recorded** for this bid, so the effort "
                "reduction cannot be measured yet."
            )
        else:
            effort.append(f"AI-assisted (workbench time on task): **{self.ai_hours:g} h**.")
        reduction = self.effort_reduction
        if reduction is not None and self.ai_hours:
            verdict = "meets" if reduction >= EFFORT_TARGET else "misses"
            effort.append(
                f"Effort reduction: **{100 * reduction:.0f}%** ({verdict} the Phase 1 target of "
                f"{100 * EFFORT_TARGET:.0f}%)."
            )
        notes = ["", "## Notes", "", *[f"- {note}" for note in self.notes]] if self.notes else []
        return "\n".join(head + rows + effort + notes) + "\n"


def manual_totals(truth: TenderTruth) -> tuple[dict[str, int], dict[int, int]]:
    """Counts by object type and pipe length (mm) by diameter, once per installation."""
    counts: dict[str, int] = defaultdict(int)
    lengths: dict[int, int] = defaultdict(int)
    for sheet in truth.current_sheets():
        if sheet.duplicates_of:
            continue  # repeats another sheet: counted there
        for entry in sheet.counts:
            counts[str(entry.object_type)] += entry.count
        for pipe in sheet.pipe_lengths:
            lengths[pipe.nominal_diameter_mm] += pipe.length_mm
    return dict(counts), dict(lengths)


def ai_totals(items: list[AiItem]) -> tuple[dict[str, int], dict[int, int]]:
    counts: dict[str, int] = defaultdict(int)
    lengths: dict[int, int] = defaultdict(int)
    for item in items:
        if item.item_type == PIPE and item.nominal_diameter_mm:
            lengths[item.nominal_diameter_mm] += int(item.net_quantity * 1000)
        elif item.unit in ("no", "nr"):
            counts[item.item_type] += int(item.net_quantity)
    return dict(counts), dict(lengths)


def compare(
    truth: TenderTruth,
    items: list[AiItem],
    *,
    manual_hours: float | None,
    ai_hours: float | None,
    synthetic: bool = False,
) -> ShadowReport:
    manual_counts, manual_lengths = manual_totals(truth)
    ai_counts, ai_lengths = ai_totals(items)
    report = ShadowReport(
        tender_id=truth.tender_id,
        consultant=truth.consultant,
        manual_hours=manual_hours,
        ai_hours=ai_hours,
        synthetic=synthetic,
    )
    for key in sorted(set(manual_counts) | set(ai_counts)):
        report.lines.append(
            Line("count", key, manual_counts.get(key, 0), ai_counts.get(key, 0), "nr")
        )
    for dn in sorted(set(manual_lengths) | set(ai_lengths)):
        report.lines.append(
            Line(
                "length",
                str(dn),
                manual_lengths.get(dn, 0) / 1000,
                ai_lengths.get(dn, 0) / 1000,
                "m",
            )
        )
    only_ai = sorted(set(ai_counts) - set(manual_counts))
    if only_ai:
        report.notes.append(
            "Counted by the platform but not in the manual takeoff: "
            + ", ".join(only_ai)
            + ". Either the estimator did not take these off, or the platform over-counts."
        )
    if truth.notes:
        report.notes.append(f"About the manual takeoff: {truth.notes}")
    return report


def ai_takeoff(session: Any, bid_id: Any) -> list[AiItem]:
    """The bid's verified QTO items (the AI-assisted takeoff, after a person's review)."""
    from firebid.services import qto

    out = []
    for item in qto.live_items(session, bid_id):
        if item.state not in ("verified", "baselined"):
            continue
        stated = dict(item.attributes or {}).get("nominal_diameter_mm")
        value = stated.get("value") if isinstance(stated, dict) else stated
        try:
            dn = int(str(value)) if value not in (None, "", "not specified") else None
        except ValueError:
            dn = None
        out.append(AiItem(item.item_type, item.unit, item.net_quantity, dn))
    return out


# The synthetic tender's symbols as the object types an estimator would count.
_SYNTHETIC_TYPES = {
    "SPK-PEND": "sprinkler_pendent",
    "SPK-UP": "sprinkler_upright",
    "SPK-SW": "sprinkler_sidewall",
    "VLV-GATE": "gate_valve",
    "VLV-CHK": "check_valve",
}


def synthetic_manual_takeoff() -> TenderTruth:
    """What an estimator taking the synthetic tender (`synthetic_qto`) off by hand would
    write: the general arrangement counted and measured, the enlarged plan and the riser
    schematic marked as repeating it."""
    from math import hypot

    from firebid.evals import synthetic_qto

    _, drawn = synthetic_qto.general_arrangement()
    counts: dict[str, int] = defaultdict(int)
    for symbol in drawn.symbols:
        kind = _SYNTHETIC_TYPES.get(symbol.block)
        if kind:
            counts[kind] += 1
    lengths: dict[int, float] = defaultdict(float)
    for pipe in drawn.pipes:
        lengths[pipe.dn] += hypot(pipe.x1 - pipe.x0, pipe.y1 - pipe.y0)
    return TenderTruth.model_validate(
        {
            "tender_id": "SYNTH-SHADOW-001",
            "consultant": "ALPHA CONSULTANTS PTE LTD (synthetic)",
            "input_class": "dwg",
            "sheets": [
                {
                    "sheet_number": "FP-L05-201",
                    "revision": "R01",
                    "input_class": "dwg",
                    "counts": [{"object_type": k, "count": v} for k, v in sorted(counts.items())],
                    "pipe_lengths": [
                        {"nominal_diameter_mm": dn, "length_mm": round(mm)}
                        for dn, mm in sorted(lengths.items())
                    ],
                },
                {
                    "sheet_number": "FP-L05-301",
                    "revision": "R01",
                    "input_class": "dwg",
                    "duplicates_of": ["FP-L05-201"],
                },
                {
                    "sheet_number": "FP-SCH-001",
                    "revision": "R01",
                    "input_class": "dwg",
                    "duplicates_of": ["FP-L05-201"],
                },
            ],
            "notes": "synthetic: written from the generator's own truth, not by an estimator",
        }
    )
