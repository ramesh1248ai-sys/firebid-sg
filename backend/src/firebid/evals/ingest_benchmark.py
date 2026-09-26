"""How long a real tender set takes to become viewable (NFR-01).

NFR-01 gives the whole pipeline a time budget from upload to priced BOQ. Ingestion is the
first slice of it, and it is the slice that is easiest to get wrong quietly: rendering every
zoom level of every sheet, or parsing serially, would spend the entire budget before
classification starts.

So this measures the thing an estimator actually waits for — **time to all sheets
viewable** — plus the two numbers that decide whether it scales: peak memory per worker, and
storage per sheet.

Run it with `make ingest-benchmark SHEETS=300`. It uses the real parsers in the real sandbox,
against generated drawings, and writes a Markdown table for the build log.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path

# A tender set of this size is a large but ordinary commercial job.
DEFAULT_SHEETS = 300


@dataclass
class SheetTiming:
    index: int
    parse_seconds: float
    render_seconds: float
    tiles: int
    tile_bytes: int
    peak_memory_bytes: int


@dataclass
class BenchmarkResult:
    sheets: int
    workers: int
    wall_seconds: float
    generate_seconds: float
    timings: list[SheetTiming] = field(default_factory=list)

    @property
    def total_tiles(self) -> int:
        return sum(timing.tiles for timing in self.timings)

    @property
    def total_tile_bytes(self) -> int:
        return sum(timing.tile_bytes for timing in self.timings)

    @property
    def seconds_per_sheet(self) -> float:
        return self.wall_seconds / max(self.sheets, 1)

    @property
    def peak_memory_bytes(self) -> int:
        return max((timing.peak_memory_bytes for timing in self.timings), default=0)

    @property
    def bytes_per_sheet(self) -> float:
        return self.total_tile_bytes / max(self.sheets, 1)

    def summary(self) -> dict[str, object]:
        parse = [timing.parse_seconds for timing in self.timings]
        render = [timing.render_seconds for timing in self.timings]
        return {
            "sheets": self.sheets,
            "workers": self.workers,
            "wall_seconds": round(self.wall_seconds, 1),
            "seconds_per_sheet": round(self.seconds_per_sheet, 3),
            "sheets_per_minute": round(60 / max(self.seconds_per_sheet, 1e-9), 1),
            "median_parse_seconds": round(statistics.median(parse), 3) if parse else 0.0,
            "median_render_seconds": round(statistics.median(render), 3) if render else 0.0,
            "tiles": self.total_tiles,
            "tiles_per_sheet": round(self.total_tiles / max(self.sheets, 1), 1),
            "peak_memory_mb": round(self.peak_memory_bytes / 1e6, 1),
            "storage_kb_per_sheet": round(self.bytes_per_sheet / 1e3, 1),
            "total_storage_mb": round(self.total_tile_bytes / 1e6, 1),
            "generate_seconds": round(self.generate_seconds, 1),
        }


def _peak_memory_bytes() -> int:
    """This process's high-water mark, where the platform reports one."""
    if sys.platform == "win32":
        return 0
    import resource

    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports kilobytes; macOS reports bytes.
    return usage * 1024 if sys.platform.startswith("linux") else usage


def process_one(payload: bytes, index: int) -> dict[str, object]:
    """Parse and tile one sheet, in this process. The worker pool calls this.

    Deliberately the same code path the queued job uses, minus the database: the point is to
    measure the parsing and rendering, not PostgreSQL.
    """
    from PIL import Image

    from firebid.imaging.pyramid import PRE_RENDERED_MAX_PIXELS, Pyramid, base_pixels
    from firebid.imaging.tiles import pre_render
    from firebid.parsing.pdf import inspect_pdf, render_page

    started = time.perf_counter()
    pages = inspect_pdf(payload)
    parse_seconds = time.perf_counter() - started

    page = pages[0]
    width_px, height_px = base_pixels(float(page["width_mm"]), float(page["height_mm"]))
    pyramid = Pyramid(width_px=width_px, height_px=height_px)

    longest = max(width_px, height_px)
    if longest > PRE_RENDERED_MAX_PIXELS:
        shrink = PRE_RENDERED_MAX_PIXELS / longest
        render_width, render_height = round(width_px * shrink), round(height_px * shrink)
    else:
        render_width, render_height = width_px, height_px

    started = time.perf_counter()
    rendered = render_page(payload, 0, render_width, render_height)
    image = Image.frombytes("RGB", (rendered["width"], rendered["height"]), rendered["pixels"])
    tiles = pre_render(image, pyramid, uuid.uuid4().hex[:32])
    render_seconds = time.perf_counter() - started

    return asdict(
        SheetTiming(
            index=index,
            parse_seconds=parse_seconds,
            render_seconds=render_seconds,
            tiles=len(tiles),
            tile_bytes=sum(len(tile) for tile in tiles.values()),
            peak_memory_bytes=_peak_memory_bytes(),
        )
    )


def generate_set(sheets: int, out_dir: Path, seed: int = 1) -> list[bytes]:
    """A tender set of `sheets` drawings, varied enough that caching cannot flatter the result."""
    from firebid.evals.synthetic import general_arrangement, write_pdf

    out_dir.mkdir(parents=True, exist_ok=True)
    payloads: list[bytes] = []
    for index in range(sheets):
        # Vary the plan so no two sheets are the same bytes; identical sheets would dedupe and
        # the benchmark would measure the cache instead of the renderer.
        columns = 6 + (index % 4)
        rows = 4 + (index % 3)
        document, _ = general_arrangement(columns=columns, rows=rows, revision=f"R{index % 9:02d}")
        payloads.append(write_pdf(document, out_dir / f"sheet-{index:04d}.pdf").read_bytes())
    return payloads


def run(sheets: int = DEFAULT_SHEETS, workers: int | None = None, seed: int = 1) -> BenchmarkResult:
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    # Somewhere to write a few hundred generated PDFs; they are thrown away afterwards.
    configured = os.environ.get("FIREBID_BENCHMARK_DIR", "")
    out_dir = Path(configured) if configured else Path(tempfile.gettempdir()) / "firebid-benchmark"

    started = time.perf_counter()
    payloads = generate_set(sheets, out_dir, seed)
    generate_seconds = time.perf_counter() - started

    result = BenchmarkResult(
        sheets=sheets, workers=workers, wall_seconds=0.0, generate_seconds=generate_seconds
    )

    started = time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(process_one, payload, index): index
            for index, payload in enumerate(payloads)
        }
        for future in as_completed(futures):
            result.timings.append(SheetTiming(**future.result()))  # type: ignore[arg-type]
    result.wall_seconds = time.perf_counter() - started

    return result


def markdown(result: BenchmarkResult) -> str:
    summary = result.summary()
    lines = [
        f"### Ingestion throughput — {summary['sheets']} sheets",
        "",
        f"Measured on {summary['workers']} worker processes.",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| Time to all sheets viewable | {summary['wall_seconds']} s |",
        f"| Per sheet | {summary['seconds_per_sheet']} s ({summary['sheets_per_minute']}/min) |",
        f"| Median parse | {summary['median_parse_seconds']} s |",
        f"| Median render and tile | {summary['median_render_seconds']} s |",
        f"| Peak memory per worker | {summary['peak_memory_mb']} MB |",
        f"| Tiles per sheet | {summary['tiles_per_sheet']} |",
        f"| Storage per sheet | {summary['storage_kb_per_sheet']} kB |",
        f"| Total tile storage | {summary['total_storage_mb']} MB |",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheets", type=int, default=DEFAULT_SHEETS)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--json", type=Path, default=None, help="also write the raw summary here")
    parser.add_argument("--report", type=Path, default=None, help="write the Markdown table here")
    arguments = parser.parse_args(argv)

    result = run(sheets=arguments.sheets, workers=arguments.workers, seed=arguments.seed)
    report = markdown(result)
    print(report)

    if arguments.report:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(report, encoding="utf-8")
    if arguments.json:
        arguments.json.parent.mkdir(parents=True, exist_ok=True)
        arguments.json.write_text(json.dumps(result.summary(), indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
