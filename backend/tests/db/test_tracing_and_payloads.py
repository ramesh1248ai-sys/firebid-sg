"""Observability that does not leak (NFR-14).

The headline test processes a document through the gateway and then reads every log line and
every span attribute, asserting that neither carries the document's text or a price.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import structlog
from cryptography.fernet import Fernet
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.ai_gateway.config import load_config
from firebid.ai_gateway.payloads import NotPermitted, PayloadStore, PayloadsUnavailable
from firebid.ai_gateway.providers.fake import FakeAdapter
from firebid.ai_gateway.router import Router
from firebid.ai_gateway.types import GenerationRequest, Message
from firebid.db.models.ai import LlmPayload
from firebid.db.models.audit import AuditEvent
from firebid.db.models.core import Bid
from firebid.domain.actors import Actor
from firebid.redaction import REDACTED, scrub

# What must never appear in a log line or a span.
SPEC_TEXT = (
    "The wet riser shall comply with SS CP 52 and the breeching inlet shall be located "
    "within 18 m of the fire engine access road, as detailed in specification clause 7.3.2."
)
SUPPLIER_RATE = "SGD 1,284.50"

CONFIG = """
version: 1
providers:
  primary:
    kind: fake
    approved_data_classes: [internal, confidential]
models:
  first:
    provider: primary
    model_id: first-1
    capabilities: [structured_output]
routes:
  work:
    requires: []
    data_class: confidential
    models: [first]
"""


@pytest.mark.req("NFR-14")
class TestRedaction:
    def test_a_long_passage_of_prose_is_not_logged(self) -> None:
        scrubbed = scrub("document_text", SPEC_TEXT)
        assert "SS CP 52" not in str(scrubbed)
        assert "characters" in str(scrubbed)

    def test_a_price_is_removed_even_under_an_allowed_key(self) -> None:
        """`reason` is allowed through, so money inside it still has to go."""
        assert "1,284.50" not in scrub("reason", f"rejected because the rate was {SUPPLIER_RATE}")

    @pytest.mark.parametrize(
        "value", ["SGD 1,284.50", "S$45.00", "$12,000", "1284.50"], ids=["sgd", "s$", "$", "bare"]
    )
    def test_money_in_any_shape_is_removed(self, value: str) -> None:
        assert value not in scrub("reason", f"the price is {value}")

    def test_identifiers_and_counts_survive(self) -> None:
        """Redaction that removed the useful fields would make the logs pointless."""
        assert scrub("route", "title_block_read") == "title_block_read"
        assert scrub("input_tokens", 1234) == 1234
        assert scrub("cache_hit", True) is True

    def test_an_unknown_key_is_redacted_by_default(self) -> None:
        """A field added later is hidden until someone deliberately allows it."""
        assert scrub("supplier_quotation", "anything at all") != "anything at all"

    def test_bytes_are_summarised_not_printed(self) -> None:
        assert scrub("drawing", b"\x89PNG" * 100) == f"{REDACTED} (400 bytes)"


@pytest.mark.req("NFR-14")
def test_no_document_text_or_price_reaches_the_logs_or_the_spans(
    tmp_path: object, caplog: pytest.LogCaptureFixture
) -> None:
    """Process a document through the gateway, then read everything it emitted."""
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    path = tmp_path / "llm.yaml"
    path.write_text(CONFIG, encoding="utf-8")

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    previous = trace.get_tracer_provider()
    trace._TRACER_PROVIDER = provider  # the SDK offers no public reset

    structlog.configure(processors=_processors(), logger_factory=structlog.stdlib.LoggerFactory())

    try:
        adapter = FakeAdapter("primary").reply(f"The rate quoted is {SUPPLIER_RATE}.")
        router = Router(
            config=load_config(path),
            adapters={"primary": adapter},
            backoff_base_seconds=0,
            sleep=lambda _s: None,
        )
        with caplog.at_level(logging.DEBUG):
            router.generate(
                "work",
                GenerationRequest(messages=(Message.user(SPEC_TEXT),), system=SPEC_TEXT),
            )
    finally:
        trace._TRACER_PROVIDER = previous

    logged = caplog.text
    assert "SS CP 52" not in logged, "a specification clause reached the logs"
    assert "breeching inlet" not in logged
    assert "1,284.50" not in logged, "a price reached the logs"

    spans = exporter.get_finished_spans()
    assert spans, "the attempt produced no span at all"
    for span in spans:
        rendered = str(dict(span.attributes or {}))
        assert "SS CP 52" not in rendered, f"a specification clause reached span {span.name}"
        assert "1,284.50" not in rendered, f"a price reached span {span.name}"

    # The span is still useful: it says which model served the call.
    attributes = dict(spans[0].attributes or {})
    assert attributes["route"] == "work"
    assert attributes["model"] == "first"
    assert attributes["provider"] == "primary"


def _processors() -> list[structlog.types.Processor]:
    from firebid.redaction import redact_processor

    return [
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        redact_processor,
        structlog.processors.KeyValueRenderer(),
    ]


@pytest.fixture
def key() -> str:
    return Fernet.generate_key().decode("ascii")


@pytest.mark.req("NFR-14")
class TestPayloadStore:
    administrator = Actor(label="Adele Admin", roles=frozenset({"system_admin"}), id=None)
    estimator = Actor(label="Esther Tan", roles=frozenset({"estimator"}), id=None)

    def test_nothing_is_stored_unless_the_bid_has_opted_in(
        self, session: Session, bid: Bid, key: str
    ) -> None:
        store = PayloadStore(session, key)
        assert store.store(bid.id, None, "work", SPEC_TEXT, "answer") is None
        session.commit()
        assert session.execute(select(LlmPayload)).first() is None

    def test_an_administrator_opts_a_bid_in_and_it_is_audited(
        self, session: Session, bid: Bid, key: str
    ) -> None:
        store = PayloadStore(session, key)
        store.enable_for(bid.id, self.administrator)
        session.commit()

        actions = [row.action for row in session.execute(select(AuditEvent)).scalars().all()]
        assert "llm payloads: capture enabled" in actions

    def test_a_stored_payload_is_ciphertext_in_the_database(
        self, session: Session, bid: Bid, key: str
    ) -> None:
        """A copy of the database alone must not disclose the tender document."""
        store = PayloadStore(session, key)
        store.enable_for(bid.id, self.administrator)
        store.store(bid.id, None, "work", SPEC_TEXT, SUPPLIER_RATE)
        session.commit()

        row = session.execute(select(LlmPayload)).scalar_one()
        assert "SS CP 52" not in row.prompt_encrypted
        assert SUPPLIER_RATE not in row.response_encrypted

    def test_an_administrator_can_read_it_back(self, session: Session, bid: Bid, key: str) -> None:
        store = PayloadStore(session, key)
        store.enable_for(bid.id, self.administrator)
        payload = store.store(bid.id, None, "work", SPEC_TEXT, "the answer")
        session.commit()
        assert payload is not None

        prompt, response = store.read(payload.id, self.administrator)
        assert prompt == SPEC_TEXT
        assert response == "the answer"

    def test_anyone_else_is_refused(self, session: Session, bid: Bid, key: str) -> None:
        store = PayloadStore(session, key)
        store.enable_for(bid.id, self.administrator)
        payload = store.store(bid.id, None, "work", SPEC_TEXT, "the answer")
        session.commit()
        assert payload is not None

        with pytest.raises(NotPermitted):
            store.read(payload.id, self.estimator)

    def test_every_read_is_audited(self, session: Session, bid: Bid, key: str) -> None:
        """Reading a tender document out of the debug store is itself an event."""
        store = PayloadStore(session, key)
        store.enable_for(bid.id, self.administrator)
        payload = store.store(bid.id, None, "work", SPEC_TEXT, "the answer")
        session.commit()
        assert payload is not None

        store.read(payload.id, self.administrator)
        session.commit()

        reads = [
            row
            for row in session.execute(select(AuditEvent)).scalars().all()
            if row.action == "llm payloads: read"
        ]
        assert len(reads) == 1
        assert reads[0].actor_label == "Adele Admin"
        assert reads[0].entity_id == str(payload.id)

    def test_expired_payloads_are_deleted_and_unreadable(
        self, session: Session, bid: Bid, key: str
    ) -> None:
        now = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
        store = PayloadStore(session, key, now=lambda: now)
        store.enable_for(bid.id, self.administrator, retention_days=14)
        payload = store.store(bid.id, None, "work", SPEC_TEXT, "the answer")
        session.commit()
        assert payload is not None

        later = PayloadStore(session, key, now=lambda: now + timedelta(days=15))
        with pytest.raises(LookupError, match="expired"):
            later.read(payload.id, self.administrator)
        assert later.delete_expired() == 1
        session.commit()
        assert session.execute(select(LlmPayload)).first() is None

    def test_without_a_key_nothing_can_be_stored(self, session: Session, bid: Bid) -> None:
        store = PayloadStore(session, encryption_key="")
        store.enable_for(bid.id, self.administrator)
        session.commit()
        with pytest.raises(PayloadsUnavailable, match="FIREBID_PAYLOAD_ENCRYPTION_KEY"):
            store.store(bid.id, None, "work", SPEC_TEXT, "answer")

    def test_a_payload_stored_under_another_key_is_not_silently_wrong(
        self, session: Session, bid: Bid, key: str
    ) -> None:
        store = PayloadStore(session, key)
        store.enable_for(bid.id, self.administrator)
        payload = store.store(bid.id, None, "work", SPEC_TEXT, "the answer")
        session.commit()
        assert payload is not None

        rotated = PayloadStore(session, Fernet.generate_key().decode("ascii"))
        with pytest.raises(PayloadsUnavailable, match="another key"):
            rotated.read(payload.id, self.administrator)

    def test_turning_capture_off_stops_new_payloads(
        self, session: Session, bid: Bid, key: str
    ) -> None:
        store = PayloadStore(session, key)
        store.enable_for(bid.id, self.administrator)
        store.store(bid.id, None, "work", SPEC_TEXT, "one")
        store.disable_for(bid.id, self.administrator)
        assert store.store(bid.id, None, "work", SPEC_TEXT, "two") is None
        session.commit()
        assert len(session.execute(select(LlmPayload)).scalars().all()) == 1


@pytest.mark.req("NFR-14")
def test_a_payload_for_an_unknown_bid_is_refused(session: Session) -> None:
    store = PayloadStore(session, Fernet.generate_key().decode("ascii"))
    with pytest.raises(LookupError):
        store.enable_for(uuid.uuid4(), Actor(label="A", roles=frozenset({"system_admin"}), id=None))
