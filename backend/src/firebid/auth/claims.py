"""Reading an OIDC token's claims.

Microsoft Entra ID's shape is the one the application knows: `oid` identifies the person,
`preferred_username` is their sign-in name, `roles` holds the application roles. The
development Keycloak realm is configured to issue the same claims, so there is no dev-only
path through this code (ADR-001).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from firebid.domain.state_machines import Role

KNOWN_ROLES = {str(role) for role in Role}


class ClaimsError(ValueError):
    """The token is valid but does not carry the claims the application needs."""


@dataclass(frozen=True)
class Identity:
    """A person as the identity provider describes them."""

    external_id: str
    username: str
    display_name: str
    roles: frozenset[str]

    @property
    def unknown_roles(self) -> frozenset[str]:
        return self.roles - KNOWN_ROLES

    @property
    def application_roles(self) -> frozenset[str]:
        """Roles this application knows. Directory groups it does not use are ignored."""
        return self.roles & KNOWN_ROLES


def _display_name(claims: dict[str, Any], username: str) -> str:
    for key in ("name", "given_name"):
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return username


def map_claims(claims: dict[str, Any]) -> Identity:
    """Turn verified token claims into an :class:`Identity`."""
    external_id = claims.get("oid") or claims.get("sub")
    if not isinstance(external_id, str) or not external_id:
        raise ClaimsError("token has neither 'oid' nor 'sub'")

    username = claims.get("preferred_username") or claims.get("upn") or claims.get("email")
    if not isinstance(username, str) or not username:
        raise ClaimsError("token has no 'preferred_username'")

    raw_roles = claims.get("roles", [])
    if isinstance(raw_roles, str):
        raw_roles = [raw_roles]
    if not isinstance(raw_roles, list):
        raise ClaimsError("'roles' must be a list")

    return Identity(
        external_id=external_id,
        username=username,
        display_name=_display_name(claims, username),
        roles=frozenset(role for role in raw_roles if isinstance(role, str)),
    )
