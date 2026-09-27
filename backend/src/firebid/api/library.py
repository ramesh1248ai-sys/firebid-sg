"""The organisation's libraries: canonical object types and consultant symbol mappings
(FR-ADM-02).

Anyone signed in may read them and their history. Changing the object library needs
`object_library.change`; every change, deprecation included, is a new version. Mappings are
decided on a bid's mapping screen, where the drawings are; here they are listed with their
history, per consultant.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from firebid.api.deps import CurrentPrincipal, DbSession, require
from firebid.api.symbols import MappingOut, mapping_out
from firebid.auth.permissions import Action
from firebid.auth.provisioning import Principal
from firebid.db.models.symbols import ObjectType, SymbolMapping
from firebid.services import object_library
from firebid.services import symbols as mapping_service
from firebid.services.object_library import LibraryError, TypeSpec

router = APIRouter(prefix="/library", tags=["library"])


class ObjectTypeOut(BaseModel):
    key: str
    version: int
    label: str
    category: str
    measure: str
    attribute_schema: dict[str, Any]
    deprecated: bool
    change_note: str | None
    changed_by: str | None
    created_at: str


class ObjectTypeCreate(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,79}$")
    label: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=40)
    measure: str = Field(pattern=r"^(count|length|none)$")
    attribute_schema: dict[str, Any] = Field(default_factory=dict)
    note: str | None = Field(default=None, max_length=2000)


class ObjectTypeChange(BaseModel):
    label: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=40)
    measure: str | None = Field(default=None, pattern=r"^(count|length|none)$")
    attribute_schema: dict[str, Any] | None = None
    deprecate: bool = False
    restore: bool = False
    note: str | None = Field(default=None, max_length=2000)


class ConsultantOut(BaseModel):
    consultant: str
    consultant_key: str
    confirmed: int
    proposed: int


def type_out(row: ObjectType) -> ObjectTypeOut:
    return ObjectTypeOut(
        key=row.key,
        version=row.version,
        label=row.label,
        category=row.category,
        measure=row.measure,
        attribute_schema=row.attribute_schema,
        deprecated=row.deprecated_at is not None,
        change_note=row.change_note,
        changed_by=row.changed_by,
        created_at=row.created_at.isoformat(),
    )


@router.get("/object-types", response_model=list[ObjectTypeOut])
def list_object_types(principal: CurrentPrincipal, session: DbSession) -> list[ObjectTypeOut]:
    """The current version of every type, deprecated ones included and marked."""
    object_library.ensure_seeded(session, principal.organisation_id)
    return [type_out(row) for row in object_library.current(session, principal.organisation_id)]


@router.get("/object-types/{key}/history", response_model=list[ObjectTypeOut])
def object_type_history(
    key: str, principal: CurrentPrincipal, session: DbSession
) -> list[ObjectTypeOut]:
    rows = object_library.history(session, principal.organisation_id, key)
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such object type")
    return [type_out(row) for row in rows]


@router.get("/object-types/{key}/versions/{number}", response_model=ObjectTypeOut)
def object_type_version(
    key: str, number: int, principal: CurrentPrincipal, session: DbSession
) -> ObjectTypeOut:
    row = object_library.version(session, principal.organisation_id, key, number)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such version")
    return type_out(row)


@router.post("/object-types", response_model=ObjectTypeOut, status_code=201)
def add_object_type(
    body: ObjectTypeCreate,
    session: DbSession,
    principal: Annotated[Principal, require(Action.OBJECT_LIBRARY_CHANGE)],
) -> ObjectTypeOut:
    object_library.ensure_seeded(session, principal.organisation_id)
    spec = TypeSpec(body.key, body.label, body.category, body.measure, body.attribute_schema)
    try:
        row = object_library.create(
            session, principal.organisation_id, spec, principal.actor(), body.note
        )
    except LibraryError as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from refusal
    return type_out(row)


@router.post("/object-types/{key}", response_model=ObjectTypeOut)
def change_object_type(
    key: str,
    body: ObjectTypeChange,
    session: DbSession,
    principal: Annotated[Principal, require(Action.OBJECT_LIBRARY_CHANGE)],
) -> ObjectTypeOut:
    """A new version of the type: edited, deprecated or restored."""
    try:
        row = object_library.change(
            session,
            principal.organisation_id,
            key,
            principal.actor(),
            label=body.label,
            category=body.category,
            measure=body.measure,
            attributes=body.attribute_schema,
            note=body.note,
            deprecate=body.deprecate,
            restore=body.restore,
        )
    except LibraryError as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from refusal
    return type_out(row)


@router.get("/consultants", response_model=list[ConsultantOut])
def consultants(principal: CurrentPrincipal, session: DbSession) -> list[ConsultantOut]:
    """Every consultant with mappings, and how many are confirmed."""
    totals: dict[str, ConsultantOut] = {}
    for row in mapping_service.current_mappings(session, principal.organisation_id):
        item = totals.setdefault(
            row.consultant_key,
            ConsultantOut(
                consultant=row.consultant,
                consultant_key=row.consultant_key,
                confirmed=0,
                proposed=0,
            ),
        )
        if row.state == "confirmed":
            item.confirmed += 1
        elif row.state == "proposed":
            item.proposed += 1
    return sorted(totals.values(), key=lambda item: item.consultant)


@router.get("/mappings", response_model=list[MappingOut])
def list_mappings(
    principal: CurrentPrincipal,
    session: DbSession,
    consultant_key: Annotated[str, Query(min_length=1, max_length=200)],
) -> list[MappingOut]:
    """One consultant's mappings, latest version of each."""
    rows = mapping_service.current_mappings(session, principal.organisation_id, consultant_key)
    return [mapping_out(row) for row in rows]


@router.get("/mappings/{lineage_id}/history", response_model=list[MappingOut])
def library_mapping_history(
    lineage_id: str, principal: CurrentPrincipal, session: DbSession
) -> list[MappingOut]:
    import uuid

    try:
        lineage = uuid.UUID(lineage_id)
    except ValueError as bad:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such mapping") from bad
    rows = [
        row
        for row in session.execute(
            select(SymbolMapping)
            .where(
                SymbolMapping.lineage_id == lineage,
                SymbolMapping.organisation_id == principal.organisation_id,
            )
            .order_by(SymbolMapping.version)
        ).scalars()
    ]
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such mapping")
    return [mapping_out(row) for row in rows]
