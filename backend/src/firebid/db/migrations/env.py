"""Alembic environment. Runs migrations online against FIREBID_DATABASE_URL."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from firebid.db import models  # noqa: F401  (registers every table on the metadata)
from firebid.db.base import Base
from firebid.db.engine import sqlalchemy_url
from firebid.settings import get_settings

config = context.config
if config.config_file_name is not None:
    # Alembic's default would set disabled=True on every logger already created, silencing
    # the application's own logging whenever migrations run in the same process.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Leave the job queue's own tables alone: Procrastinate owns their shape (ADR-006)."""
    return not (type_ == "table" and name and name.startswith("procrastinate"))


def run_migrations_online() -> None:
    engine = create_engine(sqlalchemy_url(get_settings().database_url), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("Offline migrations are not supported; run against a database.")
run_migrations_online()
