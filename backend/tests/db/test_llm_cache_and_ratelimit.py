"""The response cache and the shared rate limiter, against a real PostgreSQL.

Both exist to behave correctly when several worker processes run at once, so the limiter test
uses real concurrent sessions rather than a simulated clock.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from firebid.ai_gateway.cache import PostgresResponseCache, fingerprint
from firebid.ai_gateway.config import ModelConfig, RouteConfig
from firebid.ai_gateway.ratelimit import PostgresRateLimiter
from firebid.ai_gateway.types import (
    DataClass,
    GenerationRequest,
    GenerationResponse,
    ImagePart,
    Message,
    StopReason,
    ToolCall,
    Usage,
)
from firebid.db.models.ai import LlmRateBucket, LlmResponseCache

MODEL = ModelConfig(provider="anthropic", model_id="claude-opus-5")


def route(data_class: DataClass = DataClass.INTERNAL) -> RouteConfig:
    return RouteConfig(data_class=data_class, models=["m"], requires=[])


def answer(text: str = "the answer") -> GenerationResponse:
    return GenerationResponse(
        text=text,
        stop_reason=StopReason.END,
        provider="anthropic",
        model="claude-opus-5",
        usage=Usage(input_tokens=100, output_tokens=20),
        prompt_version="abc123",
    )


def ask(text: str = "the question") -> GenerationRequest:
    return GenerationRequest(messages=(Message.user(text),))


def key(
    request: GenerationRequest | None = None,
    *,
    route_name: str = "work",
    model: ModelConfig = MODEL,
    prompt_version: str | None = "abc123",
    config_version: str = "cfg1",
) -> str:
    return fingerprint(route_name, request or ask(), model, prompt_version, config_version)


@pytest.mark.req("NFR-15")
class TestFingerprint:
    def test_the_same_request_gives_the_same_key(self) -> None:
        assert key() == key()

    @pytest.mark.parametrize(
        "change",
        [
            {"prompt_version": "different"},
            {"config_version": "cfg2"},
            {"model": ModelConfig(provider="openai", model_id="gpt-5.1")},
            {"route_name": "other"},
        ],
        ids=["prompt-version", "config-version", "model", "route"],
    )
    def test_anything_that_could_change_the_answer_changes_the_key(
        self, change: dict[str, object]
    ) -> None:
        assert key(**change) != key()  # type: ignore[arg-type]

    def test_a_different_question_changes_the_key(self) -> None:
        assert key(ask("something else")) != key(ask("the question"))

    def test_binary_parts_are_hashed_not_embedded(self) -> None:
        """A 30 MB drawing must not become a 40 MB cache key."""
        big = Message(role="user", parts=(ImagePart("image/png", b"x" * 2_000_000),))
        request = GenerationRequest(messages=(big,))
        assert len(key(request)) == 64  # a sha256 hex digest, whatever went in

    def test_a_different_image_changes_the_key(self) -> None:
        first = GenerationRequest(
            messages=(Message(role="user", parts=(ImagePart("image/png", b"one"),)),)
        )
        second = GenerationRequest(
            messages=(Message(role="user", parts=(ImagePart("image/png", b"two"),)),)
        )
        assert key(first) != key(second)


@pytest.mark.req("NFR-15")
class TestResponseCache:
    def test_a_second_identical_call_is_served_from_the_cache(self, session: Session) -> None:
        cache = PostgresResponseCache(session)
        cache_key = key()
        assert cache.get(cache_key) is None

        cache.put(cache_key, answer(), route(), "work")
        session.commit()

        remembered = cache.get(cache_key)
        assert remembered is not None
        assert remembered.text == "the answer"
        assert remembered.cache_hit is True
        assert remembered.usage.input_tokens == 100

    def test_a_hit_is_counted(self, session: Session) -> None:
        cache = PostgresResponseCache(session)
        cache.put(key(), answer(), route(), "work")
        session.commit()
        cache.get(key())
        cache.get(key())
        session.commit()
        row = session.execute(select(LlmResponseCache)).scalar_one()
        assert row.hits == 2

    def test_tool_calls_survive_the_round_trip(self, session: Session) -> None:
        cache = PostgresResponseCache(session)
        stored = GenerationResponse(
            text="",
            stop_reason=StopReason.TOOL_CALL,
            provider="anthropic",
            model="claude-opus-5",
            tool_calls=(ToolCall(id="t1", name="lookup", arguments={"sheet": "FP-L05-201"}),),
        )
        cache.put(key(), stored, route(), "work")
        session.commit()

        remembered = cache.get(key())
        assert remembered is not None
        assert remembered.tool_calls[0].name == "lookup"
        assert remembered.tool_calls[0].arguments == {"sheet": "FP-L05-201"}

    def test_personal_data_is_never_cached(self, session: Session) -> None:
        """Retention for this class is zero, so nothing is written at all."""
        cache = PostgresResponseCache(session)
        cache.put(key(), answer(), route(DataClass.PERSONAL), "work")
        session.commit()
        assert session.execute(select(func.count()).select_from(LlmResponseCache)).scalar() == 0

    def test_confidential_answers_expire_sooner_than_internal_ones(self, session: Session) -> None:
        now = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
        cache = PostgresResponseCache(session, now=lambda: now)
        cache.put(key(), answer(), route(DataClass.INTERNAL), "internal_work")
        cache.put(key(ask("other")), answer(), route(DataClass.CONFIDENTIAL), "confidential_work")
        session.commit()

        rows = {
            row.route: row.expires_at
            for row in session.execute(select(LlmResponseCache)).scalars().all()
        }
        assert rows["confidential_work"] < rows["internal_work"]

    def test_an_expired_entry_is_a_miss_and_is_swept(self, session: Session) -> None:
        now = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
        cache = PostgresResponseCache(session, now=lambda: now)
        cache.put(key(), answer(), route(), "work")
        session.commit()

        later = PostgresResponseCache(session, now=lambda: now + timedelta(days=31))
        assert later.get(key()) is None
        session.commit()
        assert session.execute(select(func.count()).select_from(LlmResponseCache)).scalar() == 0

    def test_two_workers_storing_the_same_answer_do_not_collide(self, session: Session) -> None:
        cache = PostgresResponseCache(session)
        cache.put(key(), answer("first"), route(), "work")
        session.commit()
        cache.put(key(), answer("second"), route(), "work")
        session.commit()
        assert session.execute(select(func.count()).select_from(LlmResponseCache)).scalar() == 1

    def test_a_large_body_goes_to_object_storage(self, session: Session) -> None:
        store = _RecordingStore()
        cache = PostgresResponseCache(session, object_store=store)
        big = "x" * 200_000
        cache.put(key(), answer(big), route(), "work")
        session.commit()

        row = session.execute(select(LlmResponseCache)).scalar_one()
        assert row.body is None and row.body_ref, "a large body should not sit in the row"

        remembered = cache.get(key())
        assert remembered is not None
        assert remembered.text == big


class _RecordingStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_once(self, key: str, data: bytes, media_type: str) -> None:
        self.objects.setdefault(key, data)

    def get(self, key: str) -> bytes:
        return self.objects[key]


@pytest.mark.req("NFR-15")
class TestSharedRateLimiter:
    def test_it_allows_calls_up_to_the_limit_then_refuses(self, session: Session) -> None:
        now = datetime(2026, 9, 23, 12, 0, 30, tzinfo=UTC)
        limiter = PostgresRateLimiter(
            session, timeout_seconds=0, now=lambda: now, sleep=lambda _s: None
        )
        assert [limiter.acquire("anthropic:opus", 3) for _ in range(3)] == [True, True, True]
        assert limiter.acquire("anthropic:opus", 3) is False

    def test_a_new_minute_is_a_new_window(self, session: Session) -> None:
        first_minute = datetime(2026, 9, 23, 12, 0, 30, tzinfo=UTC)
        limiter = PostgresRateLimiter(
            session, timeout_seconds=0, now=lambda: first_minute, sleep=lambda _s: None
        )
        assert limiter.acquire("k", 1) is True
        assert limiter.acquire("k", 1) is False

        next_minute = first_minute + timedelta(minutes=1)
        later = PostgresRateLimiter(
            session, timeout_seconds=0, now=lambda: next_minute, sleep=lambda _s: None
        )
        assert later.acquire("k", 1) is True

    def test_no_limit_configured_means_no_limiting(self, session: Session) -> None:
        limiter = PostgresRateLimiter(session, timeout_seconds=0, sleep=lambda _s: None)
        assert all(limiter.acquire("k", None) for _ in range(50))
        assert session.execute(select(func.count()).select_from(LlmRateBucket)).scalar() == 0

    def test_separate_models_have_separate_budgets(self, session: Session) -> None:
        now = datetime(2026, 9, 23, 12, 0, 30, tzinfo=UTC)
        limiter = PostgresRateLimiter(
            session, timeout_seconds=0, now=lambda: now, sleep=lambda _s: None
        )
        assert limiter.acquire("anthropic:opus", 1) is True
        assert limiter.acquire("anthropic:opus", 1) is False
        assert limiter.acquire("openai:gpt", 1) is True

    def test_parallel_workers_share_one_budget(self, database_url: str) -> None:
        """The reason this lives in the database: one budget across processes, not per process.

        Ten threads on separate connections race for five slots. Exactly five must win.
        """
        from concurrent.futures import ThreadPoolExecutor

        from sqlalchemy import create_engine

        from firebid.db.engine import sqlalchemy_url

        bucket = f"race:{uuid.uuid4().hex[:8]}"
        now = datetime(2026, 9, 23, 12, 0, 30, tzinfo=UTC)
        engine = create_engine(sqlalchemy_url(database_url), pool_size=12, max_overflow=4)

        def claim() -> bool:
            with Session(engine) as own_session:
                limiter = PostgresRateLimiter(
                    own_session, timeout_seconds=0, now=lambda: now, sleep=lambda _s: None
                )
                return limiter.acquire(bucket, 5)

        try:
            with ThreadPoolExecutor(max_workers=10) as pool:
                granted = list(pool.map(lambda _i: claim(), range(10)))
        finally:
            engine.dispose()

        assert sum(granted) == 5, f"expected exactly 5 of 10 to be granted, got {sum(granted)}"


@pytest.mark.req("NFR-08")
def test_the_rate_buckets_hold_no_content(engine: Engine) -> None:
    """A limiter that logged prompts would be a leak; it stores counts only."""
    columns = {column.name for column in LlmRateBucket.__table__.columns}
    assert columns == {"id", "bucket_key", "window_start", "requests", "tokens"}
