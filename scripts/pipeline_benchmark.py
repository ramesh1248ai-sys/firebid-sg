"""End-to-end pipeline benchmark on the running stack (NFR-01; P1-11, ADR-010).

    uv run --project backend python scripts/pipeline_benchmark.py --pdf <file> [--label real-subset]
    uv run --project backend python scripts/pipeline_benchmark.py --synthetic 20

Uploads a drawing set to a new bid through the API (as the dev senior estimator), waits
until the document is read, classified and detected (`done`), and reports:

- the wall time, seconds per sheet, and the time 300 sheets would take at that rate;
- where the sheets' time went, stage by stage, summed over every `parse.sheet` job (from
  their `sheet_parsed` events in the parser pool's structured logs);
- how long the document and finish jobs took;
- when the first-pass takeoff (`qto.recompute`, queued by the finish job) was done, and the
  time 50 sheets would take at that rate.

Writes `eval/results/bench/ingest.json` (or `--out`) and `eval/results/bench/qto.json` (or
`--qto-out`), which `firebid-eval exit` reads.

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
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
API = "http://localhost:8000"
KEYCLOAK = "http://localhost:8081/realms/firebid/protocol/openid-connect/token"
TARGET_300_SHEETS_S = 3600
TARGET_50_SHEET_QTO_S = 4 * 3600
IN_FLIGHT = ("received", "processing")
LASTED = re.compile(r"Job (parse\.\w+)\[\d+\]\((.*)\) ended with status: (\w+), lasted ([\d.]+) s")


class DevLogin(httpx.Auth):
    """A dev account's bearer token, fetched again when it expires: a benchmark outlasts one."""

    def __init__(self, username: str) -> None:
        self.username = username
        self.token = self.fetch()

    def fetch(self) -> str:
        return str(
            httpx.post(
                KEYCLOAK,
                data={
                    "grant_type": "password",
                    "client_id": "firebid-dev-tests",
                    "username": self.username,
                    "password": "firebid-dev",
                },
            ).json()["access_token"]
        )

    def auth_flow(self, request: httpx.Request) -> Any:
        request.headers["Authorization"] = f"Bearer {self.token}"
        response = yield request
        if response.status_code == 401:
            self.token = self.fetch()
            request.headers["Authorization"] = f"Bearer {self.token}"
            yield request


def client(username: str = "senior.estimator@firebid.test", timeout: float = 900) -> httpx.Client:
    return httpx.Client(base_url=API, auth=DevLogin(username), timeout=timeout)


def synthetic_pdf(sheets: int) -> bytes:
    """A multi-page set of fire protection layouts, each its own sheet number.

    Each is the takeoff fixture's general arrangement (`synthetic_qto`), with its legend, so
    the set is not only read but detected and taken off: a first-pass takeoff with nothing
    in it would time nothing.
    """
    import tempfile

    import pypdfium2 as pdfium
    from firebid.evals import synthetic, synthetic_qto

    out = pdfium.PdfDocument.new()
    with tempfile.TemporaryDirectory() as folder:
        for index in range(sheets):
            number = f"FP-L{index % 40:02d}-{200 + index}"
            document, _ = synthetic_qto.general_arrangement(number)
            path = synthetic.write_pdf(document, Path(folder) / f"{index}.pdf", live_text=True)
            page = pdfium.PdfDocument(str(path))
            out.import_pages(page)
            page.close()
        buffer = io.BytesIO()
        out.save(buffer)
    return buffer.getvalue()


def compose(*arguments: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed arguments, a developer's own stack
        [  # noqa: S607 - docker from PATH, as the Makefile runs it
            "docker",
            "compose",
            "-f",
            str(ROOT / "infra/docker-compose.yml"),
            *arguments,
        ],
        capture_output=True,
        text=True,
        check=False,
    ).stdout


def psql(query: str) -> list[list[str]]:
    """Rows from the local database, as text (a developer's own stack)."""
    out = compose(
        "exec", "-T", "postgres", "psql", "-U", "firebid", "-d", "firebid", "-tAF|", "-c", query
    )
    return [row.split("|") for row in out.splitlines() if row]


def bid_jobs(bid: str) -> dict[str, int]:
    """The bid's own queued jobs, as "task status" to count (the job queue has no API)."""
    rows = psql(
        "select task_name || ' ' || status, count(*) from procrastinate_jobs "  # noqa: S608 - a parsed UUID
        f"where args->>'bid_id' = '{uuid.UUID(bid)}' group by 1"
    )
    return {key: int(count) for key, count in rows}


def wait_for_takeoff(bid: str, limit_s: float = TARGET_50_SHEET_QTO_S) -> bool:
    """True once a takeoff for the bid has succeeded and none of its jobs is still queued."""
    deadline = time.monotonic() + limit_s
    while time.monotonic() < deadline:
        jobs = bid_jobs(bid)
        busy = any(key.endswith((" todo", " doing")) for key in jobs)
        if jobs.get("qto.recompute succeeded") and not busy:
            return True
        time.sleep(2)
    return False


def confirm_legend(api: httpx.Client, bid: str) -> int:
    """Confirm each legend row the rules typed, as the estimator does before a takeoff (the
    pilot runbook's first step); rows the rules could not type are left, as a person would
    have to decide them. Returns how many mappings were confirmed."""
    lineages = {
        row["mapping"]["lineage_id"]
        for row in api.get(f"/bids/{bid}/symbols/legend").json()
        if row["mapping"]
        and row["mapping"]["state"] == "proposed"
        and row["mapping"]["object_type"]
    }
    for lineage in lineages:
        api.post(f"/bids/{bid}/symbols/mappings/{lineage}/confirm", json={}).raise_for_status()
    return len(lineages)


def pool_events(since: str) -> list[dict[str, Any]]:
    """The parser pool's structured log events since `since`."""
    logs = compose("logs", "sandbox", "--since", since, "--no-log-prefix").splitlines()
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
    parser.add_argument("--qto-out", type=Path, default=ROOT / "eval/results/bench/qto.json")
    parser.add_argument(
        "--ingest-only",
        action="store_true",
        help="stop once the set is read: for a real tender, whose legend a person confirms",
    )
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
    # The machine's time to a first-pass takeoff: reading the set, then detecting and taking
    # off once the legend is confirmed. The person's minutes on the legend are not counted.
    takeoff = state == "done" and not arguments.ingest_only
    confirmed = confirm_legend(api, bid) if takeoff else 0
    confirmed_at = time.monotonic()
    took_off = takeoff and wait_for_takeoff(bid)
    qto_seconds = seconds + time.monotonic() - confirmed_at
    sheet_rows = api.get(f"/bids/{bid}/sheets").json()
    sheets = len(sheet_rows)
    per_sheet = seconds / sheets if sheets else None
    projected = per_sheet * 300 if per_sheet else None
    environment = "local docker compose: one sandbox container, 2 CPU, 4 GiB, 2 jobs at once"
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
        "environment": environment,
    }
    result["summary"] = (
        f"{label}: {sheets} sheets in {seconds / 60:.1f} min "
        f"({result['seconds_per_sheet']} s a sheet); "
        f"300 sheets projected at {result['projected_300_sheets_minutes']} min "
        f"({'meets' if result['meets_nfr01_ingest'] else 'MISSES'} the 60 min of NFR-01)"
    )
    items = len(api.get(f"/bids/{bid}/qto/items").json()) if took_off else 0
    projected_qto = qto_seconds / sheets * 50 if took_off and sheets else None
    qto: dict[str, Any] = {
        "label": label,
        "sheets": sheets,
        "took_off": took_off,
        "items": items,
        "legend_mappings_confirmed": confirmed,
        "machine_seconds_to_takeoff": round(qto_seconds, 1),
        "projected_50_sheets_minutes": round(projected_qto / 60, 1) if projected_qto else None,
        # An empty takeoff times nothing: it cannot meet the target.
        "meets_nfr01_qto": bool(items and projected_qto and projected_qto <= TARGET_50_SHEET_QTO_S),
        "measured_at": result["measured_at"],
        "environment": environment,
    }
    qto["summary"] = (
        f"{label}: first-pass takeoff of {sheets} sheets ({items} items) "
        f"in {qto_seconds / 60:.1f} min of machine time (legend confirmed by script); "
        "50 sheets projected at "
        f"{qto['projected_50_sheets_minutes']} min "
        f"({'meets' if qto['meets_nfr01_qto'] else 'MISSES'} the 240 min of NFR-01)"
        if took_off and items
        else f"{label}: the takeoff found no items, so there was nothing to time"
        if took_off
        else f"{label}: no takeoff within the 240 min of NFR-01 (document {state})"
    )
    written = [(arguments.out, result)]
    if not arguments.ingest_only:
        written.append((arguments.qto_out, qto))
    for path, data in written:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1), encoding="utf-8", newline="\n")
        print(data["summary"])
    print(json.dumps({k: result[k] for k in ("sheet_stages_seconds", "jobs")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
