import pytest

from attendance_ai.stores.setup import libpq_url, plan_from_urls

OWNER = "postgresql+psycopg://ai_owner:o@db:5432/attendance_ai"
APP = "postgresql+psycopg://ai_app:a@db:5432/attendance_ai"
QUERY = "postgresql+psycopg://ai_query:q@db:5432/attendance_ai"


def test_plan_reads_roles_and_database_from_urls() -> None:
    plan = plan_from_urls(OWNER, APP, QUERY)
    assert plan.database == "attendance_ai"
    assert (plan.owner.name, plan.app.name, plan.query.name) == ("ai_owner", "ai_app", "ai_query")
    assert plan.app.password == "a"


def test_plan_requires_one_database() -> None:
    with pytest.raises(ValueError, match="same database"):
        plan_from_urls(OWNER, APP.replace("attendance_ai", "other_db"), QUERY)


def test_plan_requires_three_distinct_roles() -> None:
    with pytest.raises(ValueError, match="three different roles"):
        plan_from_urls(OWNER, APP, APP)


def test_libpq_url_drops_the_driver_and_can_switch_database() -> None:
    assert libpq_url(APP) == "postgresql://ai_app:a@db:5432/attendance_ai"
    assert libpq_url(APP, "postgres").endswith("/postgres")
