"""Load test on the running stack: 10 concurrent bids, 20 concurrent users (NFR-01, NFR-02; P1-11).

    uv run --project backend python scripts/load_test.py [--bids 10] [--users 20]

A bid manager opens the bids and adds an estimator, a senior estimator and a design manager
to each. Every bid then receives the synthetic fire protection tender at once (a general
arrangement, an enlarged plan and a riser schematic, as DXF), so the parser pool, the finish
jobs and the takeoffs all run side by side. Once a bid's set is read, its legend is
confirmed, as the estimator's first step.

While they run, the users work the workbench as a person would: a user is on one bid, reads
its progress, sheets, takeoff, overlay and review coverage, and sends a time-on-task
heartbeat once a minute, pausing a second or two between actions. Each user signs in on
their own, as the API's rate limit is per session. It ends once every bid's first-pass
takeoff is done.

Reported: the time until every bid was taken off, each request kind's p50, p95 and slowest
time, and the failures. NFR-01 asks workbench interactions under 2 s at p95; NFR-02 asks for
this load without degradation, which is read here as no failed request and the p95 target
kept while ten bids are parsed at once.

The first bid's takeoff is then accepted as its estimator would, so a synthetic shadow
comparison can be run against it (`firebid-eval shadow --synthetic --bid <shadow_bid>`).

Writes `eval/results/bench/load.json` (or `--out`), which `firebid-eval exit` reads.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import random
import statistics
import sys
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline_benchmark import API, IN_FLIGHT, ROOT, DevLogin, bid_jobs, confirm_legend, psql

TARGET_P95_S = 2.0
TAKEOFF_LIMIT_S = 3600
ACCOUNTS = {
    "bid_manager": "bid.manager@firebid.test",
    "estimator": "estimator@firebid.test",
    "senior_estimator": "senior.estimator@firebid.test",
    "design_manager": "design.manager@firebid.test",
}
# The users working the bids, by role: mostly estimators, as on a real bid.
WORKING = ("estimator", "senior_estimator", "estimator", "design_manager", "bid_manager")


def user_id(access_token: str) -> str:
    """The application's id for a signed-in account (joined on first use, keyed by `sub`)."""
    payload = access_token.split(".")[1]
    sub = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["sub"]
    rows = psql(f"select id from app_user where external_id = '{uuid.UUID(sub)}'")  # noqa: S608
    if not rows:
        raise SystemExit(f"no application user for {sub}")
    return rows[0][0]


def tender_files() -> list[tuple[str, bytes]]:
    from firebid.evals import synthetic, synthetic_qto

    # The sheets the synthetic manual takeoff covers (`shadow.synthetic_manual_takeoff`).
    drawings = [
        synthetic_qto.general_arrangement(),
        synthetic_qto.enlarged_plan(),
        synthetic_qto.riser_schematic(),
    ]
    names = ("FP-L05-201", "FP-L05-301", "FP-SCH-001")
    return [
        (f"{name}.dxf", synthetic.dxf_bytes(document))
        for name, (document, _) in zip(names, drawings, strict=True)
    ]


class Recorder:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.times: dict[str, list[float]] = defaultdict(list)
        self.failures: dict[str, int] = defaultdict(int)
        self.examples: list[str] = []

    def call(self, api: httpx.Client, kind: str, method: str, path: str, **kw: Any) -> Any:
        started = time.monotonic()
        try:
            response = api.request(method, path, **kw)
            failed = response.status_code >= 400
            detail = f"{kind} {response.status_code} {response.text[:160]}"
        except httpx.HTTPError as error:
            response, failed, detail = None, True, f"{kind} {type(error).__name__}"
        elapsed = time.monotonic() - started
        with self.lock:
            self.times[kind].append(elapsed)
            if failed:
                self.failures[kind] += 1
                if len(self.examples) < 10:
                    self.examples.append(detail)
        return response


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bids", type=int, default=10)
    parser.add_argument("--users", type=int, default=20)
    parser.add_argument("--out", type=Path, default=ROOT / "eval/results/bench/load.json")
    arguments = parser.parse_args()

    logins = {role: DevLogin(username) for role, username in ACCOUNTS.items()}
    clients = {
        role: httpx.Client(base_url=API, auth=login, timeout=120) for role, login in logins.items()
    }
    for api in clients.values():
        api.get("/bids").raise_for_status()  # joins the account on first use
    ids = {role: user_id(login.token) for role, login in logins.items()}

    manager = clients["bid_manager"]
    stamp = f"{dt.datetime.now(dt.UTC):%Y%m%d%H%M%S}"
    bids = []
    for index in range(arguments.bids):
        bid = manager.post(
            "/bids",
            json={
                "project_name": f"Load test {stamp} bid {index + 1}",
                "consultant": "ALPHA CONSULTANTS PTE LTD",
                "client_name": "Load test",
                "tender_reference": f"LOAD/{stamp}/{index + 1:02d}",
                "submission_deadline": (
                    dt.datetime.now(dt.UTC) + dt.timedelta(days=30)
                ).isoformat(),
            },
        )
        bid.raise_for_status()
        bid_id = bid.json()["id"]
        for role in ("estimator", "senior_estimator", "design_manager"):
            manager.post(
                f"/bids/{bid_id}/members", json={"user_id": ids[role], "role": role}
            ).raise_for_status()
        bids.append(bid_id)

    files = tender_files()
    recorder = Recorder()
    done: dict[str, float] = {}
    started = time.monotonic()

    def upload(bid_id: str) -> None:
        recorder.call(
            clients["estimator"],
            "upload tender set",
            "POST",
            f"/bids/{bid_id}/documents",
            files=[("files", (name, data, "application/dxf")) for name, data in files],
        )

    with ThreadPoolExecutor(len(bids)) as pool:
        list(pool.map(upload, bids))

    stop = threading.Event()

    confirmed: set[str] = set()

    def read(bid_id: str) -> bool:
        response = clients["senior_estimator"].get(f"/bids/{bid_id}/documents")
        if not response.is_success:
            return False  # asked again on the next pass
        documents = response.json()
        return bool(documents) and all(d["state"] not in IN_FLIGHT for d in documents)

    def watch() -> None:
        # The users stop when this does, however it ends: a watcher that died silently would
        # leave them working for ever.
        try:
            while time.monotonic() - started < TAKEOFF_LIMIT_S:
                for bid_id in bids:
                    if bid_id in done:
                        continue
                    if bid_id not in confirmed:
                        # The estimator's first step once a set is read: confirm the legend.
                        if read(bid_id):
                            confirm_legend(clients["senior_estimator"], bid_id)
                            confirmed.add(bid_id)
                        continue
                    jobs = bid_jobs(bid_id)
                    busy = any(key.endswith((" todo", " doing")) for key in jobs)
                    if jobs.get("qto.recompute succeeded") and not busy:
                        done[bid_id] = time.monotonic() - started
                if len(done) == len(bids):
                    break
                time.sleep(3)
        finally:
            stop.set()

    def work(number: int) -> None:
        # Each user is their own session, with their own token, as twenty people in twenty
        # browsers are: the API's rate limit is per token.
        role = WORKING[number % len(WORKING)]
        api = httpx.Client(base_url=API, auth=DevLogin(ACCOUNTS[role]), timeout=120)
        bid_id = bids[number % len(bids)]
        chance = random.Random(number)  # noqa: S311 - pacing, not security
        actions = [
            ("progress", "GET", f"/bids/{bid_id}/progress"),
            ("bid", "GET", f"/bids/{bid_id}"),
            ("sheets", "GET", f"/bids/{bid_id}/sheets"),
            ("takeoff items", "GET", f"/bids/{bid_id}/qto/items"),
            ("workbench sheets", "GET", f"/bids/{bid_id}/qto/sheets"),
            ("overlay", "GET", f"/bids/{bid_id}/qto/overlay"),
            ("review coverage", "GET", f"/bids/{bid_id}/review/coverage"),
        ]
        sheet_ids: list[str] = []
        last_beat = 0.0
        while not stop.is_set():
            kind, method, path = chance.choice(actions)
            if kind == "overlay":
                if not sheet_ids:
                    continue  # nothing to draw over until a sheet is read
                path += f"?sheet_id={chance.choice(sheet_ids)}"
            response = recorder.call(api, kind, method, path)
            if kind == "workbench sheets" and response is not None and response.is_success:
                sheet_ids = [sheet["sheet_id"] for sheet in response.json()]
            # The workbench's heartbeat: once a minute while the person is working.
            if role in ("estimator", "senior_estimator") and time.monotonic() - last_beat >= 60:
                recorder.call(
                    api,
                    "time-on-task heartbeat",
                    "POST",
                    f"/bids/{bid_id}/review/activity",
                    json={"area": "review"},
                )
                last_beat = time.monotonic()
            stop.wait(chance.uniform(1.0, 2.0))
        api.close()

    watcher = threading.Thread(target=watch)
    watcher.start()
    with ThreadPoolExecutor(arguments.users) as pool:
        list(pool.map(work, range(arguments.users)))
    watcher.join()
    seconds = time.monotonic() - started

    # The first bid's takeoff, accepted as its estimator would, for the shadow comparison.
    shadow_bid = bids[0]
    estimator = clients["estimator"]
    open_items = [
        item["id"]
        for item in estimator.get(f"/bids/{shadow_bid}/qto/items").json()
        if item["state"] in ("detected", "proposed", "edited")
    ]
    if open_items:
        estimator.post(
            f"/bids/{shadow_bid}/review/accept",
            json={"item_ids": open_items, "note": "load test: accepted for the shadow comparison"},
        ).raise_for_status()

    workbench = [
        value
        for kind, values in recorder.times.items()
        if kind != "upload tender set"
        for value in values
    ]
    requests = sum(len(values) for values in recorder.times.values())
    failed = sum(recorder.failures.values())
    p95 = percentile(workbench, 0.95) if workbench else None
    all_done = len(done) == len(bids)
    meets = all_done and failed == 0 and p95 is not None and p95 <= TARGET_P95_S
    result: dict[str, Any] = {
        "bids": len(bids),
        "users": arguments.users,
        "sheets_per_bid": len(files),
        "bids_taken_off": len(done),
        "seconds": round(seconds, 1),
        "slowest_bid_takeoff_seconds": round(max(done.values()), 1) if done else None,
        "requests": requests,
        "failed_requests": failed,
        "failure_examples": recorder.examples,
        "workbench_p50_seconds": round(statistics.median(workbench), 3) if workbench else None,
        "workbench_p95_seconds": round(p95, 3) if p95 is not None else None,
        "by_request": {
            kind: {
                "count": len(values),
                "failed": recorder.failures.get(kind, 0),
                "p50_seconds": round(statistics.median(values), 3),
                "p95_seconds": round(percentile(values, 0.95), 3),
                "slowest_seconds": round(max(values), 3),
            }
            for kind, values in sorted(recorder.times.items())
        },
        "meets_nfr02": meets,
        "shadow_bid": shadow_bid,
        "shadow_items_accepted": len(open_items),
        "measured_at": dt.datetime.now(dt.UTC).isoformat(),
        "environment": "local docker compose: one API, one worker, one sandbox (2 CPU, 2 jobs)",
    }
    result["summary"] = (
        f"{len(bids)} bids and {arguments.users} users: {len(done)} of {len(bids)} bids taken "
        f"off in {seconds / 60:.1f} min; {requests} requests, {failed} failed; workbench p95 "
        f"{result['workbench_p95_seconds']} s ({'meets' if meets else 'MISSES'} NFR-02 and "
        f"the 2 s p95 of NFR-01)"
    )
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(json.dumps(result, indent=1), encoding="utf-8", newline="\n")
    print(result["summary"])
    print(json.dumps(result["by_request"], indent=1))
    print("shadow bid:", shadow_bid)
    return 0 if meets else 1


if __name__ == "__main__":
    sys.exit(main())
