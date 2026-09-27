"""The canonical object library: seeded once, then edited only by new versions (FR-ADM-02).

Every edit writes a new `object_type` row with the next version and `supersedes_id`, and an
audit event naming who made it. Nothing is updated in place, so the library can be read back
as it stood at any version or moment: a count taken last month can be explained with the
library it was taken with.

Deprecating a type is an edit too: the new version carries `deprecated_at`. A deprecated type
is kept, with its history, and existing mappings to it still resolve, but it is not offered
for new mappings.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from firebid.db.audit import record_event
from firebid.db.models.symbols import ObjectType
from firebid.domain.actors import Actor, AuditContext

SEED = Path(__file__).resolve().parents[3] / "config" / "object_library.yaml"
MEASURES = ("count", "length", "none")
ATTRIBUTE_TYPES = ("enum", "number", "text")


class LibraryError(ValueError):
    """An edit the library refuses: unknown key, bad schema, a deprecated type."""


@dataclass(frozen=True)
class TypeSpec:
    key: str
    label: str
    category: str
    measure: str
    attributes: dict[str, Any]


def seed_types(path: Path = SEED) -> list[TypeSpec]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    specs = [
        TypeSpec(
            key=item["key"],
            label=item["label"],
            category=item["category"],
            measure=item["measure"],
            attributes=dict(item.get("attributes") or {}),
        )
        for item in data["types"]
    ]
    for spec in specs:
        _check(spec.measure, spec.attributes)
    return specs


def _check(measure: str, attributes: dict[str, Any]) -> None:
    if measure not in MEASURES:
        raise LibraryError(f"measure must be one of {', '.join(MEASURES)}, not {measure!r}")
    for name, rule in attributes.items():
        if not isinstance(rule, dict) or rule.get("type") not in ATTRIBUTE_TYPES:
            raise LibraryError(f"attribute {name!r} needs a type: {', '.join(ATTRIBUTE_TYPES)}")
        if rule["type"] == "enum" and not rule.get("values"):
            raise LibraryError(f"attribute {name!r} is an enum with no values")


def ensure_seeded(session: Session, organisation_id: uuid.UUID) -> None:
    """Give an organisation its library the first time it is needed."""
    exists = session.execute(
        select(ObjectType.id).where(ObjectType.organisation_id == organisation_id).limit(1)
    ).first()
    if exists:
        return
    for spec in seed_types():
        session.add(
            ObjectType(
                # The clock, not the transaction's start: versions made in one transaction
                # must still be ordered in time for `current(as_of=...)`.
                created_at=datetime.now(UTC),
                organisation_id=organisation_id,
                key=spec.key,
                version=1,
                label=spec.label,
                category=spec.category,
                measure=spec.measure,
                attribute_schema=spec.attributes,
                change_note="seeded from config/object_library.yaml",
                changed_by="platform",
            )
        )
    session.flush()


def current(
    session: Session, organisation_id: uuid.UUID, *, as_of: datetime | None = None
) -> list[ObjectType]:
    """The latest version of every type (at `as_of`, when given), deprecated ones included."""
    latest = select(ObjectType.key, func.max(ObjectType.version).label("version")).where(
        ObjectType.organisation_id == organisation_id
    )
    if as_of is not None:
        latest = latest.where(ObjectType.created_at <= as_of)
    newest = latest.group_by(ObjectType.key).subquery()
    return list(
        session.execute(
            select(ObjectType)
            .join(
                newest,
                (ObjectType.key == newest.c.key) & (ObjectType.version == newest.c.version),
            )
            .where(ObjectType.organisation_id == organisation_id)
            .order_by(ObjectType.category, ObjectType.key)
        ).scalars()
    )


def usable(session: Session, organisation_id: uuid.UUID) -> list[ObjectType]:
    """The types a symbol may be mapped to now: the current ones, not deprecated."""
    return [item for item in current(session, organisation_id) if item.deprecated_at is None]


def latest(session: Session, organisation_id: uuid.UUID, key: str) -> ObjectType | None:
    return (
        session.execute(
            select(ObjectType)
            .where(ObjectType.organisation_id == organisation_id, ObjectType.key == key)
            .order_by(ObjectType.version.desc())
        )
        .scalars()
        .first()
    )


def history(session: Session, organisation_id: uuid.UUID, key: str) -> list[ObjectType]:
    """Every version of one type, oldest first."""
    return list(
        session.execute(
            select(ObjectType)
            .where(ObjectType.organisation_id == organisation_id, ObjectType.key == key)
            .order_by(ObjectType.version)
        ).scalars()
    )


def version(
    session: Session, organisation_id: uuid.UUID, key: str, number: int
) -> ObjectType | None:
    return session.execute(
        select(ObjectType).where(
            ObjectType.organisation_id == organisation_id,
            ObjectType.key == key,
            ObjectType.version == number,
        )
    ).scalar_one_or_none()


def create(
    session: Session,
    organisation_id: uuid.UUID,
    spec: TypeSpec,
    actor: Actor,
    note: str | None = None,
) -> ObjectType:
    if latest(session, organisation_id, spec.key) is not None:
        raise LibraryError(f"{spec.key!r} is already in the library; edit it instead")
    _check(spec.measure, spec.attributes)
    row = ObjectType(
        created_at=datetime.now(UTC),
        organisation_id=organisation_id,
        key=spec.key,
        version=1,
        label=spec.label,
        category=spec.category,
        measure=spec.measure,
        attribute_schema=spec.attributes,
        change_note=note,
        changed_by=actor.label[:200],
        created_by_id=actor.id,
    )
    session.add(row)
    session.flush()
    _audit(session, organisation_id, actor, "object library: added", row, {}, _state(row), note)
    return row


def change(
    session: Session,
    organisation_id: uuid.UUID,
    key: str,
    actor: Actor,
    *,
    label: str | None = None,
    category: str | None = None,
    measure: str | None = None,
    attributes: dict[str, Any] | None = None,
    note: str | None = None,
    deprecate: bool = False,
    restore: bool = False,
) -> ObjectType:
    """A new version of `key` with the given changes. The previous version is untouched."""
    previous = latest(session, organisation_id, key)
    if previous is None:
        raise LibraryError(f"{key!r} is not in the library")
    if previous.deprecated_at is not None and not (restore or deprecate):
        raise LibraryError(f"{key!r} is deprecated; restore it before editing it")
    new_measure = measure or previous.measure
    new_attributes = attributes if attributes is not None else dict(previous.attribute_schema)
    _check(new_measure, new_attributes)
    row = ObjectType(
        created_at=datetime.now(UTC),
        organisation_id=organisation_id,
        key=key,
        version=previous.version + 1,
        label=label or previous.label,
        category=category or previous.category,
        measure=new_measure,
        attribute_schema=new_attributes,
        deprecated_at=(
            datetime.now(UTC) if deprecate else None if restore else previous.deprecated_at
        ),
        supersedes_id=previous.id,
        change_note=note,
        changed_by=actor.label[:200],
        created_by_id=actor.id,
    )
    session.add(row)
    session.flush()
    action = (
        "object library: deprecated"
        if deprecate
        else "object library: restored"
        if restore
        else "object library: changed"
    )
    _audit(session, organisation_id, actor, action, row, _state(previous), _state(row), note)
    return row


def _state(row: ObjectType) -> dict[str, Any]:
    return {
        "key": row.key,
        "version": row.version,
        "label": row.label,
        "category": row.category,
        "measure": row.measure,
        "attributes": row.attribute_schema,
        "deprecated": row.deprecated_at is not None,
    }


def _audit(
    session: Session,
    organisation_id: uuid.UUID,
    actor: Actor,
    action: str,
    row: ObjectType,
    before: dict[str, Any],
    after: dict[str, Any],
    note: str | None,
) -> None:
    record_event(
        session,
        context=AuditContext(organisation_id=organisation_id),
        actor=actor,
        action=action,
        entity_type=ObjectType.__tablename__,
        entity_id=row.id,
        before=before,
        after=after,
        reason=note,
    )
