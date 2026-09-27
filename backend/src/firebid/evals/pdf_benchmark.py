"""The PDF extraction benchmark behind ADR-002 (P1-03 item 0).

Three engines extract the same dense sheets: PDFium through pypdfium2 (the platform's
extractor), pdfplumber (its fallback) and PyMuPDF (AGPL: evaluated here, never a dependency;
it is installed into a throwaway directory only for the run). Each engine runs in its own
process, so time and peak memory are its own.

**Completeness** is measured against what the sheet is known to contain, not against another
engine: the share of the drawing's long straight runs recovered at the right length and
place, and the share of its text strings recovered as text. An engine that returns every
segment but splits text into loose glyphs scores lower, which is what matters downstream.

The sheets are synthetic until the D3 golden set arrives: A1 and A0, drawn at 1:100 with an
architectural background, a sprinkler grid, branches and mains, grid lines with bubbles, and
annotation on every branch and head. Real sheets are denser still; see ADR-002.

    python -m firebid.evals.pdf_benchmark generate --out DIR
    python -m firebid.evals.pdf_benchmark run --sheets DIR [--pymupdf DIR]
    python -m firebid.evals.pdf_benchmark throughput --sheets DIR [--count 300] [--workers 4]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PAPER = {"A1": (841.0, 594.0), "A0": (1189.0, 841.0)}
SCALE = 100  # drawn at 1:100
LONG_MM = 20.0  # a run this long on paper counts as line work to recover
ENGINES = ("pypdfium2", "pdfplumber", "pymupdf")


# --- The sheets -----------------------------------------------------------------------------


def dense_sheet(path: Path, seed: int, paper: str = "A1") -> dict[str, Any]:
    """One dense sheet as a vector PDF, and the reference of what is on it."""
    import ezdxf
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.config import BackgroundPolicy, Configuration
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    rng = random.Random(seed)  # noqa: S311  # reproducible fixtures, not cryptography
    width_mm, height_mm = PAPER[paper]
    width, height = width_mm * SCALE, height_mm * SCALE
    document = ezdxf.new(dxfversion="R2010", setup=True)
    space = document.modelspace()
    long_runs: list[tuple[float, float, float, float]] = []
    texts: list[str] = []

    def line(x0: float, y0: float, x1: float, y1: float, layer: str) -> None:
        space.add_line((x0, y0), (x1, y1), dxfattribs={"layer": layer})
        if math.hypot(x1 - x0, y1 - y0) / SCALE >= LONG_MM:
            long_runs.append((x0 / SCALE, (height - y0) / SCALE, x1 / SCALE, (height - y1) / SCALE))

    def text(value: str, x: float, y: float, size: float, layer: str) -> None:
        space.add_text(value, height=size, dxfattribs={"layer": layer}).set_placement((x, y))
        texts.append(value)

    # Border, and structural grid lines with bubbles every 8.4 m.
    for x0, y0, x1, y1 in (
        (0, 0, width, 0),
        (width, 0, width, height),
        (width, height, 0, height),
        (0, height, 0, 0),
    ):
        line(x0, y0, x1, y1, "BORDER")
    bay = 8_400
    for index, grid_x in enumerate(range(bay, int(width) - 20_000, bay)):
        line(grid_x, 3_000, grid_x, height - 3_000, "GRID")
        space.add_circle((grid_x, height - 2_000), 800, dxfattribs={"layer": "GRID"})
        text(chr(ord("A") + index % 26), grid_x - 250, height - 2_250, 500, "GRID")
    for index, grid_y in enumerate(range(bay, int(height) - 3_000, bay)):
        line(3_000, grid_y, width - 20_000, grid_y, "GRID")
        space.add_circle((2_000, grid_y), 800, dxfattribs={"layer": "GRID"})
        text(str(index + 1), 1_750, grid_y - 250, 500, "GRID")

    # Architectural background: rooms of walls and door swings, the bulk of a real sheet.
    for _ in range(int(width * height / 4_000_000)):
        room_w, room_h = rng.uniform(2_000, 7_000), rng.uniform(2_000, 6_000)
        # Wholly on the sheet: a wall running off the page is on no engine's output.
        x = rng.uniform(4_000, width - 24_000 - room_w)
        y = rng.uniform(4_000, height - 4_000 - room_h)
        for x0, y0, x1, y1 in (
            (x, y, x + room_w, y),
            (x + room_w, y, x + room_w, y + room_h),
            (x + room_w, y + room_h, x, y + room_h),
            (x, y + room_h, x, y),
        ):
            line(x0, y0, x1, y1, "A-WALL")
        space.add_arc((x + 200, y), 900, 0, 90, dxfattribs={"layer": "A-DOOR"})

    # Sprinklers on a 3 m grid, branches per row, a main, and a label on each.
    spacing = 3_000
    columns = int((width - 30_000) / spacing)
    rows = int((height - 12_000) / spacing)
    for row in range(rows):
        y = 6_000 + row * spacing
        line(6_000, y, 6_000 + (columns - 1) * spacing, y, "FP-PIPE")
        text(f"DN{rng.choice((25, 32, 40, 50))}", 6_300, y + 200, 180, "FP-TEXT")
        for column in range(columns):
            x = 6_000 + column * spacing
            space.add_circle((x, y), 60, dxfattribs={"layer": "FP-SPRINKLER"})
            if rng.random() < 0.3:
                text(f"SP{row:02d}{column:02d}", x + 150, y + 150, 120, "FP-TEXT")
    line(4_500, 6_000, 4_500, 6_000 + (rows - 1) * spacing, "FP-PIPE")
    text("DN150 WET RISER", 3_000, height / 2, 250, "FP-TEXT")

    # A hatch or two: fills are common and some engines mishandle them.
    for _ in range(12):
        x, y = rng.uniform(5_000, width - 30_000), rng.uniform(5_000, height - 8_000)
        hatch = space.add_hatch(color=8)
        hatch.paths.add_polyline_path(
            [(x, y), (x + 1_500, y), (x + 1_500, y + 900), (x, y + 900)], is_closed=True
        )

    figure = plt.figure(figsize=(width_mm / 25.4, height_mm / 25.4))
    axes = figure.add_axes((0, 0, 1, 1))
    axes.set_axis_off()
    try:
        Frontend(
            RenderContext(document),
            MatplotlibBackend(axes),
            config=Configuration(background_policy=BackgroundPolicy.WHITE),
        ).draw_layout(space, finalize=True, filter_func=lambda entity: entity.dxftype() != "TEXT")
        figure.set_size_inches(width_mm / 25.4, height_mm / 25.4)
        axes.set_aspect("auto")
        axes.set_position((0, 0, 1, 1))
        axes.set_xlim(0, width)
        axes.set_ylim(0, height)
        # Annotation as real text, as a TrueType CAD export writes it.
        for entity in space.query("TEXT"):
            x, y, _ = entity.dxf.insert
            axes.text(
                x,
                y,
                entity.dxf.text,
                ha="left",
                va="baseline",
                fontsize=entity.dxf.height / SCALE * 72 / 25.4 * 1.4,
            )
        with matplotlib.rc_context({"pdf.fonttype": 42}):
            figure.savefig(path, format="pdf")
    finally:
        plt.close(figure)

    reference = {
        "paper": paper,
        "seed": seed,
        "long_runs": long_runs,
        "texts": texts,
        "entities": len(space),
    }
    path.with_suffix(".json").write_text(json.dumps(reference), encoding="utf-8")
    return reference


# --- The engines ------------------------------------------------------------------------------


def _engine_pypdfium2(payload: bytes) -> tuple[list[list[float]], list[str]]:
    from firebid.drawings import geometry
    from firebid.parsing.geometry_pdf import _pdfium

    table = geometry.from_parquet(_pdfium(payload, 0)[0])
    found = geometry.segments(table)
    lines = [
        [a, b, c, d] for a, b, c, d in zip(found.x0, found.y0, found.x1, found.y1, strict=True)
    ]
    return lines, [span["text"] for span in geometry.texts(table)]


def _engine_pdfplumber(payload: bytes) -> tuple[list[list[float]], list[str]]:
    from firebid.drawings import geometry
    from firebid.parsing.geometry_pdf import _pdfplumber

    table = geometry.from_parquet(_pdfplumber(payload, 0))
    found = geometry.segments(table)
    lines = [
        [a, b, c, d] for a, b, c, d in zip(found.x0, found.y0, found.x1, found.y1, strict=True)
    ]
    return lines, [span["text"] for span in geometry.texts(table)]


def _engine_pymupdf(payload: bytes) -> tuple[list[list[float]], list[str]]:
    import pymupdf  # type: ignore[import-not-found]  # evaluation only: AGPL, never installed

    mm = 25.4 / 72
    document = pymupdf.open(stream=payload, filetype="pdf")
    page = document[0]
    lines: list[list[float]] = []
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                start, end = item[1], item[2]
                lines.append([start.x * mm, start.y * mm, end.x * mm, end.y * mm])
            elif item[0] == "re":
                rect = item[1]
                corners = [
                    (rect.x0, rect.y0),
                    (rect.x1, rect.y0),
                    (rect.x1, rect.y1),
                    (rect.x0, rect.y1),
                ]
                for index in range(4):
                    (ax, ay), (bx, by) = corners[index], corners[(index + 1) % 4]
                    lines.append([ax * mm, ay * mm, bx * mm, by * mm])
    words = [block[4].strip() for block in page.get_text("words")]
    spans = [
        " ".join(word for word in line.split())
        for line in page.get_text("text").splitlines()
        if line.strip()
    ]
    return lines, spans + words


def run_engine(engine: str, pdf: Path) -> dict[str, Any]:
    """In a process of its own: extract, and report time, peak memory and completeness."""
    payload = pdf.read_bytes()
    reference = json.loads(pdf.with_suffix(".json").read_text(encoding="utf-8"))
    extract = {
        "pypdfium2": _engine_pypdfium2,
        "pdfplumber": _engine_pdfplumber,
        "pymupdf": _engine_pymupdf,
    }[engine]
    started = time.perf_counter()
    lines, texts = extract(payload)
    seconds = time.perf_counter() - started
    peak_mb = _peak_mb()
    return {
        "engine": engine,
        "sheet": pdf.name,
        "paper": reference["paper"],
        "seconds": round(seconds, 3),
        "peak_mb": None if peak_mb is None else round(peak_mb, 1),
        "segments": len(lines),
        "line_work": round(_line_recall(lines, reference["long_runs"]), 4),
        "text": round(_text_recall(texts, reference["texts"]), 4),
    }


def _peak_mb() -> float | None:
    """This process's peak resident memory. Linux only, which is where the benchmark runs."""
    if sys.platform == "win32":
        return None
    import resource

    return float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / 1024


def _line_recall(lines: list[list[float]], runs: list[list[float]]) -> float:
    """Share of known long runs matched by an extracted segment within 0.5 mm at each end."""
    import numpy as np

    if not runs:
        return 1.0
    found = np.asarray(lines, dtype=float).reshape(-1, 4)
    matched = 0
    for x0, y0, x1, y1 in runs:
        forward = np.hypot(found[:, 0] - x0, found[:, 1] - y0) + np.hypot(
            found[:, 2] - x1, found[:, 3] - y1
        )
        backward = np.hypot(found[:, 0] - x1, found[:, 1] - y1) + np.hypot(
            found[:, 2] - x0, found[:, 3] - y0
        )
        if found.size and min(forward.min(), backward.min()) < 1.0:
            matched += 1
    return matched / len(runs)


def _text_recall(found: list[str], expected: list[str]) -> float:
    """Share of known strings found whole in some extracted span."""
    if not expected:
        return 1.0
    joined = "\n".join(found)
    spans = set(found)
    return sum(1 for value in expected if value in spans or f" {value} " in f" {joined} ") / len(
        expected
    )


# --- Throughput against NFR-01 -------------------------------------------------------------

# NFR-01: a 300-sheet tender is through drawing understanding within an hour on 4 workers.
NFR01_SHEETS, NFR01_SECONDS, NFR01_WORKERS = 300, 3600.0, 4


def _one_sheet(pdf: str) -> float:
    """Geometry and views for one sheet, as the parse job does them, timed."""
    from firebid.drawings import geometry, views
    from firebid.parsing import geometry_pdf

    started = time.perf_counter()
    result = geometry_pdf.extract(Path(pdf).read_bytes(), 0)
    table = geometry.from_parquet(result["parquet"])
    width, height = PAPER["A0"] if "-A0" in pdf else PAPER["A1"]
    views.analyse(table, (0.0, 0.0, width, height), f"1:{SCALE}", None)
    return time.perf_counter() - started


def throughput(sheets: list[Path], count: int, workers: int) -> dict[str, Any]:
    """`count` sheets through `workers` processes, cycling the dense sheets given.

    No stage cache: every sheet is extracted, which is the worst case (a first upload).
    """
    from concurrent.futures import ProcessPoolExecutor

    work = [str(sheets[index % len(sheets)]) for index in range(count)]
    started = time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        seconds = sorted(pool.map(_one_sheet, work))
    wall = time.perf_counter() - started
    projected = wall * (NFR01_SHEETS / count) * (workers / NFR01_WORKERS)
    return {
        "sheets": count,
        "workers": workers,
        "wall_seconds": round(wall, 1),
        "per_sheet_p50": round(seconds[len(seconds) // 2], 2),
        "per_sheet_p95": round(seconds[int(len(seconds) * 0.95) - 1], 2),
        "per_sheet_max": round(seconds[-1], 2),
        "projected_300_on_4_workers_seconds": round(projected, 1),
        "budget_seconds": NFR01_SECONDS,
        "within_budget": projected <= NFR01_SECONDS,
    }


# --- The command line -------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pdf_benchmark")
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate")
    generate.add_argument("--out", type=Path, required=True)
    run = commands.add_parser("run")
    run.add_argument("--sheets", type=Path, required=True)
    run.add_argument("--pymupdf", type=Path, help="a directory PyMuPDF was installed into")
    run.add_argument("--report", type=Path)
    rate = commands.add_parser("throughput")
    rate.add_argument("--sheets", type=Path, required=True)
    rate.add_argument("--count", type=int, default=NFR01_SHEETS)
    rate.add_argument("--workers", type=int, default=NFR01_WORKERS)
    one = commands.add_parser("engine")
    one.add_argument("engine", choices=ENGINES)
    one.add_argument("pdf", type=Path)
    arguments = parser.parse_args(argv)

    if arguments.command == "generate":
        arguments.out.mkdir(parents=True, exist_ok=True)
        plan = [(1, "A1"), (2, "A1"), (3, "A1"), (4, "A0"), (5, "A0")]
        for seed, paper in plan:
            reference = dense_sheet(arguments.out / f"dense-{seed}-{paper}.pdf", seed, paper)
            print(
                f"dense-{seed}-{paper}.pdf: {reference['entities']} entities, "
                f"{len(reference['long_runs'])} long runs, {len(reference['texts'])} texts"
            )
        return 0

    if arguments.command == "throughput":
        found = sorted(arguments.sheets.glob("*.pdf"))
        print(json.dumps(throughput(found, arguments.count, arguments.workers), indent=2))
        return 0

    if arguments.command == "engine":
        print(json.dumps(run_engine(arguments.engine, arguments.pdf)))
        return 0

    results = []
    for pdf in sorted(arguments.sheets.glob("*.pdf")):
        for engine in ENGINES:
            environment = None
            if engine == "pymupdf":
                if arguments.pymupdf is None:
                    continue
                import os

                environment = {**os.environ, "PYTHONPATH": str(arguments.pymupdf)}
            finished = subprocess.run(  # noqa: S603 - our own module, fixed arguments
                [sys.executable, "-m", "firebid.evals.pdf_benchmark", "engine", engine, str(pdf)],
                capture_output=True,
                text=True,
                env=environment,
                check=False,
                timeout=1800,
            )
            if finished.returncode != 0:
                results.append(
                    {
                        "engine": engine,
                        "sheet": pdf.name,
                        "error": finished.stderr.strip().splitlines()[-1:],
                    }
                )
                continue
            results.append(json.loads(finished.stdout.strip().splitlines()[-1]))
            print(json.dumps(results[-1]))
    if arguments.report:
        arguments.report.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
