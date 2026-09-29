"""Identity endpoints: who am I, and (development only) a login for the seeded demo users."""

from fastapi import APIRouter, Request

from attendance_ai.core.access import Scope
from attendance_ai.core.directory import Directory, User
from attendance_ai.core.errors import AuthenticationError, PermissionDeniedError
from attendance_ai.gateway.auth import issue_token
from attendance_ai.gateway.deps import CurrentAccess, get_audit, get_directory, get_settings
from attendance_ai.gateway.schemas import (
    AccessContextResponse,
    DepartmentOut,
    DevTokenRequest,
    DevUser,
    DevUsersResponse,
    TokenResponse,
)
from attendance_ai.governance.audit import AuditEvent

router = APIRouter(prefix="/api/v1", tags=["auth"])

# Mounted only when AUTH_DEV_LOGIN_ENABLED=true; otherwise these endpoints do not exist at all.
dev_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/me", response_model=AccessContextResponse)
def me(request: Request, ctx: CurrentAccess) -> AccessContextResponse:
    directory = get_directory(request)
    tenant = directory.get_tenant(ctx.tenant_id)
    user = directory.find_user(ctx.user_id)
    if tenant is None or user is None:
        raise AuthenticationError("Unknown user or tenant.")
    if ctx.scope is Scope.TENANT:
        visible = list(tenant.departments.values())
    elif ctx.scope is Scope.DEPARTMENT:
        visible = [tenant.departments[entity] for entity in ctx.entity_scope]
    else:
        employee = tenant.employees.get(ctx.employee_id or "")
        visible = [tenant.departments[employee.entity_id]] if employee else []
    return AccessContextResponse(
        **ctx.describe(),
        tenant_name=tenant.name,
        display_name=user.display_name,
        departments=[DepartmentOut(entity_id=d.entity_id, name=d.name) for d in visible],
    )


@dev_router.get("/dev-users", response_model=DevUsersResponse)
def dev_users(request: Request) -> DevUsersResponse:
    """The seeded demo users, for the development sign-in screen."""
    directory = get_directory(request)
    users: list[DevUser] = []
    for tenant_id in directory.tenant_ids:
        tenant = directory.get_tenant(tenant_id)
        if tenant is None:
            continue
        for user in tenant.users.values():
            users.append(
                DevUser(
                    user_id=user.user_id,
                    display_name=user.display_name,
                    tenant_id=tenant.tenant_id,
                    tenant_name=tenant.name,
                    role=user.role,
                    scope=_scope_of(directory, user).value,
                    scope_description=_describe_scope(directory, user),
                )
            )
    return DevUsersResponse(users=users)


@dev_router.post("/dev-token", response_model=TokenResponse)
def dev_token(body: DevTokenRequest, request: Request) -> TokenResponse:
    """Issue a token for a seeded demo user. Stands in for an identity provider in development."""
    settings = get_settings(request)
    directory = get_directory(request)
    user = directory.find_user(body.user_id)
    if user is None:
        get_audit(request).record(
            AuditEvent("auth", "dev_token_denied", "denied", details={"user_id": body.user_id}),
            request_id=request.state.request_id,
        )
        raise AuthenticationError("Unknown demo user.")
    if not directory.supports(body.product_id, settings.service_module):
        raise PermissionDeniedError(f"Product '{body.product_id}' does not provide this service.")
    token, expires_in = issue_token(settings, user, body.product_id)
    ctx = directory.build_context(
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        product_id=body.product_id,
        module=settings.service_module,
        request_id=request.state.request_id,
    )
    get_audit(request).record(AuditEvent("auth", "dev_token_issued", "success"), ctx)
    return TokenResponse(access_token=token, expires_in=expires_in)


def _scope_of(directory: Directory, user: User) -> Scope:
    role = directory.role(user.role)
    return role.scope if role else Scope.SELF


def _describe_scope(directory: Directory, user: User) -> str:
    tenant = directory.get_tenant(user.tenant_id)
    scope = _scope_of(directory, user)
    if scope is Scope.TENANT or tenant is None:
        return "Whole tenant"
    if scope is Scope.DEPARTMENT:
        return "Departments: " + ", ".join(tenant.departments[e].name for e in user.entity_scope)
    return "Own records only"
