"""Request and response models for the HTTP API."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DevTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,63}$", examples=["acme.admin"])
    product_id: str = Field(default="hrms", pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
    access_code: str | None = Field(
        default=None, max_length=200, description="Required when the deployment sets DEMO_ACCESS_CODE."
    )


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - the OAuth token type, not a secret
    expires_in: int = Field(description="Seconds until the token expires.")


class DepartmentOut(BaseModel):
    entity_id: str
    name: str


class AccessContextResponse(BaseModel):
    tenant_id: str
    tenant_name: str
    product_id: str
    module: str
    user_id: str
    display_name: str
    role: str
    scope: str
    entity_scope: list[str]
    employee_id: str | None
    clearance: str
    permissions: list[str]
    departments: list[DepartmentOut]


class DevUser(BaseModel):
    user_id: str
    display_name: str
    tenant_id: str
    tenant_name: str
    role: str
    scope: str
    scope_description: str


class DevUsersResponse(BaseModel):
    users: list[DevUser]
    access_code_required: bool = False


class CheckResponse(BaseModel):
    status: Literal["up", "down", "configured", "not_configured"]
    latency_ms: int | None = None
    detail: str | None = None


class ReadinessResponse(BaseModel):
    status: Literal["ok", "degraded"]
    checks: dict[str, CheckResponse]
