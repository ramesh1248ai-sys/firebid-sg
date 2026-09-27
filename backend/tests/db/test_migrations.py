"""Every migration upgrades and downgrades cleanly (definition of done, every step).

On a database of its own, so a migration that fails half-way cannot leave the shared test
database in a state the other tests then trip over.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from testcontainers.community.postgres import PostgresContainer

from firebid.db.engine import clear_engine_caches, sqlalchemy_url
from firebid.settings import get_settings
from tests.db.conftest import ALEMBIC_INI, APP_ROLE_PASSWORD, PG_IMAGE


@pytest.fixture
def empty_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with PostgresContainer(
        PG_IMAGE,
        username="firebid",
        password="firebid",  # noqa: S106  (throwaway container credential)
        dbname="firebid",
    ) as container:
        url = container.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        monkeypatch.setenv("FIREBID_DATABASE_URL", url)
        monkeypatch.setenv("FIREBID_APP_DB_PASSWORD", APP_ROLE_PASSWORD)
        get_settings.cache_clear()
        clear_engine_caches()
        yield url
    monkeypatch.undo()
    get_settings.cache_clear()
    clear_engine_caches()


def tables(url: str) -> set[str]:
    engine = create_engine(sqlalchemy_url(url))
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_upgrade_downgrade_and_upgrade_again(empty_database: str) -> None:
    config = Config(ALEMBIC_INI)

    command.upgrade(config, "head")
    at_head = tables(empty_database)
    assert "title_block_layout" in at_head

    command.downgrade(config, "base")
    assert tables(empty_database) <= {"alembic_version"}

    command.upgrade(config, "head")
    assert tables(empty_database) == at_head
