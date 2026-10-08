from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from attendance_ai.core.config import Settings
from attendance_ai.core.directory import Directory
from attendance_ai.core.errors import AuthenticationError
from attendance_ai.gateway.auth import ALGORITHM, issue_token, verify_token

from ..support import RecordingAudit, make_settings


def _claims(settings: Settings, **overrides: object) -> dict[str, object]:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": "acme.admin",
        "tenant_id": "acme",
        "product_id": "hrms",
        "role": "admin",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return claims


def _sign(settings: Settings, claims: dict[str, object]) -> str:
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=ALGORITHM)


def test_issue_and_verify_round_trip(settings: Settings, directory: Directory) -> None:
    user = directory.find_user("acme.eng.manager")
    assert user is not None
    token, expires_in = issue_token(settings, user, "hrms")
    claims = verify_token(settings, token)
    assert (claims.sub, claims.tenant_id, claims.role) == ("acme.eng.manager", "acme", "manager")
    assert expires_in == settings.jwt_ttl_minutes * 60


@pytest.mark.parametrize(
    "overrides",
    [
        {"exp": datetime.now(UTC) - timedelta(minutes=10)},
        {"aud": "some-other-api"},
        {"iss": "someone-else"},
    ],
)
def test_rejects_expired_or_foreign_tokens(settings: Settings, overrides: dict[str, object]) -> None:
    with pytest.raises(AuthenticationError):
        verify_token(settings, _sign(settings, _claims(settings, **overrides)))


def test_rejects_wrong_signature_and_unsigned_tokens(settings: Settings) -> None:
    forged = jwt.encode(_claims(settings), "another-secret-that-is-long-enough-000000", algorithm=ALGORITHM)
    with pytest.raises(AuthenticationError):
        verify_token(settings, forged)
    unsigned = jwt.encode(_claims(settings), key=None, algorithm="none")  # type: ignore[arg-type]
    with pytest.raises(AuthenticationError):
        verify_token(settings, unsigned)


def test_rejects_tokens_missing_claims(settings: Settings) -> None:
    claims = _claims(settings)
    del claims["tenant_id"]
    with pytest.raises(AuthenticationError, match="missing required claims"):
        verify_token(settings, _sign(settings, claims))


def test_dev_token_and_me(client: TestClient, token_for: Callable[[str], str], audit: RecordingAudit) -> None:
    response = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token_for('acme.eng.manager')}"})
    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == "acme"
    assert body["scope"] == "department"
    assert body["entity_scope"] == ["ENG"]
    assert body["clearance"] == "confidential"
    assert "dev_token_issued" in audit.actions()


def test_dev_login_route_is_absent_when_disabled(make_client: Callable[..., TestClient]) -> None:
    client = make_client(make_settings(auth_dev_login_enabled=False))
    assert client.post("/api/v1/auth/dev-token", json={"user_id": "acme.admin"}).status_code == 404


def test_unknown_demo_user_is_rejected_and_audited(client: TestClient, audit: RecordingAudit) -> None:
    response = client.post("/api/v1/auth/dev-token", json={"user_id": "nobody.here"})
    assert response.status_code == 401
    assert "dev_token_denied" in audit.actions()


def test_missing_token_gets_problem_details(client: TestClient) -> None:
    response = client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["www-authenticate"] == "Bearer"
    body = response.json()
    assert body["code"] == "unauthenticated"
    assert body["request_id"] == response.headers["x-request-id"]


def test_tenant_header_cannot_override_token(
    client: TestClient, token_for: Callable[[str], str], audit: RecordingAudit
) -> None:
    response = client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {token_for('acme.admin')}", "X-Tenant-ID": "globex"},
    )
    assert response.status_code == 403
    assert "tenant_mismatch" in audit.actions()


def test_matching_tenant_header_is_accepted(client: TestClient, token_for: Callable[[str], str]) -> None:
    response = client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {token_for('globex.admin')}", "X-Tenant-ID": "globex"},
    )
    assert response.status_code == 200
    assert response.json()["tenant_id"] == "globex"


def test_stale_role_in_token_is_rejected(client: TestClient, settings: Settings) -> None:
    # The directory says acme.eng.manager is a manager; a token claiming admin must not be honoured.
    token = _sign(settings, _claims(settings, sub="acme.eng.manager", role="admin"))
    response = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_token_for_other_tenant_user_is_rejected(client: TestClient, settings: Settings) -> None:
    token = _sign(settings, _claims(settings, sub="globex.admin", tenant_id="acme"))
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


DEMO_CODE = "invite-only-2026"


def test_access_code_is_required_when_the_deployment_sets_one(
    make_client: Callable[..., TestClient], audit: RecordingAudit
) -> None:
    client = make_client(make_settings(demo_access_code=DEMO_CODE))
    assert client.get("/api/v1/auth/dev-users").json()["access_code_required"] is True
    for body in ({"user_id": "acme.admin"}, {"user_id": "acme.admin", "access_code": "wrong-guess-123"}):
        response = client.post("/api/v1/auth/dev-token", json=body)
        assert response.status_code == 401
        assert "access code" in response.json()["detail"]
    assert audit.actions().count("dev_token_denied") == 2
    assert all("wrong-guess-123" not in str(event.details) for event, _ in audit.events)
    response = client.post("/api/v1/auth/dev-token", json={"user_id": "acme.admin", "access_code": DEMO_CODE})
    assert response.status_code == 200


@pytest.mark.parametrize("code", [None, ""])
def test_no_access_code_is_asked_for_unless_one_is_set(
    make_client: Callable[..., TestClient], code: str | None
) -> None:
    client = make_client(make_settings(demo_access_code=code))
    assert client.get("/api/v1/auth/dev-users").json()["access_code_required"] is False
    assert client.post("/api/v1/auth/dev-token", json={"user_id": "acme.admin"}).status_code == 200
