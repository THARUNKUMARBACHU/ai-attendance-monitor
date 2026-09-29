"""Provision PostgreSQL for the service: roles, database, hardening, migrations and grants.

Usage:
    uv run python scripts/db_setup.py

Reads DATABASE_ADMIN_URL, DATABASE_OWNER_URL, DATABASE_URL and DATABASE_QUERY_URL from the
environment or .env. Safe to run repeatedly.
"""

import sys

from attendance_ai.core.config import get_settings
from attendance_ai.core.logging import configure_logging
from attendance_ai.stores.setup import provision


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.database_admin_url is None:
        print("DATABASE_ADMIN_URL is not set. It needs a role that can create roles and databases.")
        return 2
    result = provision(
        admin_url=settings.database_admin_url.get_secret_value(),
        owner_url=settings.database_owner_url.get_secret_value(),
        app_url=settings.database_url.get_secret_value(),
        query_url=settings.database_query_url.get_secret_value(),
    )
    plan = result.plan
    print(
        f"Database '{plan.database}' is ready. Roles: {plan.owner.name}, {plan.app.name}, {plan.query.name}."
    )
    if not result.set_config_restricted:
        print(
            f"WARNING: role '{plan.query.name}' can still call set_config(). Revoking it needs a superuser, "
            "which this server does not provide. The SQL guard is the barrier for LLM-written SQL."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
