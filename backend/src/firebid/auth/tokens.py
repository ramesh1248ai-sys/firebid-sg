"""Verifying OIDC access tokens against the provider's published keys.

Configuration only: Keycloak in development, Microsoft Entra ID in staging and production.
Keys are fetched from the issuer's JWKS endpoint and cached by the client.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Protocol

import jwt
from jwt import PyJWKClient

from firebid.settings import Settings, get_settings


class TokenError(Exception):
    """The token is missing, malformed, expired or not for this application."""


class TokenVerifier(Protocol):
    def verify(self, token: str) -> dict[str, Any]: ...


class OidcTokenVerifier:
    def __init__(self, issuer: str, audience: str, jwks_url: str, leeway_seconds: int = 30) -> None:
        self.issuer = issuer
        self.audience = audience
        self.leeway_seconds = leeway_seconds
        self._keys = PyJWKClient(jwks_url, cache_keys=True)

    def verify(self, token: str) -> dict[str, Any]:
        try:
            key = self._keys.get_signing_key_from_jwt(token).key
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=["RS256", "ES256"],
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway_seconds,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.PyJWTError as error:
            raise TokenError(str(error)) from error
        return claims


@lru_cache
def get_token_verifier(settings: Settings | None = None) -> OidcTokenVerifier:
    settings = settings or get_settings()
    issuer = settings.oidc_issuer.rstrip("/")
    return OidcTokenVerifier(
        issuer=issuer,
        audience=settings.oidc_audience,
        jwks_url=settings.oidc_jwks_url or f"{issuer}/protocol/openid-connect/certs",
    )
