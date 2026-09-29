"""FastAPI dependencies: shared app state and the per-request access context."""

from collections.abc import Callable
from typing import Annotated, cast

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from attendance_ai.core.access import AccessContext
from attendance_ai.core.config import Settings
from attendance_ai.core.directory import Directory
from attendance_ai.core.errors import AuthenticationError, PermissionDeniedError
from attendance_ai.gateway.auth import verify_token
from attendance_ai.governance.audit import AuditEvent, AuditSink

_bearer = HTTPBearer(auto_error=False)


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_directory(request: Request) -> Directory:
    return cast(Directory, request.app.state.directory)


def get_audit(request: Request) -> AuditSink:
    return cast(AuditSink, request.app.state.audit)


def get_access_context(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    x_tenant_id: Annotated[str | None, Header(description="Optional; must match the token's tenant.")] = None,
    x_product_id: Annotated[
        str | None, Header(description="Optional; must match the token's product.")
    ] = None,
) -> AccessContext:
    """Verify the bearer token and resolve the caller's access. Tenant and product always come from
    the token; a request may repeat them in headers but never change them."""
    if credentials is None:
        raise AuthenticationError("A bearer token is required.")
    settings = get_settings(request)
    claims = verify_token(settings, credentials.credentials)
    ctx = get_directory(request).build_context(
        tenant_id=claims.tenant_id,
        user_id=claims.sub,
        product_id=claims.product_id,
        module=settings.service_module,
        request_id=request.state.request_id,
    )
    if ctx.role != claims.role:
        raise AuthenticationError("Your role has changed since this token was issued. Log in again.")
    for requested, actual, name in (
        (x_tenant_id, ctx.tenant_id, "tenant"),
        (x_product_id, ctx.product_id, "product"),
    ):
        if requested is not None and requested != actual:
            get_audit(request).record(
                AuditEvent("denial", f"{name}_mismatch", "denied", details={"requested": requested}), ctx
            )
            raise PermissionDeniedError(f"The requested {name} does not match your token.")
    return ctx


def require_permission(permission: str) -> Callable[..., AccessContext]:
    def dependency(ctx: Annotated[AccessContext, Depends(get_access_context)]) -> AccessContext:
        ctx.require(permission)
        return ctx

    return dependency


CurrentAccess = Annotated[AccessContext, Depends(get_access_context)]
