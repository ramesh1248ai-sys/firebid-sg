"""Container health check for job workers: ``python -m firebid.jobs.healthcheck``.

Healthy when the database is reachable and the heartbeat job ran recently, which proves a
worker is taking jobs off the queue. Exit code 0 = healthy, 1 = unhealthy.
"""

import sys

from firebid.api.checks import database_check, job_queue_check
from firebid.settings import get_settings


def main() -> int:
    try:
        ok = database_check().ok and job_queue_check(get_settings().heartbeat_max_age_seconds)().ok
    except Exception:
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
