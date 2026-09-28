"""A DXF sheet's tiles and its geometry describe the same sheet (P1-08, ADR-005).

The workbench draws detections in sheet millimetres (from the geometry) over the tiles
(from the renderer). They agree only if the rendered image is exactly the sheet: the
drawing's extents, edge to edge. Found in the P1-08 live check: matplotlib padded the axes,
so the overlay sat about 10% too large and off to one side.
"""

from __future__ import annotations

import numpy as np
import pytest

from firebid.drawings import geometry
from firebid.evals import synthetic, synthetic_qto
from firebid.evals.synthetic import LAYER_PIPE
from firebid.parsing.dxf import inspect_dxf, render_layout
from firebid.parsing.geometry_dxf import extract

PX_PER_MM = 4


@pytest.mark.req("FR-REV-01")
def test_every_pipe_line_lands_on_drawn_pixels() -> None:
    document, _ = synthetic_qto.general_arrangement()
    payload = synthetic.dxf_bytes(document)
    width_mm, height_mm = (
        inspect_dxf(payload)[0]["width_mm"],
        inspect_dxf(payload)[0]["height_mm"],
    )
    found = extract(payload, None)
    assert found["page"][2] == pytest.approx(width_mm)
    width_px, height_px = round(width_mm * PX_PER_MM), round(height_mm * PX_PER_MM)
    image = render_layout(payload, 0, width_px, height_px)
    pixels = np.frombuffer(image["pixels"], dtype=np.uint8).reshape(height_px, width_px, 3)

    lines = geometry.segments(geometry.from_parquet(found["parquet"]))
    pipe = lines.layer == LAYER_PIPE
    assert pipe.sum() >= 10
    missed = []
    for x0, y0, x1, y1 in zip(
        lines.x0[pipe], lines.y0[pipe], lines.x1[pipe], lines.y1[pipe], strict=True
    ):
        x, y = (x0 + x1) / 2 * PX_PER_MM, (y0 + y1) / 2 * PX_PER_MM
        # Something other than white within a pixel of where the geometry says the line is.
        window = pixels[
            max(int(y) - 1, 0) : int(y) + 2,
            max(int(x) - 1, 0) : int(x) + 2,
        ]
        if window.min() > 200:
            missed.append((round(x), round(y)))
    assert missed == []
