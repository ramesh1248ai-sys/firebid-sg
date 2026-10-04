"""Phase 2's estimating work at tender scale, timed (P2-09; NFR-01).

The Phase 1 benchmarks time ingestion and takeoff. Phase 2 added work an estimator waits
for on a page: pricing and the cost build-up, the labour estimate, drafting and exporting
clarifications, and finding risks. Each is run here on a synthetic bill far larger than a
tender's, in process, and held to the budget NFR-01 gives a workbench interaction: 2 seconds.

    python -m firebid.evals.p2_workload --out ../eval/results/bench/p2_workload.json
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

BUDGET_SECONDS = 2.0
LINES = 5_000
CLAUSES = 2_000
CANDIDATES = 500
TODAY = date(2026, 10, 4)


@dataclass
class Timing:
    workload: str
    size: str
    seconds: float
    budget_seconds: float

    @property
    def within_budget(self) -> bool:
        return self.seconds <= self.budget_seconds


def _timed(name: str, size: str, work: Callable[[], object]) -> Timing:
    started = time.perf_counter()
    work()
    return Timing(name, size, round(time.perf_counter() - started, 3), BUDGET_SECONDS)


def _build_up() -> object:
    from firebid.domain.values import Money
    from firebid.pricing import buildup

    groups = ("Pipe", "Fittings", "Valves and ancillaries", "Equipment")
    bill = [
        buildup.BillLine(
            reference=f"L{index}",
            group=groups[index % len(groups)],
            quantity=Decimal(index % 90 + 1),
            amount=Money.of(Decimal(index % 90 + 1) * Decimal("21.35")),
            unit_rate=Money.of(Decimal("21.35")),
            allowance_percent=Decimal(5),
        )
        for index in range(LINES)
    ]
    entered = [
        buildup.Entered(
            "preliminaries", "percentage", "Bench", TODAY, percent=Decimal(5), base="direct"
        ),
        buildup.Entered("margin", "percentage", "Bench", TODAY, percent=Decimal(8), base="cost"),
    ]
    return buildup.build(bill, entered, TODAY)


def _landed() -> object:
    from firebid.pricing import landed

    fx = landed.FxRate("USD", Decimal("1.35"), "bench", TODAY)
    return [
        landed.landed(Decimal(index % 400 + 1), "USD", fx=fx, delivery_terms="FOB")
        for index in range(LINES)
    ]


def _labour() -> object:
    from firebid.labour import estimate as est
    from firebid.labour import multipliers, productivity, rates
    from firebid.pricing.keys import ItemKey

    entries = [
        productivity.Entry(
            id=f"e{dn}",
            type="pipe",
            dn=str(dn),
            unit="m",
            hours=Decimal("0.30") + Decimal(dn) / 1000,
            trade="pipefitter",
            source_type="company_standard",
            source_reference="bench",
        )
        for dn in range(15, 215)
    ]
    items = multipliers.by_key()
    confirmed = [
        multipliers.Condition("night_work", None, "Bench"),
        multipliers.Condition("basement", "B1", "Bench"),
    ]
    rate = rates.trade_rate("pipefitter", TODAY)
    lines = []
    for index in range(LINES):
        level = "B1" if index % 3 == 0 else None
        key = ItemKey.of(type="pipe", dn=15 + index % 200)
        lines.append(
            est.line_hours(
                est.BillLine(str(index), f"L{index}", "pipe", "SPRINKLER", level, "m", Decimal(12)),
                productivity.match(key, "m", entries),
                multipliers.applied_to(level, confirmed, items),
                rate,
            )
        )
    return est.estimate(lines)


def _clarifications() -> object:
    from firebid.clarifications import drafting, export
    from firebid.clarifications.drafting import Candidate, EvidenceRef, SheetRef

    candidates = [
        Candidate(
            kind="spec_issue",
            ref=f"issue-{index}",
            subject=f"Pipe material on FP-L{index % 40:02d}-201",
            problem="The specification and the drawing do not agree. Please confirm which governs.",
            evidence=(
                EvidenceRef(kind="clause", label="Specification clause 2.1.3", quote="galvanised"),
                EvidenceRef(kind="sheet", label=f"Drawing FP-L{index % 40:02d}-201", quote="BLACK"),
            ),
            system="sprinkler",
            sheets=(SheetRef(sheet_number=f"FP-L{index % 40:02d}-201", revision="R01"),),
            options=("the specification governs",),
            topic="pipe_material",
            group_key=f"sprinkler|FP-L{index % 40:02d}-201",
        )
        for index in range(CANDIDATES)
    ]
    groups = drafting.propose_groups(candidates)
    drafts = [drafting.compose(group.candidates, "Bench Tower") for group in groups]
    rows = [
        {"number": f"TC-{index:03d}", "subject": draft.subject, "problem": draft.problem}
        for index, draft in enumerate(drafts, start=1)
    ]
    return export.build("company_default", "xlsx", "Bench Tower", rows)


def _risks() -> object:
    from firebid.evals.synthetic_spec import with_risks
    from firebid.risk import rules

    base = list(with_risks())
    clauses = [
        (f"{index}.{number}", heading, text)
        for index in range(CLAUSES // len(base) + 1)
        for number, heading, text in base
    ][:CLAUSES]
    words = [rules.Words("clause", f"Specification clause {n}", text) for n, _, text in clauses]
    found = rules.design_risks(clauses)
    found += rules.execution_risks(
        [f"L{index:02d}" for index in range(40)] + ["B1", "B2"],
        {f"L{index:02d}": (Decimal(2750 + index * 60), "bench") for index in range(40)},
        (Decimal(42), "bench"),
        words,
    )
    return found


WORKLOADS: tuple[tuple[str, str, Callable[[], object]], ...] = (
    ("cost build-up", f"{LINES} bill lines", _build_up),
    ("landed cost", f"{LINES} quotation lines", _landed),
    ("labour estimate", f"{LINES} bill lines, 200 library entries", _labour),
    ("clarifications: group, draft, export", f"{CANDIDATES} candidates", _clarifications),
    ("risk rules", f"{CLAUSES} clauses, 42 levels", _risks),
)


def run() -> list[Timing]:
    return [_timed(name, size, work) for name, size, work in WORKLOADS]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None)
    arguments = parser.parse_args(argv)
    timings = run()
    for timing in timings:
        verdict = "ok" if timing.within_budget else "OVER BUDGET"
        print(f"{timing.workload:<40} {timing.size:<40} {timing.seconds:>7.3f} s  {verdict}")
    if arguments.out is not None:
        arguments.out.parent.mkdir(parents=True, exist_ok=True)
        arguments.out.write_text(
            json.dumps(
                {
                    "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "budget_seconds": BUDGET_SECONDS,
                    "passed": all(timing.within_budget for timing in timings),
                    "timings": [
                        {**asdict(timing), "within_budget": timing.within_budget}
                        for timing in timings
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0 if all(timing.within_budget for timing in timings) else 1


if __name__ == "__main__":
    raise SystemExit(main())
