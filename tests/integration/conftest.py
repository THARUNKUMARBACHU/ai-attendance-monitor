"""Integration fixtures: provision a throwaway database, with unique role names, on a real server.

Set TEST_DATABASE_ADMIN_URL to a PostgreSQL superuser URL, for example
postgresql+psycopg://postgres:secret@localhost:5432/postgres. Everything created is dropped afterwards.
"""

import itertools
import os
import secrets
import uuid
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from typing import Any

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

from attendance_ai.core.access import AccessContext
from attendance_ai.core.directory import Directory
from attendance_ai.stores.db import Database
from attendance_ai.stores.models import AttendanceRecord, Source, SourceVersion
from attendance_ai.stores.setup import libpq_url, provision, query_role_can_set_config

from ..support import make_settings

ADMIN_URL_ENV = "TEST_DATABASE_ADMIN_URL"
_day_offsets = itertools.count()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "integration" in item.nodeid:
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def admin_url() -> str:
    url = os.environ.get(ADMIN_URL_ENV)
    if not url:
        pytest.skip(f"Set {ADMIN_URL_ENV} to a PostgreSQL admin URL to run integration tests.")
    return url


@pytest.fixture(scope="session")
def pg_urls(admin_url: str) -> Iterator[dict[str, str]]:
    suffix = uuid.uuid4().hex[:8]
    database = f"attendance_ai_test_{suffix}"
    base = make_url(admin_url)
    urls = {
        kind: base.set(
            username=f"ai_{kind}_{suffix}", password=secrets.token_urlsafe(18), database=database
        ).render_as_string(hide_password=False)
        for kind in ("owner", "app", "query")
    }
    provision(admin_url=admin_url, owner_url=urls["owner"], app_url=urls["app"], query_url=urls["query"])
    try:
        yield urls
    finally:
        with psycopg.connect(libpq_url(admin_url), autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database)))
            for url in urls.values():
                conn.execute(
                    sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(str(make_url(url).username)))
                )


@pytest.fixture(scope="session")
def set_config_restricted(admin_url: str, pg_urls: dict[str, str]) -> bool:
    """Whether this server let provisioning revoke set_config from the query role. Managed services
    without a superuser (for example Aiven) do not."""
    query = make_url(pg_urls["query"])
    with psycopg.connect(libpq_url(admin_url, query.database)) as conn:
        return not query_role_can_set_config(conn, str(query.username))


@pytest.fixture(scope="session")
def database(pg_urls: dict[str, str]) -> Iterator[Database]:
    db = Database(
        make_settings(
            database_url=pg_urls["app"],
            database_query_url=pg_urls["query"],
            database_owner_url=pg_urls["owner"],
            db_pool_size=2,  # managed servers can have as few as 20 connections
        )
    )
    yield db
    db.dispose()


@pytest.fixture(scope="session")
def ctx_for(directory: Directory) -> Callable[[str], AccessContext]:
    def _ctx(user_id: str) -> AccessContext:
        user = directory.find_user(user_id)
        assert user is not None, user_id
        return directory.build_context(
            tenant_id=user.tenant_id,
            user_id=user_id,
            product_id="hrms",
            module="attendance",
            request_id=f"req_{uuid.uuid4().hex[:12]}",
        )

    return _ctx


@pytest.fixture(scope="session")
def system_ctx(directory: Directory) -> Callable[[str], AccessContext]:
    def _ctx(tenant_id: str) -> AccessContext:
        return directory.system_context(
            tenant_id=tenant_id,
            product_id="hrms",
            module="attendance",
            request_id=f"job_{uuid.uuid4().hex[:12]}",
        )

    return _ctx


@pytest.fixture
def new_source(
    database: Database, system_ctx: Callable[[str], AccessContext]
) -> Callable[..., tuple[Any, Any, str]]:
    """Create a source and its first version for a tenant; returns (source_id, version_id, file name)."""

    def _new(tenant_id: str, name: str | None = None) -> tuple[Any, Any, str]:
        name = name or f"test_{uuid.uuid4().hex[:10]}.csv"
        with database.session_for(system_ctx(tenant_id)) as session:
            source = Source(
                tenant_id=tenant_id,
                product_id="hrms",
                module="attendance",
                logical_name=name,
                created_by="tests",
            )
            session.add(source)
            session.flush()
            version = SourceVersion(
                source_id=source.id,
                tenant_id=tenant_id,
                product_id="hrms",
                module="attendance",
                version_no=1,
                checksum=uuid.uuid4().hex,
                media_type="text/csv",
                size_bytes=1,
                storage_path=f"tests/{name}",
                original_filename=name,
                created_by="tests",
            )
            session.add(version)
            session.flush()
            return source.id, version.id, name

    return _new


@pytest.fixture
def record_values() -> Callable[..., dict[str, Any]]:
    """Column values for one attendance record. Each call gets its own date, so tests never collide
    on the one-active-record-per-employee-per-day index."""

    def _values(
        tenant_id: str, source_id: Any, version_id: Any, name: str, **overrides: Any
    ) -> dict[str, Any]:
        values: dict[str, Any] = {
            "record_key": uuid.uuid4().hex[:16],
            "tenant_id": tenant_id,
            "product_id": "hrms",
            "module": "attendance",
            "entity_id": "ENG",
            "department": "Engineering",
            "employee_id": "E001",
            "employee_name": "Test Person",
            "attendance_date": date(2030, 1, 1) + timedelta(days=next(_day_offsets)),
            "status": "PRESENT",
            "source_id": source_id,
            "source_version_id": version_id,
            "source_file": name,
            "source_locator": {"type": "row", "row": 2},
            "source_page_or_row": "row 2",
            "extraction_method": "native_csv",
        }
        values.update(overrides)
        return values

    return _values


@pytest.fixture
def add_records(
    database: Database,
    system_ctx: Callable[[str], AccessContext],
    new_source: Callable[..., tuple[Any, Any, str]],
    record_values: Callable[..., dict[str, Any]],
) -> Callable[..., str]:
    """Insert records for a tenant under its system context; returns the source file name."""

    def _add(tenant_id: str, rows: list[dict[str, Any]], *, source_file: str | None = None) -> str:
        source_id, version_id, name = new_source(tenant_id, source_file)
        with database.session_for(system_ctx(tenant_id)) as session:
            for row in rows:
                session.add(AttendanceRecord(**record_values(tenant_id, source_id, version_id, name, **row)))
        return name

    return _add
