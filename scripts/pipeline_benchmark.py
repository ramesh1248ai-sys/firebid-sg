"""End-to-end pipeline benchmark on the running stack (NFR-01; P1-11).

    uv run --project backend python scripts/pipeline_benchmark.py --pdf <file> [--label real-subset]
    uv run --project backend python scripts/pipeline_benchmark.py --synthetic 20

Uploads a drawing set to a new bid through the API (as the dev senior estimator), waits for
the document to be read, classified and detected, and reports the wall time, seconds per
sheet, the time 300 sheets would take at that rate, and where the time went by stage (from
the parser pool's structured logs). Writes `eval/results/bench/ingest.json` (or
`--out`), which `firebid-eval exit` reads.

NFR-01: a 300-sheet set ingested and classified within 1 hour; first-pass QTO for 50 fire
protection sheets within 4 hours.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
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
TARGET_50_SHEET_QTO_S = 4 * 3600
STAGES = {
    "document_registered": "register",
    "sheet_rendered": "render",
    "tiles_written": "render",
    "title_block_read": "title blocks",
    "title_block_rendition": "title blocks",
    "geometry_extracted": "geometry",
    "views_detected": "views",
    "symbols_read": "symbols and legend",
    "sheet_detected": "detection",
    "document_classified": "classification",
}


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


def job_events(since: str, document_id: str) -> tuple[list[dict[str, Any]], bool]:
    """The parser pool's log events for this document's parse job, and whether it ended."""
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
    events: list[dict[str, Any]] = []
    inside = False
    for line in logs:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        name = str(event.get("event", ""))
        if name.startswith("Starting job parse.document") and document_id in name:
            inside = True
        if inside:
            events.append(event)
            if "ended with status" in name and document_id in name:
                return events, True
    return events, False


def stage_times(events: list[dict[str, Any]]) -> dict[str, float]:
    """Seconds spent before each stage's log event, summed by stage."""
    totals: dict[str, float] = defaultdict(float)
    previous = None
    for event in events:
        at = dt.datetime.fromisoformat(str(event["timestamp"]).replace("Z", "+00:00"))
        if previous is not None:
            stage = STAGES.get(str(event.get("event")), "other")
            totals[stage] += (at - previous).total_seconds()
        previous = at
    return {
        stage: round(seconds, 1) for stage, seconds in sorted(totals.items(), key=lambda kv: -kv[1])
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
    # Waits for the parse job, not the document: a drawing is "done" as soon as its sheets
    # exist, before title blocks, symbols and detection are read.
    while True:
        events, ended = job_events(since, document)
        if ended:
            break
        time.sleep(10)
    seconds = time.monotonic() - started
    state = api.get(f"/bids/{bid}/documents").json()[0]["state"]
    sheets = len(api.get(f"/bids/{bid}/sheets").json())
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
        "projected_50_sheet_qto_minutes": round(per_sheet * 50 / 60, 1) if per_sheet else None,
        "stages_seconds": stage_times(events),
        "measured_at": dt.datetime.now(dt.UTC).isoformat(),
        "scan_retries": rescans,
        "environment": "local docker compose (one sandbox worker)",
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
    print(json.dumps(result["stages_seconds"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
