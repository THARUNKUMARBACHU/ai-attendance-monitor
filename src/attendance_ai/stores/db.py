"""PostgreSQL access. Every session that touches tenant data is opened with an access context."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.orm import Session

from attendance_ai.core.access import AccessContext
from attendance_ai.core.config import Settings


def apply_access_settings(connection: Connection, ctx: AccessContext) -> None:
    """Set what row-level security reads, for the current transaction only (set_config(..., true)).

    Transaction-local settings end with the transaction, so a pooled connection can never carry
    one caller's access into another caller's request.
    """
    settings = ctx.db_settings()
    calls = ", ".join(f"set_config(:name_{i}, :value_{i}, true)" for i in range(len(settings)))
    params: dict[str, str] = {}
    for i, (name, value) in enumerate(settings.items()):
        params[f"name_{i}"] = name
        params[f"value_{i}"] = value
    # Only bind placeholders are interpolated into the SQL text; names and values travel as parameters.
    connection.execute(text(f"SELECT {calls}"), params)


def quote_literal(value: str) -> str:
    """Quote a value as a PostgreSQL string literal (standard_conforming_strings is on by default)."""
    if "\x00" in value:
        raise ValueError("NUL characters are not allowed in session settings.")
    return "'" + value.replace("'", "''") + "'"


def set_local_statements(ctx: AccessContext) -> list[str]:
    """SET LOCAL statements for the read-only query role, which is not allowed to call set_config()."""
    return [f"SET LOCAL {name} = {quote_literal(value)}" for name, value in ctx.db_settings().items()]


def _create_engine(url: str, settings: Settings, application_name: str) -> Engine:
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_pool_size,
        connect_args={
            "connect_timeout": settings.db_connect_timeout_seconds,
            "application_name": application_name,
        },
    )


class Database:
    """Engines for the app role (reads and writes) and the read-only query role (LLM-written SQL)."""

    def __init__(self, settings: Settings) -> None:
        self.engine = _create_engine(settings.database_url.get_secret_value(), settings, "attendance-ai")
        self.query_engine = _create_engine(
            settings.database_query_url.get_secret_value(), settings, "attendance-ai-query"
        )

    @contextmanager
    def session_for(self, ctx: AccessContext) -> Iterator[Session]:
        """A transaction scoped to the caller's access. Commits on success, rolls back on error."""
        with Session(self.engine, expire_on_commit=False) as session, session.begin():
            apply_access_settings(session.connection(), ctx)
            yield session

    @contextmanager
    def session(self) -> Iterator[Session]:
        """A transaction with no access context. Row-level security makes tenant tables unreadable
        through it; it exists for writes that precede identification, such as failed-login audits."""
        with Session(self.engine, expire_on_commit=False) as session, session.begin():
            yield session

    @contextmanager
    def query_connection(self, ctx: AccessContext) -> Iterator[Connection]:
        """A read-only transaction for LLM-written SQL, scoped to the caller's access."""
        with self.query_engine.connect() as connection, connection.begin():
            for statement in set_local_statements(ctx):
                connection.exec_driver_sql(statement)
            yield connection

    def ping(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def dispose(self) -> None:
        self.engine.dispose()
        self.query_engine.dispose()
