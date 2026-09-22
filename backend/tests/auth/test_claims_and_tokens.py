"""Claims mapping and token verification.

The application knows Microsoft Entra ID's claim shape. The development Keycloak realm is
configured to issue the same claims, so both are checked here against the same mapper.
"""

from __future__ import annotations

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from firebid.auth.claims import ClaimsError, map_claims
from firebid.auth.tokens import OidcTokenVerifier, TokenError, get_token_verifier
from firebid.settings import Settings

ISSUER = "https://login.microsoftonline.com/tenant/v2.0"
AUDIENCE = "firebid-web"


def entra_claims(**overrides: Any) -> dict[str, Any]:
    claims = {
        "oid": "8f14e45f-ceea-467a-9f5a-1b2c3d4e5f60",
        "preferred_username": "estimator@firebid.test",
        "name": "Esther Tan",
        "roles": ["estimator", "senior_estimator"],
        "tid": "tenant",
    }
    claims.update(overrides)
    return claims


def keycloak_claims(**overrides: Any) -> dict[str, Any]:
    """What the dev realm issues: the same claim names, plus Keycloak's own extras."""
    claims = {
        "oid": "3c4d5e6f-7a8b-49c0-9d1e-2f3a4b5c6d7e",
        "sub": "3c4d5e6f-7a8b-49c0-9d1e-2f3a4b5c6d7e",
        "preferred_username": "estimator@firebid.test",
        "name": "Esther Tan",
        "roles": ["estimator", "offline_access", "default-roles-firebid"],
        "realm_access": {"roles": ["estimator"]},
    }
    claims.update(overrides)
    return claims


class TestClaimsMapper:
    def test_reads_an_entra_token(self) -> None:
        identity = map_claims(entra_claims())
        assert identity.external_id == "8f14e45f-ceea-467a-9f5a-1b2c3d4e5f60"
        assert identity.username == "estimator@firebid.test"
        assert identity.display_name == "Esther Tan"
        assert identity.application_roles == frozenset({"estimator", "senior_estimator"})

    def test_reads_a_keycloak_token_the_same_way(self) -> None:
        identity = map_claims(keycloak_claims())
        assert identity.username == "estimator@firebid.test"
        assert identity.display_name == "Esther Tan"
        assert identity.application_roles == frozenset({"estimator"})

    def test_ignores_directory_roles_it_does_not_know(self) -> None:
        identity = map_claims(keycloak_claims())
        assert identity.unknown_roles == frozenset({"offline_access", "default-roles-firebid"})

    def test_falls_back_to_sub_when_there_is_no_oid(self) -> None:
        claims = entra_claims()
        del claims["oid"]
        claims["sub"] = "fallback-subject"
        assert map_claims(claims).external_id == "fallback-subject"

    def test_falls_back_to_the_username_for_a_display_name(self) -> None:
        claims = entra_claims()
        del claims["name"]
        assert map_claims(claims).display_name == "estimator@firebid.test"

    @pytest.mark.parametrize(
        ("missing", "message"),
        [("oid", "'oid'"), ("preferred_username", "preferred_username")],
    )
    def test_refuses_a_token_without_the_claims_it_needs(self, missing: str, message: str) -> None:
        claims = entra_claims()
        del claims[missing]
        claims.pop("sub", None)
        with pytest.raises(ClaimsError, match=message):
            map_claims(claims)

    def test_refuses_roles_that_are_not_a_list(self) -> None:
        with pytest.raises(ClaimsError, match="must be a list"):
            map_claims(entra_claims(roles={"not": "a list"}))

    def test_accepts_a_single_role_as_a_string(self) -> None:
        assert map_claims(entra_claims(roles="estimator")).application_roles == frozenset(
            {"estimator"}
        )


@pytest.fixture(scope="module")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def verifier(signing_key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch) -> OidcTokenVerifier:
    verifier = OidcTokenVerifier(
        issuer=ISSUER, audience=AUDIENCE, jwks_url=f"{ISSUER}/discovery/keys"
    )

    class FakeKey:
        key = signing_key.public_key()

    monkeypatch.setattr(verifier._keys, "get_signing_key_from_jwt", lambda token: FakeKey())
    return verifier


def make_token(signing_key: rsa.RSAPrivateKey, **overrides: Any) -> str:
    claims = entra_claims(
        iss=ISSUER, aud=AUDIENCE, exp=int(time.time()) + 300, iat=int(time.time())
    )
    claims.update(overrides)
    return jwt.encode(claims, signing_key, algorithm="RS256")


class TestTokenVerification:
    def test_accepts_a_valid_token(
        self, verifier: OidcTokenVerifier, signing_key: rsa.RSAPrivateKey
    ) -> None:
        claims = verifier.verify(make_token(signing_key))
        assert map_claims(claims).username == "estimator@firebid.test"

    def test_refuses_an_expired_token(
        self, verifier: OidcTokenVerifier, signing_key: rsa.RSAPrivateKey
    ) -> None:
        with pytest.raises(TokenError, match="expired"):
            verifier.verify(make_token(signing_key, exp=int(time.time()) - 600))

    def test_refuses_a_token_for_another_application(
        self, verifier: OidcTokenVerifier, signing_key: rsa.RSAPrivateKey
    ) -> None:
        with pytest.raises(TokenError, match=r"[Aa]udience"):
            verifier.verify(make_token(signing_key, aud="another-app"))

    def test_refuses_a_token_from_another_issuer(
        self, verifier: OidcTokenVerifier, signing_key: rsa.RSAPrivateKey
    ) -> None:
        with pytest.raises(TokenError, match=r"[Ii]ssuer"):
            verifier.verify(make_token(signing_key, iss="https://evil.example/v2.0"))

    def test_refuses_a_token_signed_by_another_key(self, verifier: OidcTokenVerifier) -> None:
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with pytest.raises(TokenError):
            verifier.verify(make_token(other_key))

    def test_refuses_an_unsigned_token(self, verifier: OidcTokenVerifier) -> None:
        unsigned = jwt.encode(entra_claims(iss=ISSUER, aud=AUDIENCE), key="", algorithm="none")
        with pytest.raises(TokenError):
            verifier.verify(unsigned)


class TestVerifierFactory:
    """The factory is what the API actually calls; the tests above build verifiers directly."""

    def test_it_builds_a_verifier_from_settings(self) -> None:
        settings = Settings(
            env="test",
            oidc_issuer="https://login.example/tenant/v2.0/",
            oidc_audience="firebid-web",
        )
        verifier = get_token_verifier(settings)
        assert verifier.issuer == "https://login.example/tenant/v2.0"
        assert verifier.jwks_url.endswith("/protocol/openid-connect/certs")

    def test_an_explicit_key_set_url_is_used(self) -> None:
        """The stack fetches keys inside its network while tokens name the browser's URL."""
        settings = Settings(
            env="test",
            oidc_issuer="http://localhost:8081/realms/firebid",
            oidc_jwks_url="http://keycloak:8080/realms/firebid/protocol/openid-connect/certs",
        )
        assert get_token_verifier(settings).jwks_url.startswith("http://keycloak:8080/")

    def test_the_same_settings_give_the_same_verifier(self) -> None:
        settings = Settings(env="test", oidc_issuer="https://login.example/tenant/v2.0")
        assert get_token_verifier(settings) is get_token_verifier(settings)
