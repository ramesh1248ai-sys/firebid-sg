"""The development identity provider issues tokens in Microsoft Entra ID's shape."""

import base64
import json
import os
import uuid
from typing import Any

import httpx2 as httpx
import pytest

pytestmark = pytest.mark.integration

HOST = os.environ.get("FIREBID_STACK_HOST", "localhost")
PORT = os.environ.get("FIREBID_KEYCLOAK_PORT", "8081")
TOKEN_URL = f"http://{HOST}:{PORT}/realms/firebid/protocol/openid-connect/token"


def access_token_claims(username: str) -> dict[str, Any]:
    response = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "password",
            "client_id": "firebid-dev-tests",
            "username": username,
            "password": "firebid-dev",
            "scope": "openid",
        },
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()["access_token"].split(".")[1]
    decoded: dict[str, Any] = json.loads(
        base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    )
    return decoded


@pytest.mark.parametrize(
    ("username", "role"),
    [
        ("estimator@firebid.test", "estimator"),
        ("commercial.director@firebid.test", "commercial_director"),
    ],
)
def test_tokens_carry_entra_shaped_claims(username: str, role: str) -> None:
    claims = access_token_claims(username)
    uuid.UUID(claims["oid"])  # a stable object ID, like Entra's
    assert claims["preferred_username"] == username
    assert role in claims["roles"]
