"""End-to-end pipeline benchmark on the running stack (NFR-01; P1-11, ADR-010).

    uv run --project backend python scripts/pipeline_benchmark.py --pdf <file> [--label real-subset]
    uv run --project backend python scripts/pipeline_benchmark.py --synthetic 20

Uploads a drawing set to a new bid through the API (as the dev senior estimator), waits
until the document is read, classified and detected (`done`), and reports:

- the wall time, seconds per sheet, and the time 300 sheets would take at that rate;
- where the sheets' time went, stage by stage, summed over every `parse.sheet` job (from
  their `sheet_parsed` events in the parser pool's structured logs);
- how long the document and finish jobs took.

Writes `eval/results/bench/ingest.json` (or `--out`), which `firebid-eval exit` reads.

NFR-01: a 300-sheet set ingested and classified within 1 hour; first-pass QTO for 50 fire
protection sheets within 4 hours.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
API = "http://localhost:8000"
KEYCLOAK = "http://localhost:8081/realms/firebid/protocol/openid-connect/token"
TARGET_300_SHEETS_S = 3600
IN_FLIGHT = ("received", "processing")
LASTED = re.compile(r"Job (parse\.\w+)\[\d+\]\((.*)\) ended with status: (\w+), lasted ([\d.]+) s")


def client() -> httpx.Client:
    token = httpx.post(
        KEYCLOAK,
        data={
            "grant_type": "password",
            "client_id": "firebid-dev-tests",
            "username": "senior.estimator@firebid.test",
            "password": "firebid-dev",
        },
    ).json()["access_token"]
    return httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"}, timeout=900)


def synthetic_pdf(sheets: int) -> bytes:
    """A multi-page set of synthetic general arrangements, each its own sheet number."""
    import tempfile

    import pypdfium2 as pdfium
    from firebid.evals import synthetic

    out = pdfium.PdfDocument.new()
    with tempfile.TemporaryDirectory() as folder:
        for index in range(sheets):
            number = f"FP-L{index % 40:02d}-{200 + index}"
            document, _ = synthetic.general_arrangement(sheet_number=number)
            path = synthetic.write_pdf(document, Path(folder) / f"{index}.pdf", live_text=True)
            page = pdfium.PdfDocument(str(path))
            out.import_pages(page)
            page.close()
        buffer = io.BytesIO()
        out.save(buffer)
    return buffer.getvalue()


def pool_events(since: str) -> list[dict[str, Any]]:
    """The parser pool's structured log events since `since`."""
    logs = subprocess.run(  # noqa: S603 - fixed arguments, a developer's own stack
        [  # noqa: S607 - docker from PATH, as the Makefile runs it
            "docker",
            "compose",
            "-f",
            str(ROOT / "infra/docker-compose.yml"),
            "logs",
            "sandbox",
            "--since",
            since,
            "--no-log-prefix",
        ],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.splitlines()
    events = []
    for line in logs:
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def breakdown(
    events: list[dict[str, Any]], document_id: str, sheet_ids: set[str]
) -> dict[str, Any]:
    """Per-stage seconds summed over this document's sheet jobs, and each job kind's time."""
    stages: dict[str, float] = defaultdict(float)
    for event in events:
        if event.get("event") == "sheet_parsed" and event.get("sheet_id") in sheet_ids:
            for key, value in event.items():
                if key.endswith("_s") and isinstance(value, int | float):
                    stages[key.removesuffix("_s")] += float(value)
    jobs: dict[str, list[float]] = defaultdict(list)
    for event in events:
        found = LASTED.search(str(event.get("event", "")))
        if not found:
            continue
        task, args, _, lasted = found.groups()
        if document_id in args or any(sheet in args for sheet in sheet_ids):
            jobs[task].append(float(lasted))
    return {
        "sheet_stages_seconds": {
            stage: round(value, 1) for stage, value in sorted(stages.items(), key=lambda kv: -kv[1])
        },
        "jobs": {
            task: {
                "count": len(times),
                "total_seconds": round(sum(times), 1),
                "longest_seconds": round(max(times), 1),
            }
            for task, times in sorted(jobs.items())
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pdf", type=Path)
    source.add_argument("--synthetic", type=int)
    parser.add_argument("--label", default=None)
    parser.add_argument("--out", type=Path, default=ROOT / "eval/results/bench/ingest.json")
    arguments = parser.parse_args()

    payload = arguments.pdf.read_bytes() if arguments.pdf else synthetic_pdf(arguments.synthetic)
    label = arguments.label or (
        arguments.pdf.stem if arguments.pdf else f"synthetic-{arguments.synthetic}"
    )
    api = client()
    bid = api.post(
        "/bids",
        json={
            "project_name": f"Pipeline benchmark {label} {dt.datetime.now(dt.UTC):%Y%m%d%H%M}",
            "consultant": f"Benchmark consultant {label}",
            "client_name": "Benchmark",
            "tender_reference": f"BENCH/{int(time.time())}",
            "submission_deadline": (dt.datetime.now(dt.UTC) + dt.timedelta(days=30)).isoformat(),
        },
    ).json()["id"]
    since = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    started = time.monotonic()
    stored = api.post(
        f"/bids/{bid}/documents", files={"files": (f"{label}.pdf", payload, "application/pdf")}
    ).json()
    # A large file can outlast the scanner's timeout: it is held, not lost (guardrail 9), and
    # released by a rescan, as an estimator would retry it.
    rescans = 0
    while not stored.get("stored") and stored.get("awaiting_scan") and rescans < 10:
        rescans += 1
        time.sleep(20)
        if api.post(f"/bids/{bid}/documents/rescan").json().get("moved"):
            stored = {"stored": api.get(f"/bids/{bid}/documents").json()}
    if not stored.get("stored"):
        print("not stored:", {k: v for k, v in stored.items() if v}, file=sys.stderr)
        return 1
    document = stored["stored"][0]["id"]
    # `done` only once every sheet is read and the set is detected and classified (ADR-010).
    last_report = 0.0
    while True:
        state = api.get(f"/bids/{bid}/documents").json()[0]["state"]
        if state not in IN_FLIGHT:
            break
        if time.monotonic() - last_report > 60:
            progress = api.get(f"/bids/{bid}/progress").json()
            print(
                f"  {(time.monotonic() - started) / 60:5.1f} min: "
                f"{progress.get('sheets_parsed', 0)} of {progress.get('sheets', 0)} sheets read",
                flush=True,
            )
            last_report = time.monotonic()
        time.sleep(5)
    seconds = time.monotonic() - started
    sheet_rows = api.get(f"/bids/{bid}/sheets").json()
    sheets = len(sheet_rows)
    per_sheet = seconds / sheets if sheets else None
    projected = per_sheet * 300 if per_sheet else None
    result: dict[str, Any] = {
        "label": label,
        "sheets": sheets,
        "document_state": state,
        "seconds": round(seconds, 1),
        "seconds_per_sheet": round(per_sheet, 1) if per_sheet else None,
        "projected_300_sheets_minutes": round(projected / 60, 1) if projected else None,
        "meets_nfr01_ingest": bool(projected and projected <= TARGET_300_SHEETS_S),
        **breakdown(pool_events(since), document, {str(row["id"]) for row in sheet_rows}),
        "measured_at": dt.datetime.now(dt.UTC).isoformat(),
        "scan_retries": rescans,
        "environment": "local docker compose: one sandbox container, 2 CPU, 4 GiB, 2 jobs at once",
    }
    result["summary"] = (
        f"{label}: {sheets} sheets in {seconds / 60:.1f} min "
        f"({result['seconds_per_sheet']} s a sheet); "
        f"300 sheets projected at {result['projected_300_sheets_minutes']} min "
        f"({'meets' if result['meets_nfr01_ingest'] else 'MISSES'} the 60 min of NFR-01)"
    )
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(json.dumps(result, indent=1), encoding="utf-8", newline="\n")
    print(result["summary"])
    print(json.dumps({k: result[k] for k in ("sheet_stages_seconds", "jobs")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
