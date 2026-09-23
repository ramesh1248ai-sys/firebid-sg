"""The debugging payload store: prompts and responses, kept only when someone asks.

When an agent gets something wrong, the fastest way to find out why is to read what it was
actually sent. That is also the most dangerous thing to keep, because it is the tender
document in a second place.

So it is off by default and every safeguard is a real one:

* **Opt-in per bid**, switched on by an administrator, never a default.
* **Encrypted at rest** with a key held outside the database.
* **Expires**, default fourteen days, swept by the retention job.
* **Readable only by a named administrator**, and every read is written to the audit trail —
  so reading a payload is itself an event someone can be asked about.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import structlog
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from firebid.db.audit import record_event
from firebid.db.models.ai import LlmPayload, PayloadCapture
from firebid.db.models.core import Bid
from firebid.domain.actors import Actor, AuditContext

log = structlog.get_logger("firebid.ai_gateway.payloads")

DEFAULT_RETENTION_DAYS = 14


class PayloadsUnavailable(Exception):
    """No encryption key is configured, so payloads cannot be stored or read."""


class NotPermitted(Exception):
    """This person may not read stored payloads."""


def _cipher(key: str | None) -> Fernet:
    if not key:
        raise PayloadsUnavailable(
            "set FIREBID_PAYLOAD_ENCRYPTION_KEY to store or read debugging payloads"
        )
    return Fernet(key.encode("utf-8"))


class PayloadStore:
    def __init__(
        self,
        session: Session,
        encryption_key: str | None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._key = encryption_key
        self._now = now or (lambda: datetime.now(UTC))

    # ---- switching it on and off ----------------------------------------------------

    def enable_for(
        self,
        bid_id: uuid.UUID,
        administrator: Actor,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ) -> PayloadCapture:
        """Turn capture on for one bid. An administrator's deliberate act, and audited."""
        context = self._audit_context(bid_id)  # refuses an unknown bid before writing anything
        capture = self._capture_for(bid_id)
        if capture is None:
            capture = PayloadCapture(bid_id=bid_id)
            self._session.add(capture)
        capture.enabled = True
        capture.retention_days = retention_days
        capture.enabled_by = administrator.label
        capture.enabled_at = self._now()
        self._session.flush()

        record_event(
            self._session,
            context=context,
            actor=administrator,
            action="llm payloads: capture enabled",
            entity_type="bid",
            entity_id=str(bid_id),
            after={"retention_days": retention_days},
            reason="debugging",
        )
        return capture

    def disable_for(self, bid_id: uuid.UUID, administrator: Actor) -> None:
        capture = self._capture_for(bid_id)
        if capture is None or not capture.enabled:
            return
        capture.enabled = False
        self._session.flush()
        record_event(
            self._session,
            context=self._audit_context(bid_id),
            actor=administrator,
            action="llm payloads: capture disabled",
            entity_type="bid",
            entity_id=str(bid_id),
        )

    def is_enabled(self, bid_id: uuid.UUID | None) -> bool:
        if bid_id is None:
            return False
        capture = self._capture_for(bid_id)
        return capture is not None and capture.enabled

    def _audit_context(self, bid_id: uuid.UUID) -> AuditContext:
        """Audit events are organisation-scoped, so the bid supplies its organisation."""
        bid = self._session.get(Bid, bid_id)
        if bid is None:
            raise LookupError(f"no bid {bid_id}")
        return AuditContext(organisation_id=bid.organisation_id, bid_id=bid_id)

    def _capture_for(self, bid_id: uuid.UUID) -> PayloadCapture | None:
        return self._session.execute(
            select(PayloadCapture).where(PayloadCapture.bid_id == bid_id)
        ).scalar_one_or_none()

    # ---- storing --------------------------------------------------------------------

    def store(
        self,
        bid_id: uuid.UUID | None,
        run_id: uuid.UUID | None,
        route: str,
        prompt: str,
        response: str,
    ) -> LlmPayload | None:
        """Keep one prompt and response, if this bid has opted in. Otherwise do nothing."""
        if bid_id is None or not self.is_enabled(bid_id):
            return None

        capture = self._capture_for(bid_id)
        retention = capture.retention_days if capture else DEFAULT_RETENTION_DAYS
        cipher = _cipher(self._key)

        payload = LlmPayload(
            bid_id=bid_id,
            agent_run_id=run_id,
            route=route,
            prompt_encrypted=cipher.encrypt(prompt.encode("utf-8")).decode("ascii"),
            response_encrypted=cipher.encrypt(response.encode("utf-8")).decode("ascii"),
            expires_at=self._now() + timedelta(days=retention),
        )
        self._session.add(payload)
        self._session.flush()
        log.info("payload_stored", route=route, bid_id=str(bid_id))
        return payload

    # ---- reading --------------------------------------------------------------------

    def read(self, payload_id: uuid.UUID, reader: Actor) -> tuple[str, str]:
        """Decrypt one payload. Administrators only, and the read is recorded."""
        if "system_admin" not in reader.roles:
            raise NotPermitted("only a system administrator may read stored payloads")

        payload = self._session.get(LlmPayload, payload_id)
        if payload is None:
            raise LookupError(f"no payload {payload_id}")
        if payload.expires_at <= self._now():
            raise LookupError("that payload has expired")

        cipher = _cipher(self._key)
        try:
            prompt = cipher.decrypt(payload.prompt_encrypted.encode("ascii")).decode("utf-8")
            response = cipher.decrypt(payload.response_encrypted.encode("ascii")).decode("utf-8")
        except InvalidToken as error:
            raise PayloadsUnavailable(
                "the configured key cannot decrypt this payload; it was stored under another key"
            ) from error

        # Reading a payload is itself an event someone can be asked about.
        record_event(
            self._session,
            context=self._audit_context(payload.bid_id),
            actor=reader,
            action="llm payloads: read",
            entity_type="llm_payload",
            entity_id=str(payload_id),
            reason="debugging",
        )
        payload.reads = payload.reads + 1
        self._session.flush()
        return prompt, response

    # ---- sweeping -------------------------------------------------------------------

    def delete_expired(self) -> int:
        result = self._session.execute(
            delete(LlmPayload).where(LlmPayload.expires_at <= self._now())
        )
        return int(getattr(result, "rowcount", 0) or 0)
