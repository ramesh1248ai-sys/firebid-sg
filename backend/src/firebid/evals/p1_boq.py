"""Client BOQ mapping accuracy (FR-BOQ-02: at least 90% of client lines mapped correctly).

    firebid-eval run --suite p1_boq [--report eval/results/p1_boq.md]

Measured on the synthetic tender: its general arrangement goes through detection and
takeoff in memory (`qto_pipeline`), the company BOQ is built from the result with the seed
template, and the client's bill (`synthetic_boq`) is mapped to it by the rules. Each client
line's truth is the takeoff item it is (or nothing: a provisional sum, a flow switch nobody
drew); a mapping is right when the line it names holds that item.

What this does not measure yet: the model's share (it maps what the rules leave, and needs
a live provider), and a real tender's bill. A golden set of client bills goes in
`eval/truth/p1_boq/` once pilot tenders are imported (P1-12); until then the figure is the
synthetic one, and says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from firebid.boq import generate, matching
from firebid.boq.reader import read_workbook
from firebid.evals import synthetic_boq
from firebid.evals import synthetic_qto as fixture
from firebid.evals.qto_pipeline import Sheet, read

SUITE = "p1_boq"
TARGET = 0.90


@dataclass
class Outcome:
    client_item: str
    description: str
    truth: str | None  # the takeoff item's description, or None
    mapped_to: str | None  # the company line's description, or None
    right: bool
    reason: str


@dataclass
class Report:
    outcomes: list[Outcome] = field(default_factory=list)
    left_for_the_model: list[str] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return sum(o.right for o in self.outcomes) / len(self.outcomes) if self.outcomes else 0.0

    @property
    def met(self) -> bool:
        return self.accuracy >= TARGET

    def markdown(self) -> str:
        lines = [
            f"# {SUITE}: client BOQ mapping (FR-BOQ-02)",
            "",
            f"Source: synthetic tender (rules only). Accuracy **{self.accuracy:.1%}** against a "
            f"target of {TARGET:.0%}: {'met' if self.met else 'NOT met'}.",
            "",
            "| Client item | Description | Truth | Mapped to | Right | Why |",
            "|---|---|---|---|---|---|",
        ]
        for o in self.outcomes:
            lines.append(
                f"| {o.client_item} | {o.description} | {o.truth or '(nothing measured)'} | "
                f"{o.mapped_to or '(nothing)'} | {'yes' if o.right else 'NO'} | {o.reason} |"
            )
        if self.left_for_the_model:
            lines += [
                "",
                "Left for the model (counted as unmapped here): "
                + ", ".join(self.left_for_the_model),
            ]
        return "\n".join(lines) + "\n"


def _no_specification(system: str, dn: int | None) -> dict[str, Any]:
    return {}


def takeoff_items() -> list[generate.Item]:
    """The synthetic general arrangement's takeoff, as the BOQ generator takes it."""
    from firebid.qto import dedup, rules
    from firebid.qto import generate as takeoff

    document, _ = fixture.general_arrangement()
    detections, runs = read([Sheet("FP-L05-201", document)])
    groups = dedup.find(detections, runs)
    excluded, lengths = dedup.exclusions(groups)
    ceiling = rules.Parameter("ceiling_height_mm", 2750, "synthetic ceiling note", "L05")
    drafts = takeoff.generate(
        detections,
        runs,
        _no_specification,
        {rule.key: rule for rule in rules.seed_rules()},
        [ceiling],
        excluded=excluded,
        excluded_length=lengths,
    )
    return [
        generate.Item(
            id=f"i{index}",
            human_id=f"QTO-{index:06d}",
            item_type=d.item_type,
            classification=d.classification,
            description=d.description,
            attributes={k: str(v.get("value")) for k, v in d.attributes.items()},
            unit=d.unit,
            net_quantity=d.net_quantity,
            allowance_percent=d.allowance_percent,
            level=d.level,
        )
        for index, d in enumerate(drafts, start=1)
    ]


def evaluate() -> Report:
    items = takeoff_items()
    by_id = {item.id: item for item in items}
    drafts = generate.generate(items, generate.seed_templates()[0])
    measured = {
        str(index): draft
        for index, draft in enumerate(drafts)
        if draft.item_ids  # a line with no item cannot be what a client line is
    }
    bill = synthetic_boq.client_boq()
    truth = {line.item: line.maps_to for _, _, lines in synthetic_boq.SECTIONS for line in lines}
    [sheet] = read_workbook(bill.payload).sheets
    client_lines = [line for line in sheet.lines if line.item_no in truth]
    proposals, left = matching.match(
        [
            matching.Client(line.item_no or "", line.description, line.unit, line.kind)
            for line in client_lines
        ],
        [matching.Measured(key, d.description, d.unit) for key, d in measured.items()],
    )
    answered = {p.ref: p for p in proposals}
    report = Report(left_for_the_model=[c.ref for c in left])
    for line in client_lines:
        item_no = line.item_no or ""
        expected = truth[item_no]
        proposal = answered.get(item_no)
        target = measured.get(proposal.maps_to) if proposal and proposal.maps_to else None
        holds = {by_id[i].description for i in target.item_ids} if target else set()
        right = (expected in holds) if expected is not None else target is None
        report.outcomes.append(
            Outcome(
                client_item=item_no,
                description=line.description,
                truth=expected,
                mapped_to=target.description if target else None,
                right=right,
                reason=proposal.reason if proposal else "left for the model",
            )
        )
    return report
