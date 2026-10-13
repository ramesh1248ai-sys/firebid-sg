# ruff: noqa: F811  (the fixtures below are imported by name and requested by name)
"""The organisation's people: their roles are kept here and changed by a system
administrator, and a person's access can be switched off (FR-ADM-01, ADR-013)."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from firebid.api.deps import get_principal
from firebid.auth.claims import Identity
from firebid.auth.provisioning import Principal, SwitchedOff, provision
from firebid.db.models.audit import AuditEvent
from firebid.db.models.core import AppUser, Bid, BidMember, Organisation, UserRole
from firebid.domain.state_machines import Role
from tests.db.test_bid_workspace import SignIn, app, make_person, sign_in  # noqa: F401

pytestmark = pytest.mark.req("FR-ADM-01")

ADMIN, ESTIMATOR, SENIOR = (
    str(Role.SYSTEM_ADMIN),
    str(Role.ESTIMATOR),
    str(Role.SENIOR_ESTIMATOR),
)


@pytest.fixture
def admin(session: Session, organisation: Organisation) -> Principal:
    return make_person(session, organisation, "adam", {ADMIN})


@pytest.fixture
def estimator_person(session: Session, organisation: Organisation) -> Principal:
    return make_person(session, organisation, "esther", {ESTIMATOR})


def roles_of(session: Session, user_id: uuid.UUID) -> set[str]:
    session.expire_all()
    return set(session.execute(select(UserRole.role).where(UserRole.user_id == user_id)).scalars())


def identity(person: Principal | str, roles: set[str], session: Session | None = None) -> Identity:
    if isinstance(person, str):
        return Identity(
            uuid.uuid4().hex, f"{person}@firebid.test", person.title(), frozenset(roles)
        )
    assert session is not None
    row = session.get(AppUser, person.user_id)
    assert row is not None
    return Identity(row.external_id, row.username, row.display_name, frozenset(roles))


class TestTheScreen:
    def test_everyone_is_listed_with_their_roles_and_how_many_bids_they_are_on(
        self,
        sign_in: SignIn,
        admin: Principal,
        estimator_person: Principal,
        bid: Bid,
        session: Session,
    ) -> None:
        session.add(BidMember(bid_id=bid.id, user_id=estimator_person.user_id, role=ESTIMATOR))
        session.commit()

        listed = sign_in(admin).get("/admin/users").json()

        by_name = {one["display_name"]: one for one in listed}
        assert by_name["Esther"]["roles"] == [ESTIMATOR]
        assert by_name["Esther"]["bids"] == 1
        assert by_name["Esther"]["is_active"] is True
        assert by_name["Adam"]["roles"] == [ADMIN]

    def test_the_roles_to_choose_from_leave_out_the_platform_s_own(
        self,
        sign_in: SignIn,
        admin: Principal,
    ) -> None:
        roles = sign_in(admin).get("/admin/users/roles").json()

        assert ADMIN in roles and ESTIMATOR in roles
        assert str(Role.SYSTEM) not in roles

    @pytest.mark.parametrize(
        "role", sorted(str(role) for role in Role if role not in (Role.SYSTEM_ADMIN, Role.SYSTEM))
    )
    def test_only_a_system_administrator_sees_or_changes_it(
        self,
        role: str,
        sign_in: SignIn,
        organisation: Organisation,
        estimator_person: Principal,
        session: Session,
    ) -> None:
        client = sign_in(make_person(session, organisation, "nora", {role}))

        assert client.get("/admin/users").status_code == 403
        assert client.get("/admin/users/roles").status_code == 403
        changed = client.patch(f"/admin/users/{estimator_person.user_id}", json={"roles": [ADMIN]})
        assert changed.status_code == 403
        assert roles_of(session, estimator_person.user_id) == {ESTIMATOR}


def test_anyone_signed_in_is_told_who_they_are_and_the_roles_they_hold_here(
    sign_in: SignIn, estimator_person: Principal
) -> None:
    answer = sign_in(estimator_person).get("/me")

    assert answer.status_code == 200
    assert answer.json() == {
        "user_id": str(estimator_person.user_id),
        "username": "esther@firebid.test",
        "display_name": "Esther",
        "roles": [ESTIMATOR],
    }


class TestChangingSomeone:
    def test_roles_are_set_as_given_and_the_change_is_on_the_audit_log(
        self,
        sign_in: SignIn,
        admin: Principal,
        estimator_person: Principal,
        session: Session,
    ) -> None:
        answer = sign_in(admin).patch(
            f"/admin/users/{estimator_person.user_id}", json={"roles": [SENIOR, ESTIMATOR]}
        )

        assert answer.status_code == 200
        assert answer.json()["roles"] == sorted([ESTIMATOR, SENIOR])
        assert roles_of(session, estimator_person.user_id) == {ESTIMATOR, SENIOR}
        (event,) = session.execute(
            select(AuditEvent).where(AuditEvent.entity_id == str(estimator_person.user_id))
        ).scalars()
        assert event.action == "user: roles changed"
        assert event.actor_id == admin.user_id
        assert event.before == {"roles": [ESTIMATOR]}
        assert event.after == {"roles": sorted([ESTIMATOR, SENIOR])}

    def test_someone_may_be_left_with_no_role(
        self,
        sign_in: SignIn,
        admin: Principal,
        estimator_person: Principal,
        session: Session,
    ) -> None:
        answer = sign_in(admin).patch(
            f"/admin/users/{estimator_person.user_id}", json={"roles": []}
        )

        assert answer.status_code == 200
        assert roles_of(session, estimator_person.user_id) == set()

    def test_access_is_switched_off_and_on_and_each_is_on_the_audit_log(
        self,
        sign_in: SignIn,
        admin: Principal,
        estimator_person: Principal,
        session: Session,
    ) -> None:
        client = sign_in(admin)
        off = client.patch(f"/admin/users/{estimator_person.user_id}", json={"is_active": False})
        on = client.patch(f"/admin/users/{estimator_person.user_id}", json={"is_active": True})

        assert (off.json()["is_active"], on.json()["is_active"]) == (False, True)
        assert off.json()["roles"] == [ESTIMATOR], "their roles are kept for when they are back"
        actions = session.execute(
            select(AuditEvent.action)
            .where(AuditEvent.entity_id == str(estimator_person.user_id))
            .order_by(AuditEvent.occurred_at)
        ).scalars()
        assert list(actions) == ["user: access switched off", "user: access switched on"]

    def test_saying_what_is_already_so_changes_nothing_and_records_nothing(
        self,
        sign_in: SignIn,
        admin: Principal,
        estimator_person: Principal,
        session: Session,
    ) -> None:
        answer = sign_in(admin).patch(
            f"/admin/users/{estimator_person.user_id}",
            json={"roles": [ESTIMATOR], "is_active": True},
        )

        assert answer.status_code == 200
        events = session.execute(
            select(AuditEvent).where(AuditEvent.entity_id == str(estimator_person.user_id))
        ).scalars()
        assert list(events) == []

    def test_the_platform_s_own_role_and_a_role_nobody_knows_are_refused(
        self,
        sign_in: SignIn,
        admin: Principal,
        estimator_person: Principal,
    ) -> None:
        client = sign_in(admin)
        url = f"/admin/users/{estimator_person.user_id}"

        assert client.patch(url, json={"roles": [str(Role.SYSTEM)]}).status_code == 422
        assert client.patch(url, json={"roles": ["owner"]}).status_code == 422

    def test_nobody_changes_their_own_access(
        self,
        sign_in: SignIn,
        admin: Principal,
        organisation: Organisation,
        session: Session,
    ) -> None:
        make_person(session, organisation, "anna", {ADMIN})
        client = sign_in(admin)

        demoted = client.patch(f"/admin/users/{admin.user_id}", json={"roles": [ESTIMATOR]})
        off = client.patch(f"/admin/users/{admin.user_id}", json={"is_active": False})

        assert (demoted.status_code, off.status_code) == (409, 409)
        assert roles_of(session, admin.user_id) == {ADMIN}

    def test_the_last_administrator_who_can_sign_in_keeps_the_role_and_the_access(
        self,
        sign_in: SignIn,
        admin: Principal,
        organisation: Organisation,
        session: Session,
    ) -> None:
        # Nobody changes their own access, so an administrator acting on another leaves
        # themselves. The rule is for the request that arrives as an administrator's and is
        # not one by the database: it is checked there.
        anna = make_person(session, organisation, "anna", set())
        stale = Principal(
            anna.user_id, anna.organisation_id, anna.username, "Anna", frozenset({ADMIN})
        )
        client = sign_in(stale)

        assert client.patch(f"/admin/users/{admin.user_id}", json={"roles": []}).status_code == 409
        assert (
            client.patch(f"/admin/users/{admin.user_id}", json={"is_active": False}).status_code
            == 409
        )
        assert roles_of(session, admin.user_id) == {ADMIN}

    def test_someone_of_another_organisation_is_nobody(
        self,
        sign_in: SignIn,
        admin: Principal,
        session: Session,
    ) -> None:
        other = Organisation(name="Another Contractor")
        session.add(other)
        session.commit()
        stranger = make_person(session, other, "sam", {ESTIMATOR})

        answer = sign_in(admin).patch(f"/admin/users/{stranger.user_id}", json={"roles": [ADMIN]})

        assert answer.status_code == 404
        assert roles_of(session, stranger.user_id) == {ESTIMATOR}


class TestSigningIn:
    def test_a_first_sign_in_starts_with_the_identity_provider_s_roles(
        self, session: Session, organisation: Organisation
    ) -> None:
        principal = provision(
            session, identity("nina", {ESTIMATOR, "offline_access"}), organisation
        )

        assert principal.roles == {ESTIMATOR}
        assert roles_of(session, principal.user_id) == {ESTIMATOR}

    def test_after_that_the_roles_held_here_stand_whatever_the_token_says(
        self,
        session: Session,
        organisation: Organisation,
        admin: Principal,
        estimator_person: Principal,
    ) -> None:
        session.add(UserRole(user_id=estimator_person.user_id, role=SENIOR))
        session.commit()

        again = provision(
            session,
            identity(estimator_person, {str(Role.COMMERCIAL_DIRECTOR)}, session),
            organisation,
        )

        assert again.roles == {ESTIMATOR, SENIOR}

    def test_a_role_taken_away_here_is_gone_at_the_next_request(
        self,
        app: FastAPI,
        sign_in: SignIn,
        session: Session,
        organisation: Organisation,
        admin: Principal,
        estimator_person: Principal,
    ) -> None:
        sign_in(admin).patch(f"/admin/users/{estimator_person.user_id}", json={"roles": []})
        del app.dependency_overrides[get_principal]

        again = provision(session, identity(estimator_person, {ESTIMATOR}, session), organisation)

        assert again.roles == frozenset()

    def test_someone_whose_access_is_off_is_refused(
        self,
        session: Session,
        organisation: Organisation,
        admin: Principal,
        estimator_person: Principal,
    ) -> None:
        row = session.get(AppUser, estimator_person.user_id)
        assert row is not None
        row.is_active = False
        session.commit()

        with pytest.raises(SwitchedOff):
            provision(session, identity(estimator_person, {ESTIMATOR}, session), organisation)

    def test_an_organisation_with_no_administrator_takes_the_identity_provider_s_word_for_one(
        self, session: Session, organisation: Organisation, estimator_person: Principal
    ) -> None:
        # Nobody could give the role here, so the installation would be stuck without this.
        rescued = provision(
            session, identity(estimator_person, {ADMIN, ESTIMATOR}, session), organisation
        )

        assert rescued.roles == {ESTIMATOR, ADMIN}

    def test_where_there_is_an_administrator_the_token_makes_nobody_one(
        self,
        session: Session,
        organisation: Organisation,
        admin: Principal,
        estimator_person: Principal,
    ) -> None:
        again = provision(session, identity(estimator_person, {ADMIN}, session), organisation)

        assert again.roles == {ESTIMATOR}


def test_a_request_from_someone_switched_off_is_answered_403(
    app: FastAPI,
    session: Session,
    organisation: Organisation,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from firebid.api import deps

    person = make_person(session, organisation, "olga", {ESTIMATOR})
    row = session.get(AppUser, person.user_id)
    assert row is not None
    row.is_active = False
    session.commit()
    claims: dict[str, object] = {
        "oid": row.external_id,
        "preferred_username": row.username,
        "roles": [ESTIMATOR],
    }

    class Verifier:
        def verify(self, token: str) -> dict[str, object]:
            return claims

    monkeypatch.setattr(deps, "get_token_verifier", lambda settings: Verifier())
    monkeypatch.setattr(deps, "default_organisation", lambda session, name: organisation)

    answer = TestClient(app).get("/bids", headers={"Authorization": "Bearer anything"})

    assert answer.status_code == 403
    assert "switched off" in answer.json()["detail"]
