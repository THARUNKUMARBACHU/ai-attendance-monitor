from collections.abc import Callable

from fastapi.testclient import TestClient

from ..support import make_settings


def test_liveness(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}


def test_ready_when_database_and_redis_are_up(client: TestClient) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    checks = body["checks"]
    assert set(checks) == {"service", "database", "queue", "cache", "search", "vector", "model_provider"}
    assert checks["database"]["status"] == "up"
    assert checks["queue"]["status"] == checks["cache"]["status"] == "up"
    assert checks["vector"]["status"] == "not_configured"
    assert checks["model_provider"]["status"] == "not_configured"


def test_degraded_when_database_is_down(make_client: Callable[..., TestClient]) -> None:
    response = make_client(db_healthy=False).get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["database"] == {
        "status": "down",
        "latency_ms": body["checks"]["database"]["latency_ms"],
        "detail": "ConnectionError",
    }


def test_degraded_when_redis_is_down(make_client: Callable[..., TestClient]) -> None:
    response = make_client(redis_healthy=False).get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["queue"]["status"] == "down"


def test_model_provider_reported_as_configured(make_client: Callable[..., TestClient]) -> None:
    client = make_client(make_settings(openrouter_api_key="sk-or-test"))
    provider = client.get("/health/ready").json()["checks"]["model_provider"]
    assert provider == {"status": "configured", "latency_ms": None, "detail": "openrouter"}


def test_slow_checks_cannot_stall_readiness() -> None:
    import time

    from attendance_ai.stores.health import run_checks

    started = time.perf_counter()
    results = run_checks({"fast": lambda: None, "hung": lambda: time.sleep(3)}, deadline_seconds=0.3)
    assert time.perf_counter() - started < 1.5
    assert results["fast"].status == "up"
    assert (results["hung"].status, results["hung"].detail) == ("down", "Timeout")


def test_unreachable_qdrant_is_down(make_client: Callable[..., TestClient]) -> None:
    client = make_client(make_settings(qdrant_url="http://127.0.0.1:9", health_check_timeout_seconds=0.5))
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["vector"]["status"] == "down"
