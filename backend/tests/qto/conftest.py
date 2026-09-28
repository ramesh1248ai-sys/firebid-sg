"""The synthetic tender, read once per session: the QTO engine's inputs for each sheet set.

A verified specification is stood in for by a lookup giving what P1-06 would: sprinkler
finishes by type (pendent chrome, sidewall white), K80 everywhere, black steel pipe,
threaded to DN50 and grooved above.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from firebid.evals import synthetic_qto as fixture
from firebid.evals.qto_pipeline import Sheet, read
from firebid.qto import dedup, generate, rules
from firebid.qto.model import Detection, ItemDraft, Run, SpecValue

RULES = {rule.key: rule for rule in rules.seed_rules()}
CEILING = rules.Parameter(
    "ceiling_height_mm", 2750, "sheet FP-L05-201 note 'CEILING HEIGHT 2750'", "L05"
)


def citation(clause: str) -> dict[str, str]:
    return {"clause": clause, "quote": f"clause {clause}"}


def spec(system: str, dn: int | None) -> dict[str, list[SpecValue]]:
    if dn is None:
        return {
            "sprinkler_type": [
                SpecValue("pendent", "2.2.1", citation("2.2.1")),
                SpecValue("sidewall", "2.2.2", citation("2.2.2")),
            ],
            "k_factor": [
                SpecValue("80", "2.2.1", citation("2.2.1")),
                SpecValue("80", "2.2.2", citation("2.2.2")),
            ],
            "finish": [
                SpecValue("chrome", "2.2.1", citation("2.2.1")),
                SpecValue("white", "2.2.2", citation("2.2.2")),
            ],
        }
    return {
        "pipe_material": [SpecValue("black_steel", "2.1.1", citation("2.1.1"))],
        "joining_method": [
            SpecValue("threaded" if dn <= 50 else "grooved", "2.1.2", citation("2.1.2"))
        ],
    }


@dataclass
class Tender:
    detections: list[Detection]
    runs: list[Run]
    groups: list[dedup.Group]

    def items(
        self,
        parameters: list[rules.Parameter] | None = None,
        rule_set: dict[str, rules.Rule] | None = None,
        decisions: dict[str, str] | None = None,
    ) -> list[ItemDraft]:
        excluded, lengths = dedup.exclusions(self.groups, decisions)
        return generate.generate(
            self.detections,
            self.runs,
            spec,
            rule_set or RULES,
            [CEILING] if parameters is None else parameters,
            excluded=excluded,
            excluded_length=lengths,
            carried=dedup.carried_sizes(self.groups),
        )


def _tender(sheets: list[Sheet]) -> Tender:
    detections, runs = read(sheets)
    return Tender(detections, runs, dedup.find(detections, runs))


@pytest.fixture(scope="session")
def general() -> Tender:
    document, _ = fixture.general_arrangement()
    return _tender([Sheet("FP-L05-201", document)])


@pytest.fixture(scope="session")
def with_enlarged_and_schematic() -> Tender:
    general, _ = fixture.general_arrangement()
    enlarged, _ = fixture.enlarged_plan()
    schematic, _ = fixture.riser_schematic()
    return _tender(
        [
            Sheet("FP-L05-201", general),
            Sheet("FP-L05-301", enlarged, "1:50"),
            Sheet("FP-SCH-001", schematic, "NTS"),
        ]
    )


@pytest.fixture(scope="session")
def match_lined() -> Tender:
    (west, _), (east, _) = fixture.match_lined_pair()
    return _tender([Sheet("FP-L05-202", west), Sheet("FP-L05-203", east)])


def by_description(items: list[ItemDraft]) -> dict[str, ItemDraft]:
    out = {item.description: item for item in items}
    assert len(out) == len(items), "two items share a description"
    return out
