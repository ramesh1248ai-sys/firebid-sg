"""The restore drill (NFR-04: RPO 24 h, RTO 8 h): back up, lose, restore, verify, and time it.

Locally (`--local`) the drill runs against the docker-compose database: a custom-format
`pg_dump` taken inside the Postgres container, restored into a fresh database beside the
original, then verified: every table's row count, and every audit hash chain, must match.
The timings (backup, restore, verification) are the recovery time for this data size.

In staging the same verification runs against a Cloud SQL point-in-time clone
(`gcloud sql instances clone --point-in-time`); the runbook `docs/runbooks/restore.md`
gives the commands. Point-in-time recovery bounds the RPO by the transaction log, minutes
rather than the 24 hours allowed.
"""

from __future__ import annotations

import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, func, select, table, text
from sqlalchemy.orm import Session

RPO_HOURS = 24
RTO_HOURS = 8
STACK = Path(__file__).resolve().parents[4] / "infra" / "docker-compose.yml"
COMPOSE = ["docker", "compose", "-f", str(STACK), "exec", "-T", "postgres"]
# Written by the running stack every minute (job queue, heartbeat, rate windows): they change
# between the dump and the count, so they are not compared. Business data is.
# Inside the Postgres container, in the postgres user's own directory.
DUMP = "/var/lib/postgresql/drill.dump"
VOLATILE = ("procrastinate_", "system_heartbeat", "llm_rate_bucket")


@dataclass
class Drill:
    environment: str
    ran_at: str
    database_mb: float = 0.0
    backup_seconds: float = 0.0
    restore_seconds: float = 0.0
    verify_seconds: float = 0.0
    backup_age_seconds: float = 0.0
    tables_checked: int = 0
    mismatched_tables: list[str] = field(default_factory=list)
    chains_checked: int = 0
    broken_chains: list[str] = field(default_factory=list)

    @property
    def rto_seconds(self) -> float:
        return self.restore_seconds + self.verify_seconds

    @property
    def passed(self) -> bool:
        return (
            not self.mismatched_tables
            and not self.broken_chains
            and self.rto_seconds <= RTO_HOURS * 3600
            and self.backup_age_seconds <= RPO_HOURS * 3600
        )

    def to_json(self) -> dict[str, Any]:
        out = asdict(self)
        out.update(
            rto_seconds=round(self.rto_seconds, 1),
            passed=self.passed,
            summary=(
                f"{self.environment}: {self.database_mb:.0f} MB restored and verified in "
                f"{self.rto_seconds / 60:.1f} min (RTO target {RTO_HOURS} h); data lost "
                f"{self.backup_age_seconds / 60:.1f} min back (RPO target {RPO_HOURS} h); "
                f"{self.tables_checked} tables and {self.chains_checked} audit chains match"
                if self.passed
                else f"{self.environment}: FAILED ({len(self.mismatched_tables)} tables, "
                f"{len(self.broken_chains)} chains differ)"
            ),
        )
        return out


def _run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(command, check=True, capture_output=True, **kwargs)  # noqa: S603


def _counts(url: str) -> dict[str, int]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            tables = connection.execute(
                text(
                    "SELECT c.relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') "
                    "AND NOT c.relispartition "
                    "ORDER BY 1"
                )
            ).scalars()
            return {
                # `table()` quotes the name as an identifier: no SQL is assembled from text.
                name: int(
                    connection.execute(select(func.count()).select_from(table(name))).scalar_one()
                )
                for name in tables
            }
    finally:
        engine.dispose()


def _chains(url: str) -> tuple[int, list[str]]:
    from firebid.db.audit import verify_chain

    engine = create_engine(url)
    try:
        with Session(engine) as session:
            keys = session.execute(
                text("SELECT DISTINCT chain_key FROM audit_chain_link")
            ).scalars()
            broken = [str(key) for key in list(keys) if verify_chain(session, uuid.UUID(str(key)))]
            total = int(
                session.execute(
                    text("SELECT count(DISTINCT chain_key) FROM audit_chain_link")
                ).scalar_one()
            )
            return total, broken
    finally:
        engine.dispose()


def verify(drill: Drill, original_url: str, restored_url: str) -> Drill:
    """Every table's rows and every audit hash chain of the restored database, against the
    original, timed. Queue and heartbeat tables change by the minute and are left out."""
    started = time.monotonic()
    original = _counts(original_url)
    restored = _counts(restored_url)
    compared = [name for name in original if not name.startswith(VOLATILE)]
    drill.tables_checked = len(compared)
    drill.mismatched_tables = sorted(
        name for name in compared if original[name] != restored.get(name)
    )
    drill.chains_checked, drill.broken_chains = _chains(restored_url)
    drill.verify_seconds = time.monotonic() - started
    return drill


def compare(
    original_url: str,
    restored_url: str,
    *,
    environment: str,
    restore_seconds: float,
    data_lost_seconds: float,
) -> Drill:
    """The drill's verification for a restore done elsewhere: a Cloud SQL point-in-time
    clone in staging (docs/runbooks/restore.md). Run it while the original is quiet (the
    workers scaled to zero), or its rows will have moved on since the recovery point."""
    drill = Drill(
        environment=environment,
        ran_at=datetime.now(UTC).isoformat(),
        restore_seconds=restore_seconds,
        backup_age_seconds=data_lost_seconds,
    )
    return verify(drill, original_url, restored_url)


def local(host_url: str = "postgresql+psycopg://firebid:firebid@localhost:55432") -> Drill:
    """The drill on the docker-compose stack. Leaves the original database untouched."""
    drill = Drill(
        environment="local rehearsal (docker compose)", ran_at=datetime.now(UTC).isoformat()
    )
    size = _run(
        [
            *COMPOSE,
            "psql",
            "-U",
            "firebid",
            "-d",
            "firebid",
            "-tAc",
            "SELECT pg_database_size('firebid')",
        ]
    )
    drill.database_mb = int(size.stdout.decode().strip()) / 1_048_576

    started = time.monotonic()
    _run([*COMPOSE, "pg_dump", "-U", "firebid", "-d", "firebid", "-Fc", "-f", DUMP])
    drill.backup_seconds = time.monotonic() - started
    backup_taken = time.monotonic()

    # The incident: the database is lost. Recovery restores the last backup into a new one.
    _run(
        [
            *COMPOSE,
            "psql",
            "-U",
            "firebid",
            "-d",
            "postgres",
            "-c",
            "DROP DATABASE IF EXISTS firebid_restore",
        ]
    )
    started = time.monotonic()
    drill.backup_age_seconds = started - backup_taken
    _run([*COMPOSE, "createdb", "-U", "firebid", "firebid_restore"])
    _run(
        [
            *COMPOSE,
            "pg_restore",
            "-U",
            "firebid",
            "-d",
            "firebid_restore",
            "--no-owner",
            "--exit-on-error",
            DUMP,
        ]
    )
    drill.restore_seconds = time.monotonic() - started

    verify(drill, f"{host_url}/firebid", f"{host_url}/firebid_restore")

    _run(
        [
            *COMPOSE,
            "psql",
            "-U",
            "firebid",
            "-d",
            "postgres",
            "-c",
            "DROP DATABASE IF EXISTS firebid_restore",
        ]
    )
    _run([*COMPOSE, "rm", "-f", DUMP])
    return drill
