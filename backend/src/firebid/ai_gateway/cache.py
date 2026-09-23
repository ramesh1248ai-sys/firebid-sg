"""The response cache: do not pay twice for the same answer.

Keyed by route, model, prompt version, configuration version and a fingerprint of the
normalised request, so any change to what was asked — or to who was asked, or how — misses
rather than serving a stale answer.

Two properties matter beyond saving money:

* A cached entry records the data class it holds, so retention is shorter for confidential
  and commercial answers than for internal ones, and personal data is never cached at all.
* A hit is recorded on the response, so a reader of the provenance can tell a fresh answer
  from a remembered one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import structlog
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from firebid.ai_gateway.config import ModelConfig, RouteConfig
from firebid.ai_gateway.types import (
    DataClass,
    DocumentPart,
    GenerationRequest,
    GenerationResponse,
    ImagePart,
    StopReason,
    TextPart,
    ToolCall,
    Usage,
)
from firebid.db.models.ai import LlmResponseCache

log = structlog.get_logger("firebid.ai_gateway.cache")

# Bodies longer than this go to object storage rather than bloating the row.
INLINE_LIMIT = 64 * 1024

# How long an answer may be kept, by how sensitive it is.
DEFAULT_RETENTION = {
    DataClass.INTERNAL: timedelta(days=30),
    DataClass.CONFIDENTIAL: timedelta(days=7),
    DataClass.COMMERCIAL: timedelta(days=7),
    DataClass.PERSONAL: timedelta(0),  # never cached
}


class ResponseCache(Protocol):
    def get(self, key: str) -> GenerationResponse | None: ...

    def put(
        self, key: str, response: GenerationResponse, route: RouteConfig, route_name: str
    ) -> None: ...


def fingerprint(
    route_name: str,
    request: GenerationRequest,
    model: ModelConfig,
    prompt_version: str | None,
    config_version: str,
) -> str:
    """A stable key for a request, sensitive to everything that could change the answer.

    Binary parts are hashed rather than embedded, so a 30 MB drawing does not become a 40 MB
    cache key.
    """
    parts: list[object] = []
    for message in request.messages:
        for part in message.parts:
            if isinstance(part, TextPart):
                parts.append(["text", message.role, part.text])
            elif isinstance(part, ImagePart | DocumentPart):
                digest = hashlib.sha256(part.data).hexdigest()
                parts.append([type(part).__name__, message.role, part.media_type, digest])

    material = {
        "route": route_name,
        "model": model.model_id,
        "provider": model.provider,
        "prompt_version": prompt_version,
        "config_version": config_version,
        "reasoning": str(request.reasoning),
        "max_output_tokens": request.max_output_tokens,
        "system": request.system,
        "output_model": request.output_model.__name__ if request.output_model else None,
        "output_schema": (
            request.output_model.model_json_schema() if request.output_model else None
        ),
        "tools": sorted(tool.name for tool in request.tools),
        "parts": parts,
    }
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _tool_call(stored: dict[str, Any]) -> ToolCall:
    """Rebuild a tool call from JSONB, where everything has become a plain object."""
    arguments = stored.get("arguments") or {}
    return ToolCall(
        id=str(stored.get("id", "")),
        name=str(stored.get("name", "")),
        arguments=dict(arguments) if isinstance(arguments, dict) else {},
    )


class NullCache:
    """No caching. The default, so the gateway works with no database behind it."""

    def get(self, key: str) -> GenerationResponse | None:
        return None

    def put(
        self, key: str, response: GenerationResponse, route: RouteConfig, route_name: str
    ) -> None:
        return None


class PostgresResponseCache:
    def __init__(
        self,
        session: Session,
        object_store: Any | None = None,
        retention: dict[DataClass, timedelta] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._store = object_store
        self._retention = retention or DEFAULT_RETENTION
        self._now = now or (lambda: datetime.now(UTC))

    def get(self, key: str) -> GenerationResponse | None:
        row = self._session.execute(
            select(LlmResponseCache).where(LlmResponseCache.cache_key == key)
        ).scalar_one_or_none()
        if row is None:
            return None
        if row.expires_at <= self._now():
            # Expired entries are swept on the way past, so the table does not grow forever.
            self._session.execute(delete(LlmResponseCache).where(LlmResponseCache.id == row.id))
            return None

        body = row.body
        if body is None and row.body_ref and self._store is not None:
            body = self._store.get(row.body_ref).decode("utf-8")
        if body is None:
            return None

        self._session.execute(
            update(LlmResponseCache)
            .where(LlmResponseCache.id == row.id)
            .values(hits=LlmResponseCache.hits + 1)
        )
        return GenerationResponse(
            text=body,
            stop_reason=StopReason(row.stop_reason),
            provider=row.provider,
            model=row.model,
            usage=Usage(input_tokens=row.tokens_in, output_tokens=row.tokens_out),
            tool_calls=tuple(_tool_call(call) for call in (row.tool_calls or [])),
            route=row.route,
            prompt_version=row.prompt_version,
            cache_hit=True,
        )

    def put(
        self, key: str, response: GenerationResponse, route: RouteConfig, route_name: str
    ) -> None:
        retention = self._retention.get(route.data_class, timedelta(0))
        if retention <= timedelta(0):
            log.debug("cache_skipped", route=route_name, data_class=str(route.data_class))
            return  # this class is never cached

        body: str | None = response.text
        body_ref: str | None = None
        if len(response.text.encode("utf-8")) > INLINE_LIMIT and self._store is not None:
            body_ref = f"llm-cache/{key}.txt"
            self._store.put_once(body_ref, response.text.encode("utf-8"), "text/plain")
            body = None

        self._session.execute(
            insert(LlmResponseCache)
            .values(
                cache_key=key,
                route=route_name,
                provider=response.provider,
                model=response.model,
                prompt_version=response.prompt_version,
                data_class=str(route.data_class),
                body=body,
                body_ref=body_ref,
                stop_reason=str(response.stop_reason),
                tool_calls=[
                    {"id": call.id, "name": call.name, "arguments": dict(call.arguments)}
                    for call in response.tool_calls
                ],
                tokens_in=response.usage.input_tokens,
                tokens_out=response.usage.output_tokens,
                expires_at=self._now() + retention,
            )
            # A racing worker may have stored the same answer; theirs is as good as ours.
            .on_conflict_do_nothing(index_elements=["cache_key"])
        )
