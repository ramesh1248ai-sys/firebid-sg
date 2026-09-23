"""Tables the AI gateway owns: the response cache and the shared rate-limit buckets.

Neither is bid-scoped in the row-level-security sense — a cache entry records the data class
it holds so retention can differ per class, and a rate bucket holds no content at all.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from firebid.db.base import Base
from firebid.db.mixins import Timestamped, UuidPk, personal

DATA_CLASSES = ("internal", "confidential", "commercial", "personal")


class LlmResponseCache(UuidPk, Timestamped, Base):
    """An answer we have already paid for.

    Keyed by route, model, prompt version and a fingerprint of the normalised input, so a
    change to any of them misses rather than serving a stale answer. Large bodies live in
    object storage; `body_ref` points at them.
    """

    __tablename__ = "llm_response_cache"
    __table_args__ = (
        UniqueConstraint("cache_key", name="uq_llm_response_cache_key"),
        CheckConstraint(f"data_class IN {DATA_CLASSES}", name="ck_llm_cache_data_class"),
    )

    cache_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    route: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    config_version: Mapped[str | None] = mapped_column(String(64))
    data_class: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    # Small bodies inline, large ones in object storage. Exactly one is set.
    body: Mapped[str | None] = mapped_column(Text)
    body_ref: Mapped[str | None] = mapped_column(String(500))

    stop_reason: Mapped[str] = mapped_column(String(20), nullable=False)
    tool_calls: Mapped[list[dict[str, object]]] = mapped_column(JSONB, default=list)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    hits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class LlmRateBucket(UuidPk, Base):
    """A fixed window of spend against one provider-and-model, shared by every worker.

    Holds counts only: no prompt, no response, nothing that could leak content.
    """

    __tablename__ = "llm_rate_bucket"
    __table_args__ = (UniqueConstraint("bucket_key", "window_start", name="uq_llm_rate_window"),)

    bucket_key: Mapped[str] = mapped_column(String(120), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)


class BidBudget(UuidPk, Timestamped, Base):
    """What a bid may spend on AI processing, and who to tell as it runs down (NFR-15).

    Bid-scoped, so it carries the same row-level security as the rest of a bid's data.
    """

    __tablename__ = "bid_budget"
    __table_args__ = (UniqueConstraint("bid_id", name="uq_bid_budget_bid"),)

    bid_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("bid.id", ondelete="CASCADE"), nullable=False, index=True
    )
    limit_sgd: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # Fractions of the limit at which to warn, e.g. [0.5, 0.8, 1.0].
    alert_at: Mapped[list[float]] = mapped_column(JSONB, default=lambda: [0.5, 0.8, 1.0])
    # Which of those have already been sent, so a threshold warns once rather than every call.
    alerts_sent: Mapped[list[float]] = mapped_column(JSONB, default=list)
    owner_email: Mapped[str | None] = mapped_column(
        String(320), info=personal("who to tell when the bid's AI budget runs down")
    )
