"""The synthetic installation, revised: seeded additions, removals and changes (P2-02).

Revision R01 is `synthetic_qto.general_arrangement()`. Revision R02 is the same floor after
an addendum, with exactly these changes, each a `Seeded` entry of the truth:

* **added:** a pendent head on the first branch, between its first two heads;
* **removed:** the last sidewall head, at the end of the last branch;
* **changed:** one upright head is now a pendent; and the second branch is now DN65.

Nothing else differs, so a comparison that reports anything more, or less, is wrong.

`shift` moves the whole plan (its grid with it) on the sheet, as a consultant who re-plots a
drawing does: the grid still says where everything is. `with_grid=False` leaves the grid off
both revisions, so only the frame, or a fit, can align them.
"""

from __future__ import annotations

from dataclasses import dataclass

from ezdxf.document import Drawing

from firebid.evals import synthetic
from firebid.evals import synthetic_symbols as symbols
from firebid.evals.synthetic import LAYER_PIPE, LAYER_TEXT, SHEET_ORIGIN, SHEET_SIZE
from firebid.evals.synthetic_network import BRANCH_XS, HEAD_YS, NETWORK
from firebid.evals.synthetic_qto import CEILING_NOTE, Label, Pipe, Symbol, installation

NUMBER = "FP-L05-201"
ADDED_HEAD = (BRANCH_XS[0], 7_500.0)
REMOVED_HEAD = (BRANCH_XS[5], HEAD_YS[-1])
RETYPED_HEAD = (BRANCH_XS[4], HEAD_YS[1])
RESIZED_BRANCH_X = BRANCH_XS[1]


@dataclass(frozen=True)
class Seeded:
    """One change the revision makes, in drawing units of the unshifted plan."""

    change: str  # added | removed | changed
    kind: str  # object | run
    object_type: str
    x: float
    y: float
    before: dict[str, object] | None = None
    after: dict[str, object] | None = None


SEEDED: tuple[Seeded, ...] = (
    Seeded("added", "object", "sprinkler_pendent", *ADDED_HEAD),
    Seeded("removed", "object", "sprinkler_sidewall", *REMOVED_HEAD),
    Seeded(
        "changed",
        "object",
        "sprinkler_pendent",
        *RETYPED_HEAD,
        before={"object_type": "sprinkler_upright"},
        after={"object_type": "sprinkler_pendent"},
    ),
    Seeded(
        "changed",
        "run",
        "pipe_branch",
        RESIZED_BRANCH_X,
        (3_000.0 + HEAD_YS[-1]) / 2,
        before={"dn": 50},
        after={"dn": 65},
    ),
)


def revised_installation() -> tuple[list[Symbol], list[Pipe], list[Label]]:
    placed, pipes, labels = installation()
    placed = [s for s in placed if (s.x, s.y) != REMOVED_HEAD]
    placed = [Symbol("SPK-PEND", s.x, s.y) if (s.x, s.y) == RETYPED_HEAD else s for s in placed]
    placed.append(Symbol("SPK-PEND", *ADDED_HEAD))
    pipes = [
        Pipe(p.x0, p.y0, p.x1, p.y1, 65) if p.x0 == p.x1 == RESIZED_BRANCH_X else p for p in pipes
    ]
    labels = [
        Label("DN65", label.x, label.y, label.rotation)
        if label.text == "DN50" and abs(label.x - (RESIZED_BRANCH_X - 450)) < 1
        else label
        for label in labels
    ]
    return placed, pipes, labels


def sheet(
    revision: str = "R01",
    *,
    revised: bool = False,
    shift: tuple[float, float] = (0.0, 0.0),
    with_grid: bool = True,
    number: str = NUMBER,
) -> Drawing:
    """The general arrangement at a revision: as first issued, or with the seeded changes."""
    document, space = synthetic._new_drawing()
    symbols._define(document, NETWORK)
    placed, pipes, labels = revised_installation() if revised else installation()
    first = len(space)
    for symbol in placed:
        symbols._insert(space, symbol.block, symbol.x, symbol.y, symbol.rotation)
    for pipe in pipes:
        space.add_line((pipe.x0, pipe.y0), (pipe.x1, pipe.y1), dxfattribs={"layer": LAYER_PIPE})
    for label in labels:
        space.add_text(
            label.text, height=250, rotation=label.rotation, dxfattribs={"layer": LAYER_TEXT}
        ).set_placement((label.x, label.y))
    if with_grid:
        synthetic._structural_grid(space, 8, 6)
    synthetic._dimensions(space)
    if shift != (0.0, 0.0):
        from ezdxf.math import Matrix44

        for entity in list(space)[first:]:
            entity.transform(Matrix44.translate(shift[0], shift[1], 0))
    synthetic._view_title(space, "LEVEL 5 SPRINKLER LAYOUT PLAN", "1:100")
    right = SHEET_ORIGIN[0] + SHEET_SIZE[0] - 11_000
    top = SHEET_ORIGIN[1] + SHEET_SIZE[1] - 2_000
    symbols._legend(space, NETWORK, right, top, pitch=1_000)
    space.add_text(CEILING_NOTE, height=250, dxfattribs={"layer": LAYER_TEXT}).set_placement(
        (SHEET_ORIGIN[0] + 1_500, SHEET_ORIGIN[1] + 1_500)
    )
    synthetic._title_block(
        space,
        number,
        revision,
        "1:100",
        title="LEVEL 5 SPRINKLER LAYOUT PLAN",
        consultant=NETWORK.name,
    )
    return document
