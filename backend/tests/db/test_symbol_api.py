# ruff: noqa: F811  (the fixtures below are imported by name and requested by name)
"""The mapping screen's API and the library API (FR-VIS-02, FR-ADM-02)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy.orm import Session

from firebid.ai_gateway.providers.fake import FakeAdapter
from firebid.ai_gateway.router import Router
from firebid.auth.provisioning import Principal
from firebid.db.models.core import Bid, Organisation
from firebid.domain.state_machines import Role
from firebid.evals import synthetic_symbols as fixtures
from firebid.storage import object_store
from firebid.storage.object_store import MemoryObjectStore
from tests.db.test_sheet_views import SignIn, app, member, sign_in  # noqa: F401
from tests.db.test_symbol_mapping import (  # noqa: F401
    UPRIGHT,
    ask_the_model,
    from_consultant,
    no_tiles,
    router,
    store,
    tender,
)


@pytest.fixture(autouse=True)
def shared_store(monkeypatch: pytest.MonkeyPatch, store: MemoryObjectStore) -> None:
    """The API reads crops from the same store the pipeline wrote them to."""
    monkeypatch.setattr(object_store, "get_object_store", lambda: store)
    from firebid.api import symbols as symbols_api

    monkeypatch.setattr(symbols_api, "get_object_store", lambda: store)


@pytest.fixture
def estimator(session: Session, organisation: Organisation, bid: Bid) -> Principal:
    return member(session, organisation, bid, "ethan", Role.ESTIMATOR)


@pytest.fixture
def design_manager(session: Session, organisation: Organisation, bid: Bid) -> Principal:
    return member(session, organisation, bid, "dora", Role.DESIGN_MANAGER)


@pytest.fixture
def senior(session: Session, organisation: Organisation, bid: Bid) -> Principal:
    return member(session, organisation, bid, "sam", Role.SENIOR_ESTIMATOR)


@pytest.fixture
def mapped(
    session: Session,
    bid: Bid,
    store: MemoryObjectStore,
    router: Callable[..., tuple[Router, FakeAdapter]],
) -> Any:
    from_consultant(session, bid, "Alpha Consultants")
    truth = tender(session, bid, store, fixtures.ALPHA)
    ask_the_model(session, store, bid, router(UPRIGHT)[0])
    return truth


@pytest.mark.req("FR-VIS-02")
class TestTheMappingScreen:
    def test_counts_list_the_unmapped_symbol_and_count_nothing_unconfirmed(
        self, bid: Bid, mapped: Any, sign_in: SignIn, design_manager: Principal
    ) -> None:
        counts = sign_in(design_manager).get(f"/bids/{bid.id}/symbols/counts").json()

        assert counts["counted"] == []
        mystery = [u for u in counts["unmapped"] if u["block"] == fixtures.ALPHA.mystery_block]
        assert mystery and mystery[0]["instances"] == 3
        assert mystery[0]["status"] == "no legend"

    def test_confirming_through_the_api_counts_the_symbols(
        self, bid: Bid, mapped: Any, sign_in: SignIn, estimator: Principal
    ) -> None:
        client = sign_in(estimator)
        rows = client.get(f"/bids/{bid.id}/symbols/legend").json()
        lineages = {row["mapping"]["lineage_id"] for row in rows if row["mapping"]}

        for lineage in lineages:
            response = client.post(f"/bids/{bid.id}/symbols/mappings/{lineage}/confirm", json={})
            assert response.status_code == 200, response.text
            assert response.json()["confirmed_by"] == "Ethan"

        counts = client.get(f"/bids/{bid.id}/symbols/counts").json()
        assert {item["object_type"]: item["count"] for item in counts["counted"]} == mapped.counts
        assert [u["block"] for u in counts["unmapped"]] == [fixtures.ALPHA.mystery_block]

    def test_a_row_shows_its_proposal_with_provenance_and_its_crop(
        self, bid: Bid, mapped: Any, sign_in: SignIn, design_manager: Principal
    ) -> None:
        client = sign_in(design_manager)
        rows = client.get(f"/bids/{bid.id}/symbols/legend").json()
        upright = next(row for row in rows if row["description"] == "SPRINKLER - UP TYPE")

        assert upright["mapping"]["source"] == "model"
        assert upright["mapping"]["confidence"] == 0.82
        assert upright["mapping"]["model"] == "vision-1"
        assert upright["mapping"]["prompt_version"]
        crop = client.get(f"/bids/{bid.id}/symbols/legend/{upright['id']}/crop.png")
        assert crop.status_code == 200
        assert crop.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_only_a_mapping_confirmer_may_decide(
        self, bid: Bid, mapped: Any, sign_in: SignIn, design_manager: Principal
    ) -> None:
        client = sign_in(design_manager)
        [row, *_] = client.get(f"/bids/{bid.id}/symbols/legend").json()

        response = client.post(
            f"/bids/{bid.id}/symbols/mappings/{row['mapping']['lineage_id']}/confirm", json={}
        )

        assert response.status_code == 403

    def test_a_correction_to_an_unknown_type_is_refused(
        self, bid: Bid, mapped: Any, sign_in: SignIn, estimator: Principal
    ) -> None:
        client = sign_in(estimator)
        [row, *_] = client.get(f"/bids/{bid.id}/symbols/legend").json()

        response = client.post(
            f"/bids/{bid.id}/symbols/mappings/{row['mapping']['lineage_id']}/confirm",
            json={"object_type": "fire_hydrant_pillar"},
        )

        assert response.status_code == 409

    def test_another_bids_mapping_is_not_found(
        self,
        bid: Bid,
        second_bid: Bid,
        mapped: Any,
        session: Session,
        organisation: Organisation,
        sign_in: SignIn,
    ) -> None:
        outsider = member(session, organisation, second_bid, "olive", Role.ESTIMATOR)
        client = sign_in(outsider)
        [row, *_] = sign_in(outsider).get(f"/bids/{second_bid.id}/symbols/legend").json() or [None]
        assert row is None
        response = client.post(
            f"/bids/{second_bid.id}/symbols/mappings/00000000-0000-0000-0000-000000000000/confirm",
            json={},
        )
        assert response.status_code == 404


@pytest.mark.req("FR-ADM-02")
class TestTheLibraryApi:
    def test_an_edit_is_versioned_and_the_earlier_version_reads_back(
        self, sign_in: SignIn, senior: Principal
    ) -> None:
        client = sign_in(senior)
        types = client.get("/library/object-types").json()
        assert any(t["key"] == "sprinkler_pendent" for t in types)

        edited = client.post(
            "/library/object-types/sprinkler_pendent",
            json={"label": "Pendent sprinkler head", "note": "house style"},
        )
        assert edited.status_code == 200
        assert (edited.json()["version"], edited.json()["changed_by"]) == (2, "Sam")

        first = client.get("/library/object-types/sprinkler_pendent/versions/1").json()
        assert first["label"] == "Sprinkler, pendent"
        history = client.get("/library/object-types/sprinkler_pendent/history").json()
        assert [row["version"] for row in history] == [1, 2]

    def test_deprecating_a_type_keeps_it_listed_and_marked(
        self, sign_in: SignIn, senior: Principal
    ) -> None:
        client = sign_in(senior)
        client.get("/library/object-types")

        client.post("/library/object-types/tamper_switch", json={"deprecate": True})

        listed = {t["key"]: t for t in client.get("/library/object-types").json()}
        assert listed["tamper_switch"]["deprecated"] is True

    def test_only_a_library_editor_may_change_it(
        self, sign_in: SignIn, estimator: Principal
    ) -> None:
        client = sign_in(estimator)
        client.get("/library/object-types")

        response = client.post("/library/object-types/gate_valve", json={"label": "x"})

        assert response.status_code == 403

    def test_a_consultants_mappings_and_their_history_are_listed(
        self, mapped: Any, sign_in: SignIn, estimator: Principal
    ) -> None:
        client = sign_in(estimator)
        [consultant] = client.get("/library/consultants").json()
        assert consultant["consultant_key"] == "ALPHA CONSULTANTS"

        mappings = client.get(
            "/library/mappings", params={"consultant_key": consultant["consultant_key"]}
        ).json()
        assert {m["object_type"] for m in mappings} >= {"sprinkler_pendent", "gate_valve"}
        history = client.get(f"/library/mappings/{mappings[0]['lineage_id']}/history").json()
        assert history[0]["version"] == 1


@pytest.fixture
def as_app(as_application_role: None) -> Callable[[Principal], Any]:
    """The API as it runs live: the application's database role, under row-level security,
    with the caller's identity set on the transaction exactly as `get_principal` sets it."""
    from fastapi.testclient import TestClient

    from firebid.api.app import create_app
    from firebid.api.deps import SESSION, get_principal
    from firebid.db.identity import set_transaction_identity
    from firebid.settings import Settings

    application = create_app(Settings(env="test"), health_checks={})

    def client(principal: Principal) -> TestClient:
        def signed_in(session: Session = SESSION) -> Principal:
            set_transaction_identity(session, principal.user_id)
            return principal

        application.dependency_overrides[get_principal] = signed_in
        return TestClient(application)

    return client


@pytest.mark.req("FR-ADM-02")
class TestUnderRowLevelSecurity:
    """What the owner-connected tests above cannot see: the live role's policies."""

    def test_a_mapping_decision_is_saved_and_audited_under_its_bid(
        self,
        bid: Bid,
        mapped: Any,
        as_app: Callable[[Principal], Any],
        estimator: Principal,
        session: Session,
    ) -> None:
        from sqlalchemy import select

        from firebid.db.models.audit import AuditEvent

        client = as_app(estimator)
        [row, *_] = client.get(f"/bids/{bid.id}/symbols/legend").json()

        response = client.post(
            f"/bids/{bid.id}/symbols/mappings/{row['mapping']['lineage_id']}/confirm", json={}
        )

        assert response.status_code == 200, response.text
        session.expire_all()
        event = session.execute(
            select(AuditEvent).where(AuditEvent.action == "symbol mapping: confirmed")
        ).scalar_one()
        assert event.bid_id == bid.id

    def test_a_senior_estimator_can_edit_the_library_and_it_is_audited(
        self, as_app: Callable[[Principal], Any], senior: Principal, session: Session
    ) -> None:
        from sqlalchemy import select

        from firebid.db.models.audit import AuditEvent

        client = as_app(senior)
        client.get("/library/object-types")

        response = client.post(
            "/library/object-types/gate_valve", json={"label": "Gate valve (OS&Y)"}
        )

        assert response.status_code == 200, response.text
        session.expire_all()
        event = session.execute(
            select(AuditEvent).where(AuditEvent.action == "object library: changed")
        ).scalar_one()
        assert (event.bid_id, event.actor_label) == (None, "Sam")

    def test_an_estimator_cannot_write_an_organisation_event_in_the_database(
        self, app_role_engine: Any, estimator: Principal, organisation: Organisation
    ) -> None:
        """The database refuses it too, not only the permission check in the API."""
        from sqlalchemy.orm import Session as OrmSession

        from firebid.db.audit import record_event
        from firebid.db.identity import set_transaction_identity
        from firebid.domain.actors import AuditContext

        with OrmSession(app_role_engine) as raw, pytest.raises(Exception, match="row-level"):
            set_transaction_identity(raw, estimator.user_id)
            record_event(
                raw,
                context=AuditContext(organisation_id=organisation.id),
                actor=estimator.actor(),
                action="object library: changed",
                entity_type="object_type",
                entity_id=estimator.user_id,
            )
            raw.commit()
