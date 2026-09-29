"""The ORM models must match what the migrations create, and provisioning must be repeatable and
report its security outcome truthfully."""

import psycopg
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url

from attendance_ai.stores.models import Base
from attendance_ai.stores.setup import libpq_url, provision, query_role_can_set_config


def test_models_match_the_migrated_schema(pg_urls: dict[str, str]) -> None:
    engine = create_engine(pg_urls["owner"])
    try:
        inspector = inspect(engine)
        for table in Base.metadata.tables.values():
            columns = {column["name"]: column for column in inspector.get_columns(table.name)}
            assert set(columns) == {column.name for column in table.columns}, table.name
            for column in table.columns:
                assert columns[column.name]["nullable"] == column.nullable, f"{table.name}.{column.name}"
    finally:
        engine.dispose()


def test_provisioning_is_idempotent_and_reports_set_config_truthfully(
    admin_url: str, pg_urls: dict[str, str]
) -> None:
    result = provision(
        admin_url=admin_url, owner_url=pg_urls["owner"], app_url=pg_urls["app"], query_url=pg_urls["query"]
    )
    assert result.plan.database.startswith("attendance_ai_test_")
    with psycopg.connect(libpq_url(admin_url, result.plan.database)) as conn:
        can_set = query_role_can_set_config(conn, str(make_url(pg_urls["query"]).username))
    assert result.set_config_restricted is (not can_set)
