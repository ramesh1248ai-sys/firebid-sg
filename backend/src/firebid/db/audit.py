"""The append-only audit trail and its per-bid hash chain (NFR-09).

Services call :func:`record_event`. When the session commits, one chain link per chain is
appended, covering every event written in that transaction. So a bulk action over thousands of
rows costs a single link, and two bids never wait on each other's lock.

The database refuses UPDATE and DELETE on both tables (migration 0002), so the chain can only
be broken by someone with owner rights, and :func:`verify_chain` then reports it.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from firebid.db.models.audit import AuditChainLink, AuditEvent
from firebid.domain.actors import Actor, AuditContext

_PENDING = "firebid_pending_audit_events"
_TX_ID = "firebid_tx_id"


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def event_digest(record: AuditEvent) -> str:
    """The hashed form of one event. Any change to these fields breaks the chain."""
    return hashlib.sha256(
        _canonical(
            {
                "id": str(record.id),
                "occurred_at": record.occurred_at.astimezone(UTC).isoformat(),
                "organisation_id": str(record.organisation_id),
                "bid_id": str(record.bid_id) if record.bid_id else None,
                "actor_id": str(record.actor_id) if record.actor_id else None,
                "actor_label": record.actor_label,
                "action": record.action,
                "entity_type": record.entity_type,
                "entity_id": record.entity_id,
                "before": record.before,
                "after": record.after,
                "reason": record.reason,
            }
        ).encode()
    ).hexdigest()


def link_hash(prev_hash: str | None, events_hash: str, seq: int) -> str:
    return hashlib.sha256(f"{prev_hash or ''}|{events_hash}|{seq}".encode()).hexdigest()


def _events_hash(records: list[AuditEvent]) -> str:
    digests = sorted(event_digest(record) for record in records)
    return hashlib.sha256("".join(digests).encode()).hexdigest()


def _transaction_id(session: Session) -> int:
    """The database transaction ID, so events written together can be found together."""
    if _TX_ID not in session.info:
        session.info[_TX_ID] = int(session.execute(select(func.pg_current_xact_id())).scalar_one())
    return int(session.info[_TX_ID])


def _lock_key(chain_key: uuid.UUID) -> int:
    return int.from_bytes(chain_key.bytes[:8], "big", signed=True)


def record_event(
    session: Session,
    *,
    context: AuditContext,
    actor: Actor,
    action: str,
    entity_type: str,
    entity_id: str | uuid.UUID,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
) -> AuditEvent:
    """Add one event to this transaction. Its chain link is written at commit."""
    record = AuditEvent(
        id=uuid.uuid4(),
        occurred_at=datetime.now(UTC),
        organisation_id=context.organisation_id,
        bid_id=context.bid_id,
        chain_key=context.chain_key,
        actor_id=actor.id,
        actor_label=actor.label,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        before=before,
        after=after,
        reason=reason,
        tx_id=_transaction_id(session),
    )
    session.add(record)
    session.info.setdefault(_PENDING, []).append(record)
    return record


def append_chain_links(session: Session) -> list[AuditChainLink]:
    """Close the chain for every scope touched in this transaction. Called before commit."""
    pending: list[AuditEvent] = session.info.get(_PENDING, [])
    if not pending:
        return []
    session.flush()

    by_chain: dict[uuid.UUID, list[AuditEvent]] = defaultdict(list)
    for record in pending:
        by_chain[record.chain_key].append(record)

    links: list[AuditChainLink] = []
    for chain_key, records in sorted(by_chain.items(), key=lambda item: str(item[0])):
        # Held until this transaction ends, and only for this chain.
        session.execute(select(func.pg_advisory_xact_lock(_lock_key(chain_key))))
        previous = session.execute(
            select(AuditChainLink)
            .where(AuditChainLink.chain_key == chain_key)
            .order_by(AuditChainLink.seq.desc())
            .limit(1)
        ).scalar_one_or_none()
        seq = (previous.seq if previous else 0) + 1
        events_hash = _events_hash(records)
        link = AuditChainLink(
            chain_key=chain_key,
            organisation_id=records[0].organisation_id,
            bid_id=records[0].bid_id,
            seq=seq,
            event_count=len(records),
            events_hash=events_hash,
            prev_hash=previous.hash if previous else None,
            hash=link_hash(previous.hash if previous else None, events_hash, seq),
            tx_id=records[0].tx_id,
        )
        session.add(link)
        links.append(link)

    session.info[_PENDING] = []
    session.flush()
    return links


@dataclass(frozen=True)
class ChainProblem:
    seq: int
    detail: str


def verify_chain(session: Session, chain_key: uuid.UUID) -> list[ChainProblem]:
    """Recompute a chain from its events. An empty list means the history is intact."""
    links = list(
        session.execute(
            select(AuditChainLink)
            .where(AuditChainLink.chain_key == chain_key)
            .order_by(AuditChainLink.seq)
        )
        .scalars()
        .all()
    )
    problems: list[ChainProblem] = []
    previous_hash: str | None = None
    for index, link in enumerate(links, start=1):
        if link.seq != index:
            problems.append(ChainProblem(link.seq, f"expected sequence {index}"))
        if link.prev_hash != previous_hash:
            problems.append(ChainProblem(link.seq, "does not follow the previous link"))
        records = list(
            session.execute(
                select(AuditEvent)
                .where(AuditEvent.chain_key == chain_key, AuditEvent.tx_id == link.tx_id)
                .order_by(AuditEvent.id)
            )
            .scalars()
            .all()
        )
        if len(records) != link.event_count:
            problems.append(
                ChainProblem(link.seq, f"expected {link.event_count} events, found {len(records)}")
            )
        elif _events_hash(records) != link.events_hash:
            problems.append(ChainProblem(link.seq, "an event was altered after it was recorded"))
        if link_hash(link.prev_hash, link.events_hash, link.seq) != link.hash:
            problems.append(ChainProblem(link.seq, "the link's own hash does not match"))
        previous_hash = link.hash
    return problems


@event.listens_for(Session, "before_commit")
def _close_chains_before_commit(session: Session) -> None:
    append_chain_links(session)


@event.listens_for(Session, "after_commit")
@event.listens_for(Session, "after_rollback")
def _forget_transaction_state(session: Session) -> None:
    """A session outlives its transactions, so the cached transaction ID must not."""
    session.info.pop(_TX_ID, None)
    session.info.pop(_PENDING, None)
