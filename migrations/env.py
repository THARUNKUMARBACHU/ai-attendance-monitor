"""Alembic environment: migrations always run online, as the database owner role."""

from alembic import context
from sqlalchemy import create_engine, pool

from attendance_ai.core.config import get_settings

config = context.config


def _database_url() -> str:
    url = config.attributes.get("url")
    if isinstance(url, str) and url:
        return url
    return get_settings().database_owner_url.get_secret_value()


def run_migrations() -> None:
    if context.is_offline_mode():
        raise RuntimeError("Offline migrations are not supported; run them against a database.")
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, transaction_per_migration=True)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run_migrations()
