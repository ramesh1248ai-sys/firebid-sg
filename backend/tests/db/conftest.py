"""A real PostgreSQL for the database tests.

Testcontainers starts one container per test session and Alembic migrates it, so tests run
against the same schema production gets, including partitions, triggers and role grants.
Inside the Dev Container the database is reached through the host, so TESTCONTAINERS_HOST_OVERRIDE
is set for us there.
"""

from __future__ import annotations

import hashlib
import os
import pickle
import uuid
from collections import OrderedDict
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.community.postgres import PostgresContainer

from firebid.db.base import Base
from firebid.db.engine import clear_engine_caches, sqlalchemy_url
from firebid.db.models.core import AppUser, Bid, BidMember, Organisation, Project
from firebid.domain.actors import Actor
from firebid.domain.state_machines import Role
from firebid.settings import get_settings

PG_IMAGE = "pgvector/pgvector:pg17"
APP_ROLE_PASSWORD = "firebid-app-test"  # noqa: S105  (throwaway container credential)
ALEMBIC_INI = "alembic.ini"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    with PostgresContainer(
        PG_IMAGE,
        username="firebid",
        password="firebid",  # noqa: S106  (throwaway container credential)
        dbname="firebid",
    ) as container:
        url = container.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        os.environ["FIREBID_DATABASE_URL"] = url
        os.environ["FIREBID_APP_DB_PASSWORD"] = APP_ROLE_PASSWORD
        # Settings and the engine are cached; an earlier test may have cached the defaults.
        get_settings.cache_clear()
        clear_engine_caches()
        config = Config(ALEMBIC_INI)
        command.upgrade(config, "head")
        yield url
        get_settings.cache_clear()
        clear_engine_caches()


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    engine = create_engine(sqlalchemy_url(database_url), future=True)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def app_role_engine(database_url: str) -> Iterator[Engine]:
    """A connection as the restricted application role (no UPDATE/DELETE on audit tables)."""
    url = sqlalchemy_url(database_url).replace(
        "firebid:firebid@", f"firebid_app:{APP_ROLE_PASSWORD}@"
    )
    engine = create_engine(url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture
def as_application_role(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make `session_scope` connect as the application role, under row-level security.

    Most tests connect as the table owner, which row-level security does not restrict. Code
    that opens its own session (a job, a stream) must be tested this way, or a session that
    forgets to act as someone passes here and sees nothing in production.
    """
    monkeypatch.setenv(
        "FIREBID_DATABASE_URL",
        database_url.replace("firebid:firebid@", f"firebid_app:{APP_ROLE_PASSWORD}@"),
    )
    get_settings.cache_clear()
    clear_engine_caches()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()
    clear_engine_caches()


# What the sandbox gave for an input already read in this test run, as the pickled answer.
_SANDBOXED: OrderedDict[str, bytes] = OrderedDict()
_SANDBOXED_MOST_BYTES = 256 * 1024 * 1024


@pytest.fixture(autouse=True)
def sandbox_reads_an_input_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A sandboxed parse of exactly the same input gives back what it gave before.

    Most of the slowest database tests read the same synthetic drawings in their set-up, each
    a fresh process a call: about 30 calls and three quarters of the test. The parsers are
    pure functions of their input, so the second time the answer is taken from the first. It
    is unpickled afresh each time, as a real sandbox's answer is. A failure is never kept: a
    test that makes the sandbox fail still sees it fail. The sandbox itself is tested, with a
    process a call, in `tests/sandbox`.
    """
    from firebid.sandbox import runner

    real = runner._attempt

    def remembered(
        function: Any, args: tuple[Any, ...], kwargs: dict[str, Any], limits: Any, **options: Any
    ) -> Any:
        try:
            asked = pickle.dumps(
                (function.__module__, function.__qualname__, args, sorted(kwargs.items()), limits)
            )
        except Exception:  # an argument that does not pickle: not ours to remember
            return real(function, args, kwargs, limits, **options)
        key = hashlib.sha256(asked).hexdigest()
        if key in _SANDBOXED:
            _SANDBOXED.move_to_end(key)
            return pickle.loads(_SANDBOXED[key])  # noqa: S301 - written by this process
        value = real(function, args, kwargs, limits, **options)
        _SANDBOXED[key] = pickle.dumps(value)
        while sum(map(len, _SANDBOXED.values())) > _SANDBOXED_MOST_BYTES and len(_SANDBOXED) > 1:
            _SANDBOXED.popitem(last=False)
        return value

    monkeypatch.setattr(runner, "_attempt", remembered)


@pytest.fixture(autouse=True)
def clean_database(engine: Engine) -> Iterator[None]:
    """Each test starts empty. Truncating keeps partitions, triggers and grants in place."""
    yield
    tables = [
        table.name
        for table in reversed(Base.metadata.sorted_tables)
        if table.name not in {"alembic_version"}
    ]
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session


@pytest.fixture
def estimator() -> Actor:
    return Actor(label="Esther Tan", roles=frozenset({str(Role.ESTIMATOR)}), id=None)


@pytest.fixture
def senior_estimator() -> Actor:
    return Actor(label="Samuel Lim", roles=frozenset({str(Role.SENIOR_ESTIMATOR)}), id=None)


@pytest.fixture
def commercial_director() -> Actor:
    return Actor(label="Clara Wong", roles=frozenset({str(Role.COMMERCIAL_DIRECTOR)}), id=None)


@pytest.fixture
def organisation(session: Session) -> Organisation:
    org = Organisation(name=f"FireBid Test {uuid.uuid4().hex[:8]}")
    session.add(org)
    session.commit()
    return org


@pytest.fixture
def user(session: Session, organisation: Organisation) -> AppUser:
    person = AppUser(
        organisation_id=organisation.id,
        external_id=uuid.uuid4().hex,
        username="estimator@firebid.test",
        display_name="Esther Tan",
    )
    session.add(person)
    session.commit()
    return person


@pytest.fixture
def bid(session: Session, organisation: Organisation, user: AppUser) -> Bid:
    """A complete, staffed bid: every FR-BID-01 detail present, so it can be qualified."""
    project = Project(organisation_id=organisation.id, name="Example Commercial Tower")
    session.add(project)
    session.flush()
    bid = Bid(
        organisation_id=organisation.id,
        project_id=project.id,
        human_id="BID-2026-014",
        client_name="Main Contractor Pte Ltd",
        tender_reference="MC/2026/FP/014",
        submission_deadline=datetime.now(UTC) + timedelta(days=14),
        clarification_cutoff=datetime.now(UTC) + timedelta(days=7),
        tender_validity_days=90,
    )
    session.add(bid)
    session.flush()
    session.add(BidMember(bid_id=bid.id, user_id=user.id, role=str(Role.BID_MANAGER)))
    session.commit()
    return bid


@pytest.fixture
def second_bid(session: Session, organisation: Organisation, user: AppUser) -> Bid:
    """Another bid in the same organisation, for testing that bids stay separate."""
    project = Project(organisation_id=organisation.id, name="Another Tower")
    session.add(project)
    session.flush()
    bid = Bid(
        organisation_id=organisation.id,
        project_id=project.id,
        human_id="BID-2026-015",
        client_name="Another Contractor Pte Ltd",
        tender_reference="AC/2026/FP/015",
        submission_deadline=datetime.now(UTC) + timedelta(days=21),
    )
    session.add(bid)
    session.flush()
    session.add(BidMember(bid_id=bid.id, user_id=user.id, role=str(Role.BID_MANAGER)))
    session.commit()
    return bid
