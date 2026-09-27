"""The canonical object library: seeded once, edited by new versions only (FR-ADM-02)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.db.models.audit import AuditEvent
from firebid.db.models.core import Organisation
from firebid.db.models.symbols import ObjectType
from firebid.domain.actors import Actor
from firebid.services import object_library as library
from firebid.services.object_library import LibraryError, TypeSpec

pytestmark = pytest.mark.req("FR-ADM-02")

SENIOR = Actor(label="Sam Senior", roles=frozenset({"senior_estimator"}))


@pytest.fixture
def seeded(session: Session, organisation: Organisation) -> Organisation:
    library.ensure_seeded(session, organisation.id)
    return organisation


class TestTheSeed:
    def test_every_phase_1_type_is_there_with_its_attribute_schema(
        self, session: Session, seeded: Organisation
    ) -> None:
        types = {item.key: item for item in library.current(session, seeded.id)}

        for key in (
            "sprinkler_pendent",
            "sprinkler_upright",
            "sprinkler_sidewall",
            "sprinkler_concealed",
            "pipe",
            "fitting",
            "installation_control_valve_set",
            "subsidiary_control_valve",
            "gate_valve",
            "butterfly_valve",
            "check_valve",
            "test_and_drain_valve",
            "pressure_reducing_valve",
            "flow_switch",
            "tamper_switch",
        ):
            assert key in types, key
        pendent = types["sprinkler_pendent"]
        assert set(pendent.attribute_schema) == {
            "k_factor",
            "temperature_rating_c",
            "response",
            "finish",
        }
        assert (pendent.version, pendent.measure) == (1, "count")
        assert types["pipe"].measure == "length"

    def test_seeding_twice_changes_nothing(self, session: Session, seeded: Organisation) -> None:
        before = len(library.current(session, seeded.id))
        library.ensure_seeded(session, seeded.id)
        rows = session.execute(
            select(ObjectType).where(ObjectType.organisation_id == seeded.id)
        ).scalars()
        assert len(list(rows)) == before


class TestVersions:
    def test_an_edit_is_a_new_version_and_the_earlier_one_reads_back_unchanged(
        self, session: Session, seeded: Organisation
    ) -> None:
        schema = dict(library.latest(session, seeded.id, "sprinkler_pendent").attribute_schema)  # type: ignore[union-attr]
        schema["orifice"] = {"type": "text"}
        between = datetime.now(UTC)

        edited = library.change(
            session,
            seeded.id,
            "sprinkler_pendent",
            SENIOR,
            label="Sprinkler, pendent (standard coverage)",
            attributes=schema,
            note="orifice size is asked for on BOQs",
        )

        first = library.version(session, seeded.id, "sprinkler_pendent", 1)
        assert first is not None
        assert first.label == "Sprinkler, pendent"
        assert "orifice" not in first.attribute_schema
        assert (edited.version, edited.supersedes_id) == (2, first.id)
        assert edited.changed_by == "Sam Senior"
        assert [
            row.version for row in library.history(session, seeded.id, "sprinkler_pendent")
        ] == [1, 2]
        as_it_was = {row.key: row for row in library.current(session, seeded.id, as_of=between)}
        assert as_it_was["sprinkler_pendent"].version == 1

    def test_every_edit_is_audited_with_before_and_after(
        self, session: Session, seeded: Organisation
    ) -> None:
        library.change(session, seeded.id, "gate_valve", SENIOR, label="Gate valve (OS&Y)")
        session.flush()

        event = session.execute(
            select(AuditEvent).where(AuditEvent.action == "object library: changed")
        ).scalar_one()
        assert event.actor_label == "Sam Senior"
        assert event.before["label"] == "Gate valve"  # type: ignore[index]
        assert event.after["label"] == "Gate valve (OS&Y)"  # type: ignore[index]

    def test_a_deprecated_type_keeps_its_history_but_is_not_offered(
        self, session: Session, seeded: Organisation
    ) -> None:
        library.change(session, seeded.id, "tamper_switch", SENIOR, deprecate=True, note="merged")

        offered = {row.key for row in library.usable(session, seeded.id)}
        assert "tamper_switch" not in offered
        history = library.history(session, seeded.id, "tamper_switch")
        assert [(row.version, row.deprecated_at is not None) for row in history] == [
            (1, False),
            (2, True),
        ]
        with pytest.raises(LibraryError, match="deprecated"):
            library.change(session, seeded.id, "tamper_switch", SENIOR, label="x")
        restored = library.change(session, seeded.id, "tamper_switch", SENIOR, restore=True)
        assert restored.deprecated_at is None

    def test_a_new_type_can_be_added_but_not_twice(
        self, session: Session, seeded: Organisation
    ) -> None:
        spec = TypeSpec("deluge_valve", "Deluge valve", "valve", "count", {})
        added = library.create(session, seeded.id, spec, SENIOR)
        assert added.version == 1
        with pytest.raises(LibraryError, match="already"):
            library.create(session, seeded.id, spec, SENIOR)

    def test_a_bad_schema_is_refused(self, session: Session, seeded: Organisation) -> None:
        with pytest.raises(LibraryError, match="enum"):
            library.change(
                session, seeded.id, "check_valve", SENIOR, attributes={"x": {"type": "enum"}}
            )
