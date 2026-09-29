"""Database provisioning: roles, database, hardening, migrations and grants.

Needs an administrator connection (DATABASE_ADMIN_URL). Every step is idempotent, so it is safe to
run on every deployment. Role names and passwords are taken from the three connection URLs.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy.engine import make_url

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"

READ_WRITE_TABLES = (
    "sources",
    "source_versions",
    "ingestion_jobs",
    "attendance_records",
    "query_log",
    "feedback_examples",
    "scope_versions",
)
APPEND_ONLY_TABLES = ("audit_events",)
QUERY_VIEWS = ("v_attendance",)


@dataclass(frozen=True, slots=True)
class Role:
    name: str
    password: str | None


@dataclass(frozen=True, slots=True)
class DatabasePlan:
    database: str
    owner: Role
    app: Role
    query: Role


def plan_from_urls(owner_url: str, app_url: str, query_url: str) -> DatabasePlan:
    owner, app, query = (make_url(url) for url in (owner_url, app_url, query_url))
    databases = {owner.database, app.database, query.database}
    if len(databases) != 1 or None in databases:
        raise ValueError(
            "DATABASE_OWNER_URL, DATABASE_URL and DATABASE_QUERY_URL must name the same database."
        )
    names = [owner.username, app.username, query.username]
    if None in names or len(set(names)) != 3:
        raise ValueError("The owner, app and query URLs must use three different roles.")
    return DatabasePlan(
        database=str(owner.database),
        owner=Role(str(owner.username), owner.password),
        app=Role(str(app.username), app.password),
        query=Role(str(query.username), query.password),
    )


def libpq_url(url: str, database: str | None = None) -> str:
    """Turn a SQLAlchemy URL into a plain libpq URL, optionally for another database."""
    parsed = make_url(url).set(drivername="postgresql")
    if database is not None:
        parsed = parsed.set(database=database)
    return parsed.render_as_string(hide_password=False)


def ensure_roles(admin_url: str, plan: DatabasePlan) -> None:
    """Create the roles, or update login and password if they exist. Privileged attributes are set
    only on creation (a non-superuser admin may not restate them later), then verified every time."""
    with psycopg.connect(libpq_url(admin_url), autocommit=True) as conn:
        for role in (plan.owner, plan.app, plan.query):
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role.name,)).fetchone()
            if exists:
                query = sql.SQL("ALTER ROLE {} WITH LOGIN").format(sql.Identifier(role.name))
            else:
                query = sql.SQL(
                    "CREATE ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
                ).format(sql.Identifier(role.name))
            if role.password:
                query = sql.SQL("{} PASSWORD {}").format(query, sql.Literal(role.password))
            conn.execute(query)
            _require_unprivileged(conn, role.name)


def _require_unprivileged(conn: psycopg.Connection[Any], role: str) -> None:
    row = conn.execute(
        "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = %s", (role,)
    ).fetchone()
    if row is None or any(row):
        raise RuntimeError(
            f"Role '{role}' must not be a superuser, bypass row-level security, or create roles or "
            "databases. Remove those attributes before provisioning."
        )


def ensure_database(admin_url: str, plan: DatabasePlan) -> None:
    database, owner = sql.Identifier(plan.database), sql.Identifier(plan.owner.name)
    with psycopg.connect(libpq_url(admin_url), autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (plan.database,)).fetchone()
        if exists:
            conn.execute(sql.SQL("ALTER DATABASE {} OWNER TO {}").format(database, owner))
        else:
            conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(database, owner))


def harden_database(admin_url: str, plan: DatabasePlan) -> bool:
    """Lock the database down. Returns True if the query role can no longer call set_config()."""
    database = sql.Identifier(plan.database)
    owner, app, query = (sql.Identifier(role.name) for role in (plan.owner, plan.app, plan.query))
    with psycopg.connect(libpq_url(admin_url, plan.database), autocommit=True) as conn:
        conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(database))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(database, app, query))
        conn.execute(sql.SQL("ALTER SCHEMA public OWNER TO {}").format(owner))
        conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        in_database = sql.SQL("ALTER ROLE {} IN DATABASE {} SET {} = {}")
        conn.execute(
            in_database.format(
                query, database, sql.Identifier("default_transaction_read_only"), sql.Literal("on")
            )
        )
        conn.execute(
            in_database.format(query, database, sql.Identifier("statement_timeout"), sql.Literal("3s"))
        )
        conn.execute(
            in_database.format(app, database, sql.Identifier("statement_timeout"), sql.Literal("30s"))
        )
        # Row-level security reads per-transaction settings. Only the app and owner roles should be able
        # to change them, so the query role (LLM-written SQL) cannot, even if the SQL guard were bypassed.
        # Only a superuser can revoke this, and PostgreSQL merely warns when a non-superuser tries
        # (for example Aiven's avnadmin), so the outcome is checked rather than assumed.
        with contextlib.suppress(psycopg.errors.InsufficientPrivilege):
            conn.execute("REVOKE EXECUTE ON FUNCTION pg_catalog.set_config(text, text, boolean) FROM PUBLIC")
            conn.execute(
                sql.SQL(
                    "GRANT EXECUTE ON FUNCTION pg_catalog.set_config(text, text, boolean) TO {}, {}"
                ).format(owner, app)
            )
        restricted = not query_role_can_set_config(conn, plan.query.name)
        if not restricted:
            logger.warning(
                "set_config_not_restricted",
                extra={
                    "fields": {
                        "query_role": plan.query.name,
                        "reason": "the admin role is not a superuser, so set_config cannot be revoked",
                    }
                },
            )
        return restricted


def query_role_can_set_config(conn: psycopg.Connection[Any], role: str) -> bool:
    row = conn.execute(
        "SELECT has_function_privilege(%s, 'pg_catalog.set_config(text, text, boolean)', 'EXECUTE')", (role,)
    ).fetchone()
    return bool(row and row[0])


def migrate(owner_url: str) -> None:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.attributes["url"] = owner_url
    command.upgrade(config, "head")


def apply_grants(owner_url: str, plan: DatabasePlan) -> None:
    app, query = sql.Identifier(plan.app.name), sql.Identifier(plan.query.name)
    with psycopg.connect(libpq_url(owner_url), autocommit=True) as conn:
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}, {}").format(app, query))
        for table in READ_WRITE_TABLES:
            conn.execute(
                sql.SQL("GRANT SELECT, INSERT, UPDATE ON {} TO {}").format(sql.Identifier(table), app)
            )
        for table in APPEND_ONLY_TABLES:
            conn.execute(sql.SQL("GRANT SELECT, INSERT ON {} TO {}").format(sql.Identifier(table), app))
            conn.execute(
                sql.SQL("REVOKE UPDATE, DELETE, TRUNCATE ON {} FROM {}").format(sql.Identifier(table), app)
            )
        for view in QUERY_VIEWS:
            conn.execute(sql.SQL("GRANT SELECT ON {} TO {}, {}").format(sql.Identifier(view), app, query))


@dataclass(frozen=True, slots=True)
class ProvisionResult:
    plan: DatabasePlan
    set_config_restricted: bool


def provision(*, admin_url: str, owner_url: str, app_url: str, query_url: str) -> ProvisionResult:
    plan = plan_from_urls(owner_url, app_url, query_url)

    def done(step: str) -> None:
        logger.info("provision_step_done", extra={"fields": {"step": step, "database": plan.database}})

    ensure_roles(admin_url, plan)
    done("roles")
    ensure_database(admin_url, plan)
    done("database")
    restricted = harden_database(admin_url, plan)
    done("hardening")
    migrate(owner_url)
    done("migrations")
    apply_grants(owner_url, plan)
    done("grants")
    return ProvisionResult(plan=plan, set_config_restricted=restricted)
