"""Shared API dependencies: the session, who is calling, and what they may reach.

Bid-owned routes take :func:`get_bid_context`, which resolves the bid and checks membership.
A non-member gets 404, not 403, so the API never confirms that another client's bid exists.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Path, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from firebid.auth.claims import ClaimsError, map_claims
from firebid.auth.permissions import Action, may
from firebid.auth.provisioning import Principal, default_organisation, is_member, provision
from firebid.auth.tokens import TokenError, get_token_verifier
from firebid.db.engine import session_scope
from firebid.db.identity import set_transaction_identity
from firebid.db.models.core import Bid
from firebid.settings import Settings, get_settings

bearer_scheme = HTTPBearer(auto_error=False, description="OIDC access token")


def get_session() -> Iterator[Session]:
    """One database session per request, committed on success."""
    with session_scope() as session:
        yield session


def settings_dep() -> Settings:
    return get_settings()


def get_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(settings_dep)],
) -> Principal:
    """Verify the token, provision the person, and set the acting user on this transaction."""
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sign-in required")
    try:
        claims = get_token_verifier(settings).verify(credentials.credentials)
        identity = map_claims(claims)
    except (TokenError, ClaimsError) as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(error)) from error

    organisation = default_organisation(session, settings.organisation_name)
    principal = provision(session, identity, organisation)
    set_transaction_identity(session, principal.user_id)
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
DbSession = Annotated[Session, Depends(get_session)]


def require(action: Action) -> object:
    """Dependency factory: the caller must hold a role permitted for ``action``."""

    def check(principal: CurrentPrincipal) -> Principal:
        if not may(principal.roles, action):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"'{action}' is not allowed for you")
        return principal

    return Depends(check)


class BidContext:
    """A bid the caller is a member of."""

    def __init__(self, bid: Bid, principal: Principal) -> None:
        self.bid = bid
        self.principal = principal


def get_bid_context(
    bid_id: Annotated[uuid.UUID, Path()],
    session: DbSession,
    principal: CurrentPrincipal,
) -> BidContext:
    bid = session.get(Bid, bid_id)
    if bid is None or not is_member(session, bid_id, principal.user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "bid not found")
    return BidContext(bid, principal)


CurrentBid = Annotated[BidContext, Depends(get_bid_context)]
