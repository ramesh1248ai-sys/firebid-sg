"""A region of a sheet as a PNG, drawn from its extracted geometry (P1-04).

A legend row is shown to the model and to a person as an image. It is drawn from the
Parquet geometry, not the tender file, so it can be made anywhere and says exactly what the
platform read: if a stroke is missing from the crop, it was missing from the extraction.
"""

from __future__ import annotations

import io

import numpy as np
import pyarrow as pa

from firebid.drawings.geometry import Kind, segments, texts

PIXELS_PER_MM = 12


def render(
    table: pa.Table, box: tuple[float, float, float, float], margin_mm: float = 2.0
) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from matplotlib.patches import Circle

    x0, y0, x1, y1 = box[0] - margin_mm, box[1] - margin_mm, box[2] + margin_mm, box[3] + margin_mm
    width, height = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    figure = plt.figure(
        figsize=(width * PIXELS_PER_MM / 100, height * PIXELS_PER_MM / 100), dpi=100
    )
    axes = figure.add_axes((0, 0, 1, 1))
    axes.set_xlim(x0, x1)
    axes.set_ylim(y1, y0)  # sheet y points down
    axes.set_axis_off()
    figure.patch.set_facecolor("white")

    lines = segments(table)
    near = (
        (np.maximum(lines.x0, lines.x1) >= x0)
        & (np.minimum(lines.x0, lines.x1) <= x1)
        & (np.maximum(lines.y0, lines.y1) >= y0)
        & (np.minimum(lines.y0, lines.y1) <= y1)
    )
    for i in np.flatnonzero(near):
        axes.plot(
            [lines.x0[i], lines.x1[i]], [lines.y0[i], lines.y1[i]], color="black", linewidth=1.0
        )
    for row in table.select(["kind", "cx", "cy", "radius"]).to_pylist():
        if (
            row["kind"] == str(Kind.CIRCLE)
            and row["radius"]
            and (
                x0 - row["radius"] <= row["cx"] <= x1 + row["radius"]
                and y0 - row["radius"] <= row["cy"] <= y1 + row["radius"]
            )
        ):
            axes.add_patch(
                Circle(
                    (row["cx"], row["cy"]), row["radius"], fill=False, color="black", linewidth=1.0
                )
            )
    for span in texts(table):
        if span["text"] and x0 <= span["minx"] <= x1 and y0 <= span["miny"] <= y1:
            size = max(float(span["maxy"] - span["miny"]), 1.0) * PIXELS_PER_MM * 0.72
            axes.text(span["minx"], span["maxy"], span["text"], fontsize=size, va="baseline")
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", facecolor="white")
    plt.close(figure)
    return buffer.getvalue()
