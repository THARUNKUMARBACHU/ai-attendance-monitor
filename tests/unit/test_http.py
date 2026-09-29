from typing import Any

import pytest
from fastapi.testclient import TestClient

from attendance_ai.core.directory import Directory
from attendance_ai.stores.db import apply_access_settings, quote_literal, set_local_statements


def test_valid_request_id_is_echoed(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "trace-1234abcd"})
    assert response.headers["x-request-id"] == "trace-1234abcd"


def test_unsafe_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "bad id\r\ninjected"})
    assert response.headers["x-request-id"].startswith("req_")


def test_validation_errors_do_not_echo_input(client: TestClient) -> None:
    response = client.post("/api/v1/auth/dev-token", json={"user_id": "Robert'); DROP TABLE users;--"})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert "DROP TABLE" not in response.text
    assert body["errors"][0]["loc"] == ["body", "user_id"]


def test_quote_literal_escapes_quotes_and_rejects_nul() -> None:
    assert quote_literal("O'Brien") == "'O''Brien'"
    with pytest.raises(ValueError):
        quote_literal("bad\x00value")


def test_set_local_statements_cover_every_setting(directory: Directory) -> None:
    ctx = directory.build_context(
        tenant_id="acme",
        user_id="acme.employee",
        product_id="hrms",
        module="attendance",
        request_id="req_12345678",
    )
    statements = set_local_statements(ctx)
    assert len(statements) == len(ctx.db_settings())
    assert "SET LOCAL app.tenant_id = 'acme'" in statements
    assert "SET LOCAL app.scope = 'self'" in statements


class _RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement: Any, params: dict[str, Any]) -> None:
        self.calls.append((str(statement), params))


def test_apply_access_settings_uses_bind_parameters(directory: Directory) -> None:
    ctx = directory.build_context(
        tenant_id="globex",
        user_id="globex.admin",
        product_id="hrms",
        module="attendance",
        request_id="req_12345678",
    )
    connection = _RecordingConnection()
    apply_access_settings(connection, ctx)  # type: ignore[arg-type]
    ((sql, params),) = connection.calls
    assert sql.startswith("SELECT set_config(:name_0, :value_0, true)")
    assert "globex" not in sql
    assert dict(zip(params.values(), list(params.values())[1:], strict=False))
    assert params["name_0"] == "app.tenant_id" and params["value_0"] == "globex"
