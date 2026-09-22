"""Worker entry point: ``python -m firebid.jobs.worker``.

Tasks are synchronous and may be CPU-heavy, so each process runs one job at a time and
throughput scales by running more worker processes (containers), not threads (ADR-006).
"""

from firebid.jobs.app import app
from firebid.logging import configure_logging
from firebid.settings import get_settings


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    app.run_worker(concurrency=settings.worker_concurrency, name=settings.worker_name)


if __name__ == "__main__":
    main()
