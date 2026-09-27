"""A synthetic sprinkler installation: symbols on a connected pipe network (P1-05).

One floor of a wet-pipe system, drawn the way consultant Alpha draws it at 1:100:

* a riser at the west end, marked by its symbol and "RISER R1";
* a DN150 main running east through a gate valve and a non-return valve, reducing to DN100
  at a reducer, and ending in a short stub past the last branch;
* six DN50 branches running north off the main at 3 m centres, each carrying four heads at
  3 m: four branches of pendents, one of uprights, one of sidewalls (turned to face east);
* size annotations as consultants write them: "150Ø" and "DN100" on the main, "DN50" on
  each branch, turned to run along it.

With `conflict=True`, the last branch carries a second, wrong annotation ("DN65"): its size
must be flagged, not guessed. With `jitter`, symbols and annotations are nudged and
distractor symbols scattered, so the confidence can be calibrated against real mistakes.

The truth is computed from what is drawn: the length of every pipe line per DN, and the
count of each installed symbol. The legend in the corner is not installed anywhere.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from ezdxf.document import Drawing
from ezdxf.layouts import Modelspace

from firebid.evals import synthetic
from firebid.evals import synthetic_symbols as symbols
from firebid.evals.synthetic import LAYER_PIPE, LAYER_TEXT, SHEET_ORIGIN, SHEET_SIZE

R = symbols.R
MAIN_Y = 3_000.0
RISER_X = 1_000.0
BRANCH_XS = (3_000.0, 6_000.0, 9_000.0, 12_000.0, 15_000.0, 18_000.0)
HEAD_YS = (6_000.0, 9_000.0, 12_000.0, 15_000.0)
MAIN_END_X = 19_000.0
GATE_X, CHECK_X, REDUCER_X = 1_800.0, 2_500.0, 10_500.0
# Which head each branch carries, west to east.
BRANCH_HEADS = ("SPK-PEND", "SPK-PEND", "SPK-PEND", "SPK-PEND", "SPK-UP", "SPK-SW")

NETWORK = symbols.Consultant(
    name=symbols.ALPHA.name,
    symbols=(
        *symbols.ALPHA.symbols,
        symbols.Symbol("FTG-RED", "REDUCER", "fitting", "reducer"),
        symbols.Symbol("RSR", "SPRINKLER RISER", "pipe", "riser"),
    ),
)
TYPES = {symbol.block: symbol.object_type for symbol in NETWORK.symbols}
DESCRIBED = {symbol.description: symbol.object_type for symbol in NETWORK.symbols}


@dataclass
class NetworkTruth:
    counts: dict[str, int] = field(default_factory=dict)
    # Drawn pipe length per nominal diameter, in millimetres.
    lengths: dict[int, float] = field(default_factory=dict)
    # Sheet positions (drawing units) of every installed symbol, with its type.
    placed: list[tuple[float, float, str]] = field(default_factory=list)
    conflict_branch_x: float | None = None
    # (fault, branch x) for every branch whose size annotation is missing, wrong or doubled.
    label_faults: list[tuple[str, float]] = field(default_factory=list)


LAYER_DECOY = "FP-DETAIL"


def _decoy(
    space: Modelspace, block: str, x: float, y: float, rotation: float, scale: float
) -> None:
    space.add_blockref(
        block,
        (x, y),
        dxfattribs={"layer": LAYER_DECOY, "rotation": rotation, "xscale": scale, "yscale": scale},
    )


def _pipe(space: Modelspace, x0: float, y0: float, x1: float, y1: float) -> float:
    space.add_line((x0, y0), (x1, y1), dxfattribs={"layer": LAYER_PIPE})
    return math.hypot(x1 - x0, y1 - y0)


def _label(space: Modelspace, text: str, x: float, y: float, rotation: float = 0.0) -> None:
    space.add_text(
        text, height=250, rotation=rotation, dxfattribs={"layer": LAYER_TEXT}
    ).set_placement((x, y))


def network_plan(
    *,
    conflict: bool = False,
    with_legend: bool = True,
    jitter: float = 0.0,
    distractors: int = 0,
    near_misses: int = 0,
    label_noise: float = 0.0,
    seed: int = 0,
    sheet_number: str = "FP-L05-201",
) -> tuple[Drawing, NetworkTruth]:
    rng = random.Random(seed)  # noqa: S311  # reproducible fixtures, not cryptography
    document, space = synthetic._new_drawing()
    symbols._define(document, NETWORK)
    truth = NetworkTruth()

    def place(block: str, x: float, y: float, rotation: float = 0.0) -> None:
        dx, dy = (rng.uniform(-jitter, jitter) for _ in range(2)) if jitter else (0.0, 0.0)
        turn = rotation + (rng.uniform(-3, 3) if jitter else 0.0)
        symbols._insert(space, block, x + dx, y + dy, turn)
        kind = TYPES[block]
        if kind != "pipe":  # the riser is a vertical pipe, not a counted object
            truth.counts[kind] = truth.counts.get(kind, 0) + 1
        truth.placed.append((x, y, kind))

    def length(dn: int, value: float) -> None:
        truth.lengths[dn] = truth.lengths.get(dn, 0.0) + value

    # The riser, and the main from it: DN150 through the valves to the reducer.
    place("RSR", RISER_X, MAIN_Y)
    _label(space, "RISER R1", RISER_X - 600, MAIN_Y + 500)
    place("VLV-GATE", GATE_X, MAIN_Y)
    place("VLV-CHK", CHECK_X, MAIN_Y)
    place("FTG-RED", REDUCER_X, MAIN_Y)
    # Pipe runs up to each fitting's body and on from its far side.
    stops = [RISER_X + R * 0.8, GATE_X - R, GATE_X + R, CHECK_X - R, CHECK_X + R, REDUCER_X - R]
    for x0, x1 in zip(stops[0::2], stops[1::2], strict=True):
        length(150, _pipe(space, x0, MAIN_Y, x1, MAIN_Y))
    length(100, _pipe(space, REDUCER_X + R, MAIN_Y, MAIN_END_X, MAIN_Y))
    _label(space, "150Ø", 4_200, MAIN_Y + 250)
    _label(space, "DN100", 13_300, MAIN_Y + 250)

    # The branches: pipe from the tee to the last head, heads along it.
    for index, (x, head) in enumerate(zip(BRANCH_XS, BRANCH_HEADS, strict=True)):
        length(50, _pipe(space, x, MAIN_Y, x, HEAD_YS[-1]))
        for y in HEAD_YS:
            place(head, x, y, 90.0 if head == "SPK-SW" else 0.0)
        roll = rng.random() if label_noise else 1.0
        if roll < label_noise / 3:
            truth.label_faults.append(("missing", x))  # no size annotation at all
        elif roll < 2 * label_noise / 3:
            _label(space, "DN65", x - 450, 7_000, rotation=90)  # the wrong size, alone
            truth.label_faults.append(("wrong", x))
        else:
            _label(space, "DN50", x - 450, 7_000, rotation=90)
        if (conflict and index == len(BRANCH_XS) - 1) or (
            label_noise and label_noise / 3 * 2 <= roll < label_noise
        ):
            _label(space, "DN65", x - 450, 13_000, rotation=90)
            truth.conflict_branch_x = x
            truth.label_faults.append(("conflict", x))

    # Distractors: real symbols in a "typical detail" off the network, on a layer of their
    # own. They are drawn, but not installed: they are not in the truth.
    for _ in range(distractors):
        block = rng.choice(["SPK-PEND", "SPK-UP", "VLV-GATE"])
        x, y = rng.uniform(20_500, 23_500), rng.uniform(6_000, 16_000)
        _decoy(space, block, x, y, rng.uniform(0, 360), rng.uniform(0.8, 1.2))
    # Near misses: a legend symbol with one stroke too many. Some are close enough to be
    # matched; none is what the legend says. Half sit on branches, where topology cannot
    # give them away.
    for index in range(near_misses):
        base = rng.choice(["SPK-PEND", "SPK-UP", "VLV-GATE", "SPK-SW"])
        name = f"NM-{seed}-{index}"
        layout = document.blocks.new(name)
        symbols._graphic(layout, next(s.draw for s in NETWORK.symbols if s.block == base))
        angle, reach = rng.uniform(0, 2 * math.pi), rng.uniform(0.15, 0.9) * R
        layout.add_line(
            (0, 0), (reach * math.cos(angle), reach * math.sin(angle)), dxfattribs={"layer": "0"}
        )
        if index % 2 == 0:
            x, y = rng.choice(BRANCH_XS), rng.choice((7_500.0, 10_500.0, 13_500.0))
        else:
            x, y = rng.uniform(20_500, 23_500), rng.uniform(6_000, 16_000)
        _decoy(space, name, x, y, rng.uniform(0, 360), 1.0)

    synthetic._structural_grid(space, 8, 6)
    synthetic._dimensions(space)
    synthetic._view_title(space, "LEVEL 5 SPRINKLER LAYOUT PLAN", "1:100")
    if with_legend:
        right = SHEET_ORIGIN[0] + SHEET_SIZE[0] - 11_000
        top = SHEET_ORIGIN[1] + SHEET_SIZE[1] - 2_000
        symbols._legend(space, NETWORK, right, top, pitch=1_000)
    synthetic._title_block(
        space,
        sheet_number,
        "R01",
        "1:100",
        title="FIRE SPRINKLER LAYOUT",
        consultant=NETWORK.name,
    )
    return document, truth
