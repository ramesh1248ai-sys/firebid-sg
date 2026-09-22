"""The security baseline: headers, CORS, body limits and rate limiting (NFR-06, NFR-08).

Each test states the control it holds in place; docs/security-baseline.md maps them to ASVS.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from firebid.api.app import create_app
from firebid.api.security import SECURITY_HEADERS
from firebid.settings import Settings

pytestmark = pytest.mark.req("NFR-06")


def make_client(**overrides: object) -> TestClient:
    settings = Settings(env="test", **overrides)  # type: ignore[arg-type]
    return TestClient(create_app(settings, health_checks={}))


class TestResponseHeaders:
    def test_every_response_carries_the_baseline_headers(self) -> None:
        response = make_client().get("/version")
        for name, value in SECURITY_HEADERS:
            assert response.headers[name.decode()] == value.decode()

    def test_a_refusal_carries_them_too(self) -> None:
        """An error page is exactly where sniffing and framing protection matter."""
        response = make_client().get("/bids")
        assert response.status_code == 401
        assert response.headers["x-content-type-options"] == "nosniff"
        assert (
            response.headers["content-security-policy"]
            == "default-src 'none'; frame-ancestors 'none'"
        )

    def test_the_request_id_is_still_echoed(self) -> None:
        response = make_client().get("/version", headers={"x-request-id": "known-request-01"})
        assert response.headers["x-request-id"] == "known-request-01"


class TestCrossOrigin:
    def test_no_origin_is_allowed_by_default(self) -> None:
        """The app and API share an origin, so nothing needs to be let through."""
        response = make_client().get("/version", headers={"origin": "https://attacker.example"})
        assert "access-control-allow-origin" not in response.headers

    def test_only_a_configured_origin_is_allowed(self) -> None:
        client = make_client(cors_allowed_origins=("https://app.firebid.test",))
        allowed = client.get("/version", headers={"origin": "https://app.firebid.test"})
        assert allowed.headers["access-control-allow-origin"] == "https://app.firebid.test"

        refused = client.get("/version", headers={"origin": "https://attacker.example"})
        assert "access-control-allow-origin" not in refused.headers


class TestBodySize:
    def test_a_declared_oversized_body_is_refused_before_the_route_runs(self) -> None:
        client = make_client(max_body_bytes=1024)
        response = client.post("/bids", content=b"x" * 2048)
        assert response.status_code == 413
        assert response.json()["detail"] == "request body too large"

    def test_a_streamed_oversized_body_is_refused_too(self) -> None:
        """Without a content-length, the limit has to hold as the bytes arrive."""
        client = make_client(max_body_bytes=1024)

        def chunks() -> Iterator[bytes]:
            for _ in range(4):
                yield b"x" * 512

        response = client.post("/bids", content=chunks())
        assert response.status_code == 413

    def test_a_body_within_the_limit_reaches_the_route(self) -> None:
        client = make_client(max_body_bytes=1024)
        response = client.post("/bids", json={"client_name": "x"})
        assert response.status_code == 401  # refused for want of a token, not for size


class TestRateLimit:
    def test_a_caller_over_the_budget_is_slowed_down(self) -> None:
        client = make_client(rate_limit_per_minute=3)
        for _ in range(3):
            assert client.get("/version").status_code == 200
        refused = client.get("/version")
        assert refused.status_code == 429
        assert refused.json()["detail"] == "too many requests; slow down"

    def test_callers_are_counted_apart(self) -> None:
        client = make_client(rate_limit_per_minute=2)
        for _ in range(2):
            client.get("/version", headers={"authorization": "Bearer one"})
        assert client.get("/version", headers={"authorization": "Bearer one"}).status_code == 429
        assert client.get("/version", headers={"authorization": "Bearer two"}).status_code == 200

    def test_health_checks_are_never_rate_limited(self) -> None:
        """A limiter that blocks the liveness probe takes the service down itself."""
        client = make_client(rate_limit_per_minute=1)
        for _ in range(5):
            assert client.get("/health/live").status_code == 200
