"""The access context: who is asking, and exactly which data they may see."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from attendance_ai.core.errors import PermissionDeniedError

CLEARANCE_LEVELS: tuple[str, ...] = ("public", "internal", "confidential", "restricted")


def clearance_rank(level: str) -> int:
    try:
        return CLEARANCE_LEVELS.index(level)
    except ValueError:
        raise ValueError(f"Unknown classification level: {level!r}") from None


class Scope(StrEnum):
    TENANT = "tenant"
    DEPARTMENT = "department"
    SELF = "self"


@dataclass(frozen=True, slots=True)
class AccessContext:
    """Built once per request from the verified token and the directory, then passed to every
    service and store call. Nothing reads tenant data without one."""

    request_id: str
    tenant_id: str
    product_id: str
    module: str
    user_id: str
    role: str
    scope: Scope
    clearance: int
    permissions: frozenset[str]
    entity_scope: tuple[str, ...] = ()
    employee_id: str | None = None
    attendance_weights: tuple[tuple[str, float | None], ...] = ()
    late_arrival_after: str = "09:30"

    def require(self, permission: str) -> None:
        if permission not in self.permissions:
            raise PermissionDeniedError(f"Your role ({self.role}) does not allow '{permission}'.")

    def db_settings(self) -> dict[str, str]:
        """The per-transaction settings that PostgreSQL row-level security policies read."""
        return {
            "app.tenant_id": self.tenant_id,
            "app.product_id": self.product_id,
            "app.module": self.module,
            "app.user_id": self.user_id,
            "app.scope": self.scope.value,
            "app.entity_scope": ",".join(self.entity_scope),
            "app.employee_id": self.employee_id or "",
            "app.clearance": str(self.clearance),
            "app.weights": json.dumps(dict(self.attendance_weights), sort_keys=True),
            "app.late_after": self.late_arrival_after,
        }

    def describe(self) -> dict[str, Any]:
        """The context as shown to API callers: no secrets, no internals."""
        return {
            "tenant_id": self.tenant_id,
            "product_id": self.product_id,
            "module": self.module,
            "user_id": self.user_id,
            "role": self.role,
            "scope": self.scope.value,
            "entity_scope": list(self.entity_scope),
            "employee_id": self.employee_id,
            "clearance": CLEARANCE_LEVELS[self.clearance],
            "permissions": sorted(self.permissions),
        }
