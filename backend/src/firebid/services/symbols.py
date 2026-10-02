"""Legends, symbol mappings and instances: every symbol maps to one object, or is raised
(FR-VIS-02, FR-ADM-02).

In the parse job, after views, each sheet's legends are read and its symbols found. A
legend row is resolved against the consultant's mappings first:

* a confirmed mapping with the same signature and description is **reused**: nothing to
  confirm (the whole point of remembering a consultant);
* the same signature with a different description is a **changed** symbol: a proposal for
  this project only, so the consultant's confirmed mapping is not disturbed;
* a mapping already proposed (by another sheet of this tender) is shared, not duplicated;
* otherwise the keyword rules propose a type, and failing them the model is asked, on the
  ordinary worker.

Every proposal is a new `symbol_mapping` lineage in state `proposed`. Only a person confirms
it (guardrail 2), and each confirmation, correction or rejection is a new version.

A symbol instance is matched to a legend row of the tender, or directly to one of the
consultant's mappings. What it is is looked up when counting, never stored on it, so
confirming a mapping takes effect at once. `counts` includes only instances whose mapping is
confirmed, to a type that is counted, on a Current sheet; everything else is listed under
`unmapped`, never dropped.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import Text, cast, delete, func, select, update
from sqlalchemy.orm import Session

from firebid.db.audit import record_event
from firebid.db.models.core import Bid, Project
from firebid.db.models.documents import Sheet
from firebid.db.models.drawings import SheetGeometry, SheetView
from firebid.db.models.symbols import LegendEntry, SymbolInstance, SymbolMapping
from firebid.domain.actors import Actor, AuditContext
from firebid.drawings import crops, legends, symbol_rules, symbols
from firebid.drawings.symbols import Signature
from firebid.services import geometry as geometry_service
from firebid.services import object_library
from firebid.storage.object_store import ObjectExists, ObjectStore

log = structlog.get_logger("firebid.symbols")

DETECTOR_VERSION = "1"
PNG = "image/png"

# Legend entry statuses.
REUSED, PROPOSED, AWAITING_MODEL, CONFIRMED, REJECTED = (
    "reused",
    "proposed",
    "awaiting_model",
    "confirmed",
    "rejected",
)

_SUFFIXES = re.compile(
    r"\(S\)|\b(PTE\.?|PRIVATE|LTD\.?|LIMITED|LLP|INC\.?|CORP\.?|CO\.?|SINGAPORE)\b"
)


def consultant_key(name: str) -> str:
    """ "Alpha Consultants Pte. Ltd." and "ALPHA CONSULTANTS" are one consultant."""
    text = _SUFFIXES.sub(" ", name.upper())
    text = re.sub(r"[^A-Z0-9&]+", " ", text)
    return " ".join(text.split())


@dataclass(frozen=True)
class Consultant:
    key: str
    name: str
    organisation_id: uuid.UUID
    project_id: uuid.UUID


def consultant_of(session: Session, bid_id: uuid.UUID) -> Consultant:
    """The consultant a bid's drawings come from: the project's. With none named, mappings
    stay with this bid, so no symbol is reused across consultants by accident."""
    bid = session.get(Bid, bid_id)
    if bid is None:
        raise ValueError("no such bid")
    project = session.get(Project, bid.project_id)
    name = (project.consultant if project else None) or ""
    key = consultant_key(name) if name.strip() else f"BID {bid.id}"
    return Consultant(
        key, name.strip() or "Unnamed consultant", bid.organisation_id, bid.project_id
    )


# --- Mappings -------------------------------------------------------------------------------


def current_mappings(
    session: Session, organisation_id: uuid.UUID, consultant: str | None = None
) -> list[SymbolMapping]:
    """The latest version of every mapping lineage, for one consultant when given."""
    newest = (
        select(SymbolMapping.lineage_id, func.max(SymbolMapping.version).label("version"))
        .where(SymbolMapping.organisation_id == organisation_id)
        .group_by(SymbolMapping.lineage_id)
        .subquery()
    )
    query = select(SymbolMapping).join(
        newest,
        (SymbolMapping.lineage_id == newest.c.lineage_id)
        & (SymbolMapping.version == newest.c.version),
    )
    if consultant is not None:
        query = query.where(SymbolMapping.consultant_key == consultant)
    return list(session.execute(query.order_by(SymbolMapping.created_at)).scalars())


def current(session: Session, lineage_id: uuid.UUID) -> SymbolMapping | None:
    return (
        session.execute(
            select(SymbolMapping)
            .where(SymbolMapping.lineage_id == lineage_id)
            .order_by(SymbolMapping.version.desc())
        )
        .scalars()
        .first()
    )


def history(session: Session, lineage_id: uuid.UUID) -> list[SymbolMapping]:
    return list(
        session.execute(
            select(SymbolMapping)
            .where(SymbolMapping.lineage_id == lineage_id)
            .order_by(SymbolMapping.version)
        ).scalars()
    )


def _usable_for(mappings: list[SymbolMapping], project_id: uuid.UUID) -> list[SymbolMapping]:
    """This project's overrides first, then the consultant's own; rejected ones never."""
    live = [m for m in mappings if m.state != REJECTED and m.project_id in (None, project_id)]
    return sorted(live, key=lambda m: (m.project_id is None, m.created_at))


def _propose(
    session: Session,
    consultant: Consultant,
    signature: Signature,
    description: str,
    *,
    object_type: str | None,
    source: str,
    provenance: dict[str, Any],
    attributes: dict[str, Any] | None = None,
    project_only: bool = False,
) -> SymbolMapping:
    row = SymbolMapping(
        created_at=datetime.now(UTC),
        organisation_id=consultant.organisation_id,
        lineage_id=uuid.uuid4(),
        version=1,
        consultant_key=consultant.key,
        consultant=consultant.name,
        project_id=consultant.project_id if project_only else None,
        signature=signature.as_json(),
        description=description[:300],
        object_type_key=object_type,
        attributes=attributes or {},
        state=PROPOSED,
        source=source,
        provenance=provenance,
    )
    session.add(row)
    session.flush()
    return row


def _next_version(previous: SymbolMapping, **changes: Any) -> SymbolMapping:
    values = {
        "organisation_id": previous.organisation_id,
        "lineage_id": previous.lineage_id,
        "version": previous.version + 1,
        "supersedes_id": previous.id,
        "consultant_key": previous.consultant_key,
        "consultant": previous.consultant,
        "project_id": previous.project_id,
        "signature": previous.signature,
        "description": previous.description,
        "object_type_key": previous.object_type_key,
        "attributes": dict(previous.attributes or {}),
        "state": previous.state,
        "source": previous.source,
        "provenance": dict(previous.provenance or {}),
        "confirmed_by": previous.confirmed_by,
        "confirmed_by_id": previous.confirmed_by_id,
        "confirmed_at": previous.confirmed_at,
    }
    values.update(changes)
    return SymbolMapping(created_at=datetime.now(UTC), **values)


class MappingError(ValueError):
    """A decision the mapping library refuses: unknown type, deprecated type, no such row."""


def confirm(
    session: Session,
    lineage_id: uuid.UUID,
    actor: Actor,
    *,
    object_type: str | None = None,
    attributes: dict[str, Any] | None = None,
    note: str | None = None,
    bid_id: uuid.UUID | None = None,
) -> SymbolMapping:
    """A person says what the symbol is: the proposal as it stands, or corrected.

    `bid_id` is the bid the decision was made on, which the audit event is recorded under.
    """
    previous = current(session, lineage_id)
    if previous is None:
        raise MappingError("no such mapping")
    chosen = object_type or previous.object_type_key
    if not chosen:
        raise MappingError("say which object type this symbol is before confirming it")
    usable = {item.key for item in object_library.usable(session, previous.organisation_id)}
    if chosen not in usable:
        raise MappingError(f"{chosen!r} is not a current type in the object library")
    corrected = chosen != previous.object_type_key or (
        attributes is not None and attributes != previous.attributes
    )
    row = _next_version(
        previous,
        object_type_key=chosen,
        attributes=attributes if attributes is not None else dict(previous.attributes or {}),
        state=CONFIRMED,
        source="person" if corrected else previous.source,
        confirmed_by=actor.label[:200],
        confirmed_by_id=actor.id,
        confirmed_at=datetime.now(UTC),
        change_note=note,
        created_by_id=actor.id,
    )
    session.add(row)
    session.flush()
    _audit(
        session,
        actor,
        "symbol mapping: " + ("corrected" if corrected else "confirmed"),
        previous,
        row,
        note,
        bid_id,
    )
    _settle_entries(session, lineage_id, CONFIRMED)
    return row


def reject(
    session: Session,
    lineage_id: uuid.UUID,
    actor: Actor,
    note: str | None = None,
    bid_id: uuid.UUID | None = None,
) -> SymbolMapping:
    """A person says the proposal is wrong and gives no answer yet: it stays unmapped."""
    previous = current(session, lineage_id)
    if previous is None:
        raise MappingError("no such mapping")
    row = _next_version(
        previous,
        state=REJECTED,
        change_note=note,
        created_by_id=actor.id,
        confirmed_by=None,
        confirmed_by_id=None,
        confirmed_at=None,
    )
    session.add(row)
    session.flush()
    _audit(session, actor, "symbol mapping: rejected", previous, row, note, bid_id)
    _settle_entries(session, lineage_id, REJECTED)
    return row


def name_unlisted(
    session: Session,
    bid_id: uuid.UUID,
    symbol_key: str,
    object_type: str,
    actor: Actor,
    note: str | None = None,
) -> SymbolMapping:
    """A recurring symbol no legend explains (a grid bubble, a north point): a person says
    what it is, often "not an installed object" (P1-08).

    It becomes a confirmed mapping for the consultant, like a legend row's, so the same symbol
    is known on every sheet and later bid, and it stops being raised as unmapped.
    """
    instance = (
        session.execute(
            select(SymbolInstance).where(
                SymbolInstance.bid_id == bid_id,
                SymbolInstance.symbol_key == symbol_key,
                SymbolInstance.mapping_lineage_id.is_(None),
            )
        )
        .scalars()
        .first()
    )
    if instance is None:
        raise MappingError("no unlisted symbol with that key on this bid")
    consultant = consultant_of(session, bid_id)
    proposed = _propose(
        session,
        consultant,
        Signature.from_json(instance.signature),
        f"Unlisted symbol ({instance.block or 'drawn shape'})",
        object_type=object_type,
        source="person",
        provenance={"symbol_key": symbol_key, "named_on_bid": str(bid_id)},
    )
    row = confirm(
        session, proposed.lineage_id, actor, object_type=object_type, note=note, bid_id=bid_id
    )
    # A new mapping cannot reach a symbol already matched to a legend row: rows are tried
    # first, and none has changed. Only the rest are matched again.
    match_instances(session, bid_id, consultant, beyond_legends_only=True)
    return row


def _settle_entries(session: Session, lineage_id: uuid.UUID, status: str) -> None:
    for entry in session.execute(
        select(LegendEntry).where(LegendEntry.mapping_lineage_id == lineage_id)
    ).scalars():
        entry.status = status
    session.flush()


def _audit(
    session: Session,
    actor: Actor,
    action: str,
    before: SymbolMapping,
    after: SymbolMapping,
    note: str | None,
    bid_id: uuid.UUID | None = None,
) -> None:
    def state(row: SymbolMapping) -> dict[str, Any]:
        return {
            "version": row.version,
            "state": row.state,
            "object_type": row.object_type_key,
            "attributes": row.attributes,
            "consultant": row.consultant,
        }

    record_event(
        session,
        context=AuditContext(organisation_id=after.organisation_id, bid_id=bid_id),
        actor=actor,
        action=action,
        entity_type=SymbolMapping.__tablename__,
        entity_id=after.id,
        before=state(before),
        after=state(after),
        reason=note,
    )


# --- Reading a sheet ------------------------------------------------------------------------


@dataclass
class SheetSymbols:
    entries: list[LegendEntry] = field(default_factory=list)
    instances: int = 0
    awaiting_model: list[uuid.UUID] = field(default_factory=list)


def read_sheet(
    session: Session,
    store: ObjectStore,
    record: SheetGeometry,
    consultant: Consultant,
    *,
    match: bool = True,
    shapes: Shapes | None = None,
) -> SheetSymbols:
    """A sheet's legends and symbols, recorded again from its geometry (idempotent).

    `match=False` leaves matching the bid's instances to the caller, who reads several
    sheets and matches once after the last (`read_all`). `shapes` are the sheet's legends
    and symbols when they were found ahead, in parallel (`read_all`).
    """
    page = (record.page[0], record.page[1], record.page[2], record.page[3])
    table: Any = None
    if shapes is None:
        table = geometry_service.load(store, record)
        shapes = _shapes_of(table, page)
    found, placed = shapes
    outcome = SheetSymbols()
    if table is None and any(legend.rows for legend in found):
        table = geometry_service.load(store, record)  # each legend row is cropped from it

    session.execute(delete(LegendEntry).where(LegendEntry.sheet_id == record.sheet_id))
    session.execute(delete(SymbolInstance).where(SymbolInstance.sheet_id == record.sheet_id))
    session.flush()

    mappings = _usable_for(
        current_mappings(session, consultant.organisation_id, consultant.key),
        consultant.project_id,
    )
    ordinal = 0
    for legend in found:
        for row in legend.rows:
            if row.symbol.signature is None:
                continue
            entry = LegendEntry(
                bid_id=record.bid_id,
                sheet_id=record.sheet_id,
                ordinal=ordinal,
                heading=legend.heading[:80],
                description=row.description[:300],
                symbol_box=[round(v, 3) for v in row.symbol.box],
                row_box=[round(v, 3) for v in row.row_box],
                signature=row.symbol.signature.as_json(),
                crop_key=_store_crop(store, table, row.row_box),
                status=AWAITING_MODEL,
            )
            ordinal += 1
            session.add(entry)
            session.flush()
            _resolve(session, entry, row.symbol.signature, consultant, mappings)
            if entry.status == AWAITING_MODEL:
                outcome.awaiting_model.append(entry.id)
            elif entry.mapping_lineage_id is not None:
                mapping = current(session, entry.mapping_lineage_id)
                if mapping is not None and mapping not in mappings:
                    mappings.append(mapping)
            outcome.entries.append(entry)

    outcome.instances = _record_instances(session, record, placed)
    if match:
        match_instances(session, record.bid_id, consultant)
    session.flush()
    log.info(
        "symbols_read",
        sheet_id=str(record.sheet_id),
        legend_rows=len(outcome.entries),
        instances=outcome.instances,
        awaiting_model=len(outcome.awaiting_model),
    )
    return outcome


def _store_crop(store: ObjectStore, table: Any, box: tuple[float, float, float, float]) -> str:
    image = crops.render(table, box)
    key = f"symbols/crops/{hashlib.sha256(image).hexdigest()}.png"
    with contextlib.suppress(ObjectExists):  # the same row, cropped by an earlier run
        store.put_once(key, image, content_type=PNG)
    return key


def _same_words(a: str | None, b: str | None) -> bool:
    return symbol_rules.normalise(a or "") == symbol_rules.normalise(b or "")


def _resolve(
    session: Session,
    entry: LegendEntry,
    signature: Signature,
    consultant: Consultant,
    mappings: list[SymbolMapping],
) -> None:
    candidates = [Signature.from_json(m.signature) for m in mappings]
    found = symbols.best_match(signature, candidates) if candidates else None
    if found is not None:
        mapping = mappings[found.index]
        if mapping.state == CONFIRMED and _same_words(mapping.description, entry.description):
            entry.mapping_lineage_id, entry.status = mapping.lineage_id, REUSED
            return
        if mapping.state == PROPOSED:
            entry.mapping_lineage_id, entry.status = mapping.lineage_id, PROPOSED
            return
        # The same symbol described differently: changed, so a person looks again. The
        # consultant's confirmed mapping stands; this tender gets its own proposal.
        proposal = _propose(
            session,
            consultant,
            signature,
            entry.description,
            object_type=mapping.object_type_key,
            attributes=dict(mapping.attributes or {}),
            source="reuse",
            provenance={
                "reason": "the consultant's symbol, described differently on this tender",
                "previous_lineage_id": str(mapping.lineage_id),
                "previous_description": mapping.description,
                "match": found.how,
                "distance": round(found.distance, 4),
            },
            project_only=True,
        )
        entry.mapping_lineage_id, entry.status = proposal.lineage_id, PROPOSED
        return

    rule = symbol_rules.load().propose(entry.description)
    if rule is not None:
        proposal = _propose(
            session,
            consultant,
            signature,
            entry.description,
            object_type=rule.object_type,
            source="rule",
            provenance={"rule": rule.rule, "rule_version": rule.rule_version},
        )
        entry.mapping_lineage_id, entry.status = proposal.lineage_id, PROPOSED
        return
    entry.status = AWAITING_MODEL


Shapes = tuple[list[legends.Legend], list[symbols.Cluster]]


def _shapes_of(table: Any, page: tuple[float, float, float, float]) -> Shapes:
    """A sheet's legends, and every symbol installed on it: outside its legends and its
    title block. Pure work on the geometry, no database."""
    from firebid.drawings.geometry import texts
    from firebid.drawings.views import _title_block_region

    # The shapes are found once: the legends and the installed symbols are both read from them.
    shapes = symbols.candidates(table)
    found = legends.detect(table, page, shapes)
    excluded = [legend.box for legend in found]
    region = _title_block_region(texts(table), page)
    if region is not None:
        excluded.append((region.x0, region.y0, region.x1, region.y1))
    return found, symbols.clusters(table, excluding=excluded, found=shapes)


def sheet_shapes(parquet: bytes, page: tuple[float, float, float, float]) -> Shapes:
    """`_shapes_of` from the geometry file itself: what a worker process is given."""
    from firebid.drawings.geometry import from_parquet

    return _shapes_of(from_parquet(parquet), page)


def _shapes_ahead(store: ObjectStore, records: list[SheetGeometry]) -> dict[uuid.UUID, Shapes]:
    """Every sheet's shapes, several sheets at a time (NFR-01; P1-11).

    Finding symbols is most of reading a sheet, and it needs only the sheet's own geometry,
    so worker processes do it side by side, as many as `parse_concurrency` allows. The job
    then records each sheet in turn. A sheet that fails here is read again there, where the
    failure is reported.
    """
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    from firebid.settings import get_settings

    workers = min(max(1, get_settings().parse_concurrency), len(records))
    if workers <= 1:
        return {}
    shapes: dict[uuid.UUID, Shapes] = {}
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=workers, mp_context=context) as pool:
        pending = {
            record.sheet_id: pool.submit(
                sheet_shapes,
                store.get(record.object_key),
                (record.page[0], record.page[1], record.page[2], record.page[3]),
            )
            for record in records
        }
        for sheet_id, future in pending.items():
            try:
                shapes[sheet_id] = future.result()
            except Exception as failure:  # read again in the job, which reports why
                log.warning("symbols_ahead_failed", sheet_id=str(sheet_id), reason=str(failure))
    return shapes


def _record_instances(
    session: Session, record: SheetGeometry, placed: list[symbols.Cluster]
) -> int:
    """Every symbol installed on the sheet, with the geometry rows it is made of."""
    views = list(
        session.execute(select(SheetView).where(SheetView.sheet_id == record.sheet_id)).scalars()
    )
    for cluster in placed:
        if cluster.signature is None:  # clusters() returns only signed clusters
            continue
        cx, cy = cluster.centre
        view = next(
            (
                v
                for v in views
                if v.extent[0] <= cx <= v.extent[2] and v.extent[1] <= cy <= v.extent[3]
            ),
            None,
        )
        session.add(
            SymbolInstance(
                bid_id=record.bid_id,
                sheet_id=record.sheet_id,
                view_id=view.id if view else None,
                symbol_key="unmatched",
                block=cluster.block,
                cx=round(cx, 3),
                cy=round(cy, 3),
                bbox=[round(v, 3) for v in cluster.box],
                rotation=cluster.rotation,
                scale=cluster.scale,
                signature=cluster.signature.as_json(),
                geometry_rows=list(cluster.rows),
                detector_version=DETECTOR_VERSION,
            )
        )
    session.flush()
    return len(placed)


def match_instances(
    session: Session,
    bid_id: uuid.UUID,
    consultant: Consultant,
    *,
    beyond_legends_only: bool = False,
) -> None:
    """Match the bid's instances to its legend rows, else to the consultant's mappings.

    Run after every document, because files arrive in any order: a plan read before its
    legend sheet is matched again once the legend is known. Once per document, not per
    sheet: every instance of the bid is matched again, so per sheet it grew with the square
    of the sheet count (P1-11).

    `beyond_legends_only` matches again only the instances no legend row claimed, for when
    a mapping was added and the legend rows are what they were: an instance matched to a
    row matches it still, so reading it again changes nothing. On a real tender that is a
    seventh of the instances.
    """
    entries = list(
        session.execute(select(LegendEntry).where(LegendEntry.bid_id == bid_id)).scalars()
    )
    entry_signatures = symbols.Candidates([Signature.from_json(e.signature) for e in entries])
    mappings = _usable_for(
        current_mappings(session, consultant.organisation_id, consultant.key),
        consultant.project_id,
    )
    mapping_signatures = symbols.Candidates([Signature.from_json(m.signature) for m in mappings])
    unknown: tuple[symbols.Candidates, list[str]] = (symbols.Candidates(), [])

    def matched(signature: Signature) -> Matched:
        """What a symbol of this shape is matched to: a legend row, else a mapping, else
        the group of unexplained symbols it belongs to."""
        found = symbols.best_match(signature, entry_signatures) if entries else None
        if found is not None:
            entry = entries[found.index]
            return (
                entry.id,
                entry.mapping_lineage_id,
                f"legend:{entry.id}",
                round(found.distance, 4),
            )
        found = symbols.best_match(signature, mapping_signatures) if mappings else None
        if found is not None:
            mapping = mappings[found.index]
            return (
                None,
                mapping.lineage_id,
                f"lineage:{mapping.lineage_id}",
                round(found.distance, 4),
            )
        return (None, None, _unknown_key(signature, unknown), None)

    # A tender draws a few thousand shapes many times each. Each instance is read as its
    # ID, its shape as text and what it is matched to now: not as a whole row, which on a
    # real tender of 150,000 instances was most of the time. Each distinct shape is worked
    # out once, in ID order, and instances that share a shape share the answer.
    #
    # For a legend row or a mapping that is the answer matching each in turn gives: it
    # depends only on the shape. For an unexplained symbol it is deliberately not always:
    # in turn, a later copy of a shape could join a group that did not exist when the first
    # copy was read, so one shape was raised under two keys (131 shapes on that tender).
    # Now a shape is in one group, and the same instances give the same groups every time.
    wanted = select(
        SymbolInstance.id,
        cast(SymbolInstance.signature, Text),
        SymbolInstance.legend_entry_id,
        SymbolInstance.mapping_lineage_id,
        SymbolInstance.symbol_key,
        SymbolInstance.match_distance,
    ).where(SymbolInstance.bid_id == bid_id)
    if beyond_legends_only:
        wanted = wanted.where(SymbolInstance.legend_entry_id.is_(None))
        # What matching again would do for the rest: each takes its row's mapping as it
        # stands now. In one statement, and only where it differs.
        session.execute(
            update(SymbolInstance)
            .where(
                SymbolInstance.bid_id == bid_id,
                SymbolInstance.legend_entry_id == LegendEntry.id,
                SymbolInstance.mapping_lineage_id.is_distinct_from(LegendEntry.mapping_lineage_id),
            )
            .values(mapping_lineage_id=LegendEntry.mapping_lineage_id)
            .execution_options(synchronize_session=False)
        )
    by_shape: dict[str, Matched] = {}
    changed: dict[Matched, list[int]] = {}
    for identity, shape, entry_id, lineage, key, distance in session.execute(
        wanted.order_by(SymbolInstance.id)
    ):
        now = by_shape.get(shape)
        if now is None:
            now = by_shape[shape] = matched(Signature.from_json(json.loads(shape)))
        if now != (entry_id, lineage, key, distance):
            changed.setdefault(now, []).append(identity)
    # Only what changed is written, an answer at a time: every instance now matched to one
    # row, or to one mapping at one distance, in one statement.
    for (entry_id, lineage, key, distance), identities in changed.items():
        for chunk in range(0, len(identities), WRITE_CHUNK):
            session.execute(
                update(SymbolInstance)
                .where(SymbolInstance.id.in_(identities[chunk : chunk + WRITE_CHUNK]))
                .values(
                    legend_entry_id=entry_id,
                    mapping_lineage_id=lineage,
                    symbol_key=key,
                    match_distance=distance,
                )
                .execution_options(synchronize_session=False)
            )
    # Instances already loaded in this session are read again when next used.
    session.expire_all()


# What an instance is matched to: its legend row, its mapping's lineage, its key, how near.
Matched = tuple[uuid.UUID | None, uuid.UUID | None, str, float | None]
# Instances written in one statement.
WRITE_CHUNK = 20_000


def link_instances(session: Session, entry: LegendEntry) -> int:
    """Point the instances matched to a legend row at the mapping the row now has.

    For when only the row's mapping changed (a proposal arrived for it). Which row an
    instance matches depends on the shapes, which have not changed, so nothing needs
    matching again: one statement, where `match_instances` reads every instance of the bid.
    On a 121-sheet tender that was 150,000 instances, once for each of 61 proposals.
    Returns how many instances were updated.
    """
    result = session.execute(
        update(SymbolInstance)
        .where(SymbolInstance.bid_id == entry.bid_id, SymbolInstance.legend_entry_id == entry.id)
        .values(mapping_lineage_id=entry.mapping_lineage_id)
    )
    return int(getattr(result, "rowcount", 0) or 0)


def _unknown_key(signature: Signature, seen: tuple[symbols.Candidates, list[str]]) -> str:
    """Symbols nobody has explained, grouped so recurring ones are raised as one."""
    if signature.block_hash:
        return f"block:{signature.block_hash}"
    shapes, keys = seen
    found = symbols.best_match(signature, shapes)
    if found is not None:
        return keys[found.index]
    key = (
        "shape:"
        + hashlib.sha256(",".join(f"{v:.2f}" for v in signature.descriptor).encode()).hexdigest()[
            :16
        ]
    )
    shapes.append(signature)
    keys.append(key)
    return key


def read_all(
    session: Session, store: ObjectStore, sheets: list[Sheet], *, user_id: str = ""
) -> list[SheetSymbols]:
    """Every sheet of a document, in the parse job. Queues the model for undecided rows.

    The model is asked on the ordinary worker (`symbol.propose`): this job runs in the
    sandbox pool, which has no network.
    """
    if not sheets:
        return []
    from firebid.jobs.enqueue import enqueue
    from firebid.jobs.tasks import propose_symbol

    consultant = consultant_of(session, sheets[0].bid_id)
    object_library.ensure_seeded(session, consultant.organisation_id)
    records = list(
        session.execute(
            select(SheetGeometry).where(SheetGeometry.sheet_id.in_([sheet.id for sheet in sheets]))
        ).scalars()
    )
    ahead = _shapes_ahead(store, records)
    outcomes = [
        read_sheet(
            session, store, record, consultant, match=False, shapes=ahead.get(record.sheet_id)
        )
        for record in records
    ]
    match_instances(session, sheets[0].bid_id, consultant)
    for outcome in outcomes:
        for entry_id in outcome.awaiting_model:
            enqueue(session, propose_symbol, entry_id=str(entry_id), user_id=user_id)
    return outcomes


# --- The model ------------------------------------------------------------------------------


def propose_with_model(
    session: Session, store: ObjectStore, entry: LegendEntry, router: Any
) -> LegendEntry:
    """Ask `symbol_mapper` about a legend row the rules could not decide."""
    from firebid.agents.base import AgentInput
    from firebid.agents.runtime import Escalated, run_agent_with_result
    from firebid.agents.symbol_mapper import LegendRowInput, SymbolMapper, SymbolProposal

    if entry.status != AWAITING_MODEL or entry.crop_key is None:
        return entry
    consultant = consultant_of(session, entry.bid_id)
    signature = Signature.from_json(entry.signature)
    # Another row of this tender may have been answered meanwhile: share its mapping.
    mappings = _usable_for(
        current_mappings(session, consultant.organisation_id, consultant.key),
        consultant.project_id,
    )
    _resolve(session, entry, signature, consultant, mappings)
    if entry.status != AWAITING_MODEL:
        link_instances(session, entry)
        return entry

    choices = [(t.key, t.label) for t in object_library.usable(session, consultant.organisation_id)]
    image = store.get(entry.crop_key)
    request = AgentInput(
        bid_id=entry.bid_id,
        idempotency_key=f"symbol_map:{entry.id}:{hashlib.sha256(image).hexdigest()[:16]}",
        payload=LegendRowInput(
            description=entry.description,
            image_png=image,
            choices=choices,
            consultant=consultant.name,
        ),
    )
    try:
        run, result = run_agent_with_result(session, SymbolMapper(router), request)
    except Escalated as escalation:
        # The model could not answer at all: a person maps the row from scratch.
        proposal = _propose(
            session,
            consultant,
            signature,
            entry.description,
            object_type=None,
            source="model",
            provenance={"error": escalation.reason, "review_task_id": str(escalation.task.id)},
        )
        entry.mapping_lineage_id, entry.status = proposal.lineage_id, PROPOSED
        session.flush()
        return entry
    if result is None or not isinstance(result.output, SymbolProposal):
        return entry
    answer = result.output
    proposal = _propose(
        session,
        consultant,
        signature,
        entry.description,
        object_type=answer.object_type,
        attributes=answer.attributes,
        source="model",
        provenance={
            "provider": run.provider,
            "model": run.model,
            "prompt_version": run.prompt_version,
            "confidence": answer.confidence,
            "reason": answer.reason,
            "agent_run_id": str(run.id),
            "input_hash": request.idempotency_key.rsplit(":", 1)[-1],
        },
    )
    entry.mapping_lineage_id, entry.status = proposal.lineage_id, PROPOSED
    session.flush()
    link_instances(session, entry)
    return entry


# --- Counting -------------------------------------------------------------------------------


@dataclass
class Counted:
    object_type: str
    label: str
    count: int
    sheets: set[uuid.UUID] = field(default_factory=set)


@dataclass
class Unmapped:
    symbol_key: str
    instances: int
    status: str  # awaiting_model | proposed | rejected | no legend
    description: str | None
    block: str | None
    mapping_lineage_id: uuid.UUID | None
    proposed_type: str | None
    sheets: set[uuid.UUID] = field(default_factory=set)


@dataclass
class Counts:
    counted: dict[str, Counted]
    unmapped: list[Unmapped]
    not_objects: int


def counts(session: Session, bid_id: uuid.UUID, *, current_only: bool = True) -> Counts:
    """How many of each object type the bid's drawings show, and every symbol that is not
    counted because nobody has confirmed what it is. Nothing is dropped silently."""
    from firebid.services.revisions import current_sheets

    consultant = consultant_of(session, bid_id)
    types = {t.key: t for t in object_library.current(session, consultant.organisation_id)}
    query = select(SymbolInstance).where(SymbolInstance.bid_id == bid_id)
    if current_only:
        sheet_ids = [sheet.id for _, sheet in current_sheets(session, bid_id)]
        query = query.where(SymbolInstance.sheet_id.in_(sheet_ids))
    entries = {
        entry.id: entry
        for entry in session.execute(
            select(LegendEntry).where(LegendEntry.bid_id == bid_id)
        ).scalars()
    }
    lineages: dict[uuid.UUID, SymbolMapping | None] = {}
    counted: dict[str, Counted] = {}
    unmapped: dict[str, Unmapped] = {}
    not_objects = 0
    for instance in session.execute(query).scalars():
        entry = entries.get(instance.legend_entry_id) if instance.legend_entry_id else None
        lineage = instance.mapping_lineage_id or (entry.mapping_lineage_id if entry else None)
        mapping = None
        if lineage is not None:
            if lineage not in lineages:
                lineages[lineage] = current(session, lineage)
            mapping = lineages[lineage]
        kind = types.get(mapping.object_type_key or "") if mapping else None
        if mapping is not None and mapping.state == CONFIRMED and kind is not None:
            if kind.measure == "none":
                not_objects += 1
                continue
            if kind.measure == "count":
                bucket = counted.setdefault(kind.key, Counted(kind.key, kind.label, 0))
                bucket.count += 1
                bucket.sheets.add(instance.sheet_id)
            # A confirmed type measured by length (a riser) is mapped: it is taken off as pipe.
            continue
        status = (
            mapping.state
            if mapping is not None
            else entry.status
            if entry is not None
            else "no legend"
        )
        group = unmapped.setdefault(
            instance.symbol_key,
            Unmapped(
                symbol_key=instance.symbol_key,
                instances=0,
                status=status,
                description=(entry.description if entry else None)
                or (mapping.description if mapping else None),
                block=instance.block,
                mapping_lineage_id=lineage,
                proposed_type=mapping.object_type_key if mapping else None,
            ),
        )
        group.instances += 1
        group.sheets.add(instance.sheet_id)
    return Counts(counted, sorted(unmapped.values(), key=lambda u: -u.instances), not_objects)


def unmapped_by_sheet(found: Counts) -> dict[uuid.UUID, int]:
    totals: dict[uuid.UUID, int] = defaultdict(int)
    for group in found.unmapped:
        for sheet in group.sheets:
            totals[sheet] += 1
    return dict(totals)
