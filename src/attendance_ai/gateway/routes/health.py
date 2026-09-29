"""Liveness and readiness. Check names follow the specification's diagnostics list."""

from collections.abc import Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from attendance_ai.gateway.deps import get_settings
from attendance_ai.gateway.schemas import CheckResponse, ReadinessResponse
from attendance_ai.stores.health import CheckResult, ping_qdrant, run_checks

router = APIRouter(tags=["health"])


@router.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", response_model=ReadinessResponse, responses={503: {"model": ReadinessResponse}})
def ready(request: Request) -> JSONResponse:
    settings = get_settings(request)
    state = request.app.state
    probes: dict[str, Callable[[], object]] = {"database": state.database.ping, "redis": state.redis.ping}
    if settings.qdrant_url:
        probes["vector"] = lambda: ping_qdrant(settings)
    results = run_checks(probes, deadline_seconds=settings.health_check_timeout_seconds + 0.5)
    database, redis = results["database"], results["redis"]
    vector = results.get("vector", CheckResult("not_configured"))
    provider = CheckResult(
        "configured" if settings.llm_api_key else "not_configured", detail=settings.llm_provider
    )
    checks = {
        "service": CheckResult("up"),
        "database": database,
        "queue": redis,
        "cache": redis,
        "search": vector,
        "vector": vector,
        "model_provider": provider,
    }
    healthy = all(check.status != "down" for check in checks.values())
    body = ReadinessResponse(
        status="ok" if healthy else "degraded",
        checks={
            name: CheckResponse(status=c.status, latency_ms=c.latency_ms, detail=c.detail)
            for name, c in checks.items()
        },
    )
    return JSONResponse(body.model_dump(), status_code=200 if healthy else 503)
