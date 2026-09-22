"""Who is acting. Every audit event names an actor, and transitions check their roles."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from firebid.domain.state_machines import Role


@dataclass(frozen=True)
class Actor:
    """A person, or the platform acting on its own."""

    label: str
    roles: frozenset[str]
    id: uuid.UUID | None = None

    @classmethod
    def system(cls, label: str = "platform") -> Actor:
        return cls(label=label, roles=frozenset({str(Role.SYSTEM)}))


SYSTEM_ACTOR = Actor.system()


@dataclass(frozen=True)
class AuditContext:
    """The scope an event belongs to: an organisation, and a bid where there is one."""

    organisation_id: uuid.UUID
    bid_id: uuid.UUID | None = None
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def chain_key(self) -> uuid.UUID:
        """Events are chained per bid; organisation-level events use their own chain."""
        return self.bid_id or self.organisation_id
