"""Identity and permissions: who is calling, and what they may do."""

from firebid.auth.claims import Identity, map_claims
from firebid.auth.permissions import Action, may, roles_for

__all__ = ["Action", "Identity", "map_claims", "may", "roles_for"]
