"""Worker entry point: ``python -m firebid.jobs.worker [--queues parse,default]``.

Tasks are synchronous and may be CPU-heavy, so each process runs one job at a time and
throughput scales by running more worker processes (containers), not threads (ADR-006).

**Queues split the work by trust, not by priority.** Anything that opens a file from outside
the company runs on the `parse` queue, which only the sandbox pool takes from
(`infra/sandbox/Dockerfile`). The ordinary worker takes everything else. Without the split,
a parser would run in the same container as the deadline alerts, which holds the SMTP
credentials and a database connection that can see every bid.
"""

from __future__ import annotations

import argparse

from firebid.jobs.app import app
from firebid.logging import configure_logging
from firebid.settings import get_settings

# The queue every parser of external files runs on, taken only by the sandbox pool.
PARSE_QUEUE = "parse"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a FireBid job worker.")
    parser.add_argument(
        "--queues",
        default="",
        help="comma-separated queues to take work from; the default takes every queue",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    arguments = parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)

    queues = [name.strip() for name in arguments.queues.split(",") if name.strip()] or None
    # The ordinary worker says it is alive from a thread of its own, so the job it is
    # running cannot starve its heartbeat. The parser pool does not: its jobs are counted
    # in the queue depths the ordinary worker logs.
    if queues is None or "default" in queues:
        from firebid.jobs import heartbeat

        heartbeat.start()
    app.run_worker(
        concurrency=settings.worker_concurrency,
        name=settings.worker_name,
        queues=queues,
    )


if __name__ == "__main__":
    main()
