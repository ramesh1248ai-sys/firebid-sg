"""Retention, archival and audit-chain verification, on a schedule (NFR-07, NFR-09; P1-11).

* **Delete** what the policy (`config/retention.yaml`) keeps only for a while: workbench time
  on task, finished job history, and what has passed its own `expires_at` (AI response cache,
  debugging payloads).
* **Archive** the audit log: each month older than `archive_after_months` is written once to
  object storage as JSON lines, after its chains are verified; the rows stay in the database
  until `retain_years` (never under 7) has passed.
* **Verify** every audit chain (one per bid, one per organisation). A break is logged at
  error level for the alert, and returned for the job's result.

Runs on the service role: retention spans bids by nature.
"""

from __future__ import annotations

import gzip
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any

import structlog
import yaml
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from firebid.db.audit import verify_chain
from firebid.db.models.ai import LlmPayload, LlmResponseCache
from firebid.db.models.audit import AuditChainLink, AuditEvent
from firebid.db.models.review import ActivityMinute

log = structlog.get_logger("firebid.retention")

CONFIG = Path(__file__).resolve().parents[3] / "config" / "retention.yaml"
MIN_AUDIT_YEARS = 7


class PolicyError(ValueError):
    """A retention policy that would delete evidence the requirements say to keep."""


@cache
def policy() -> dict[str, Any]:
    found = dict(yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {})
    years = int((found.get("audit") or {}).get("retain_years", MIN_AUDIT_YEARS))
    if years < MIN_AUDIT_YEARS:
        raise PolicyError(
            f"audit retention of {years} years is under the {MIN_AUDIT_YEARS} NFR-09 requires"
        )
    return found


@dataclass
class RetentionRun:
    deleted: dict[str, int] = field(default_factory=dict)
    archived: list[str] = field(default_factory=list)


def purge(session: Session, now: datetime | None = None) -> RetentionRun:
    """Delete what the policy no longer keeps."""
    now = now or datetime.now(UTC)
    rules = policy()
    run = RetentionRun()
    days = dict(rules.get("delete_after_days") or {})
    if "activity_minute" in days:
        cutoff = now - timedelta(days=int(days["activity_minute"]))
        result = session.execute(delete(ActivityMinute).where(ActivityMinute.minute < cutoff))
        run.deleted["activity_minute"] = int(getattr(result, "rowcount", 0) or 0)
    if "procrastinate_jobs" in days:
        cutoff = now - timedelta(days=int(days["procrastinate_jobs"]))
        result = session.execute(
            text(
                "DELETE FROM procrastinate_jobs WHERE status IN "
                "('succeeded', 'failed', 'cancelled', 'aborted') AND id IN ("
                "SELECT job_id FROM procrastinate_events GROUP BY job_id "
                "HAVING max(at) < :cutoff)"
            ),
            {"cutoff": cutoff},
        )
        run.deleted["procrastinate_jobs"] = int(getattr(result, "rowcount", 0) or 0)
    expiring = set(rules.get("expire") or [])
    for name, model in (("llm_response_cache", LlmResponseCache), ("llm_payload", LlmPayload)):
        if name in expiring:
            result = session.execute(delete(model).where(model.expires_at <= now))
            run.deleted[name] = int(getattr(result, "rowcount", 0) or 0)
    log.info("retention_purged", **run.deleted)
    return run


def archive_months(session: Session, store: Any, now: datetime | None = None) -> list[str]:
    """Copy each audit month older than the archive age to object storage, once."""
    now = now or datetime.now(UTC)
    months = int((policy().get("audit") or {}).get("archive_after_months", 24))
    before = date(now.year, now.month, 1) - timedelta(days=31 * months)
    oldest = session.execute(
        select(AuditEvent.occurred_at).order_by(AuditEvent.occurred_at).limit(1)
    ).scalar()
    if oldest is None:
        return []
    written = []
    month = date(oldest.year, oldest.month, 1)
    while month < date(before.year, before.month, 1):
        following = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
        key = f"archive/audit/{month:%Y-%m}.jsonl.gz"
        if not store.exists(key):
            rows = session.execute(
                select(AuditEvent)
                .where(AuditEvent.occurred_at >= month, AuditEvent.occurred_at < following)
                .order_by(AuditEvent.occurred_at)
            ).scalars()
            lines = [
                json.dumps(
                    {
                        "id": str(event.id),
                        "chain_key": str(event.chain_key),
                        "occurred_at": event.occurred_at.isoformat(),
                        "action": event.action,
                        "entity_type": event.entity_type,
                        "entity_id": str(event.entity_id),
                        "before": event.before,
                        "after": event.after,
                        "reason": event.reason,
                    },
                    sort_keys=True,
                    default=str,
                )
                for event in rows
            ]
            if lines:
                store.put_once(
                    key,
                    gzip.compress("\n".join(lines).encode("utf-8")),
                    content_type="application/gzip",
                )
                written.append(key)
        month = following
    log.info("audit_archived", months=len(written))
    return written


def verify_all_chains(session: Session) -> dict[str, list[str]]:
    """Every audit chain checked end to end. Returns the broken ones, with what broke."""
    keys = session.execute(select(AuditChainLink.chain_key).distinct()).scalars().all()
    broken: dict[str, list[str]] = {}
    for key in keys:
        problems = verify_chain(session, uuid.UUID(str(key)))
        if problems:
            broken[str(key)] = [str(problem) for problem in problems]
            log.error("audit_chain_broken", chain_key=str(key), problems=len(problems))
    log.info("audit_chains_verified", chains=len(keys), broken=len(broken))
    return broken
