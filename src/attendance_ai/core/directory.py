"""Access metadata (products, roles, tenants, departments, employees, users) from the seed file.

The seed stands in for the reference design's "Access Metadata" source and the product's HR
database. It is loaded once at startup, validated, and treated as read-only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any

from attendance_ai.core.access import AccessContext, Scope, clearance_rank
from attendance_ai.core.domain import AttendanceStatus
from attendance_ai.core.errors import AuthenticationError, NotFoundError, PermissionDeniedError

SYSTEM_ROLE = "system"

_TENANT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")
_USER_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
# Product, module, department and employee IDs. No commas: department scopes are comma-joined.
_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
_CLOCK_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class SeedError(ValueError):
    """The seed file is missing, malformed or inconsistent."""


@dataclass(frozen=True, slots=True)
class RoleDefinition:
    name: str
    scope: Scope
    clearance: int
    permissions: frozenset[str]


@dataclass(frozen=True, slots=True)
class Department:
    entity_id: str
    name: str
    manager_employee_id: str | None


@dataclass(frozen=True, slots=True)
class Employee:
    employee_id: str
    name: str
    entity_id: str
    email: str | None
    is_manager: bool


@dataclass(frozen=True, slots=True)
class User:
    user_id: str
    tenant_id: str
    role: str
    display_name: str
    employee_id: str | None
    entity_scope: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TenantConfig:
    timezone: str
    date_format: str
    week_off_days: tuple[str, ...]
    late_arrival_after: str
    ocr_review_threshold: float
    status_codes: Mapping[str, str]
    attendance_weights: tuple[tuple[str, float | None], ...]
    rounding_decimals: int


@dataclass(frozen=True, slots=True)
class Tenant:
    tenant_id: str
    name: str
    config: TenantConfig
    departments: Mapping[str, Department]
    employees: Mapping[str, Employee]
    users: Mapping[str, User]
    holidays: tuple[date, ...]


class Directory:
    """Read-only lookup of who exists and what each role may do."""

    def __init__(
        self,
        *,
        products: Mapping[str, frozenset[str]],
        roles: Mapping[str, RoleDefinition],
        tenants: Mapping[str, Tenant],
    ) -> None:
        self._products = dict(products)
        self._roles = dict(roles)
        self._tenants = dict(tenants)
        self._users: dict[str, User] = {}
        for tenant in self._tenants.values():
            for user in tenant.users.values():
                if user.user_id in self._users:
                    raise SeedError(f"Duplicate user_id across tenants: {user.user_id}")
                self._users[user.user_id] = user

    @classmethod
    def from_file(cls, path: Path) -> Directory:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SeedError(f"Cannot read seed file {path}: {exc}") from exc
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Directory:
        products = {
            _code(product.get("product_id"), "product_id"): frozenset(
                _code(module.get("module_id"), "module_id") for module in product.get("modules", [])
            )
            for product in data.get("products", [])
        }
        roles = {name: _parse_role(name, raw) for name, raw in data.get("roles", {}).items()}
        tenants: dict[str, Tenant] = {}
        for raw in data.get("tenants", []):
            tenant = _parse_tenant(raw, roles)
            if tenant.tenant_id in tenants:
                raise SeedError(f"Duplicate tenant_id: {tenant.tenant_id}")
            tenants[tenant.tenant_id] = tenant
        if not products or not roles or not tenants:
            raise SeedError("The seed must define products, roles and tenants.")
        return cls(products=products, roles=roles, tenants=tenants)

    @property
    def tenant_ids(self) -> tuple[str, ...]:
        return tuple(self._tenants)

    @property
    def product_modules(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (product, module) for product, modules in self._products.items() for module in sorted(modules)
        )

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        return self._tenants.get(tenant_id)

    def find_user(self, user_id: str) -> User | None:
        return self._users.get(user_id)

    def role(self, name: str) -> RoleDefinition | None:
        return self._roles.get(name)

    def supports(self, product_id: str, module: str) -> bool:
        return module in self._products.get(product_id, frozenset())

    def build_context(
        self, *, tenant_id: str, user_id: str, product_id: str, module: str, request_id: str
    ) -> AccessContext:
        """Resolve a verified identity into an access context. Scope comes from the role alone:
        tenant roles see everything, department roles their entity_scope, self roles their records."""
        tenant = self._tenants.get(tenant_id)
        user = tenant.users.get(user_id) if tenant else None
        if tenant is None or user is None:
            raise AuthenticationError("Unknown user or tenant.")
        if not self.supports(product_id, module):
            raise PermissionDeniedError(f"Product '{product_id}' does not provide the '{module}' module.")
        role = self._roles[user.role]
        return AccessContext(
            request_id=request_id,
            tenant_id=tenant.tenant_id,
            product_id=product_id,
            module=module,
            user_id=user.user_id,
            role=role.name,
            scope=role.scope,
            clearance=role.clearance,
            permissions=role.permissions,
            entity_scope=user.entity_scope if role.scope is Scope.DEPARTMENT else (),
            employee_id=user.employee_id,
            attendance_weights=tenant.config.attendance_weights,
            late_arrival_after=tenant.config.late_arrival_after,
        )

    def system_context(
        self, *, tenant_id: str, product_id: str, module: str, request_id: str
    ) -> AccessContext:
        """Context for background work done on a tenant's behalf (for example, ingestion jobs)."""
        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            raise NotFoundError(f"Unknown tenant: {tenant_id}")
        if not self.supports(product_id, module):
            raise PermissionDeniedError(f"Product '{product_id}' does not provide the '{module}' module.")
        return AccessContext(
            request_id=request_id,
            tenant_id=tenant.tenant_id,
            product_id=product_id,
            module=module,
            user_id=SYSTEM_ROLE,
            role=SYSTEM_ROLE,
            scope=Scope.TENANT,
            clearance=clearance_rank("restricted"),
            permissions=frozenset({"ingest"}),
            attendance_weights=tenant.config.attendance_weights,
            late_arrival_after=tenant.config.late_arrival_after,
        )


def _matches(pattern: re.Pattern[str], value: Any, what: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise SeedError(f"Invalid {what}: {value!r}")
    return value


def _code(value: Any, what: str) -> str:
    return _matches(_CODE, value, what)


def _parse_role(name: str, raw: Mapping[str, Any]) -> RoleDefinition:
    if name == SYSTEM_ROLE:
        raise SeedError(f"'{SYSTEM_ROLE}' is reserved and cannot be defined in the seed.")
    try:
        scope = Scope(str(raw.get("scope")))
        clearance = clearance_rank(str(raw.get("clearance")))
    except ValueError as exc:
        raise SeedError(f"Role {name!r}: {exc}") from exc
    permissions = raw.get("permissions", [])
    if not isinstance(permissions, list) or not all(isinstance(p, str) for p in permissions):
        raise SeedError(f"Role {name!r}: permissions must be a list of strings.")
    return RoleDefinition(name=name, scope=scope, clearance=clearance, permissions=frozenset(permissions))


def _parse_config(raw: Mapping[str, Any], tenant_id: str) -> TenantConfig:
    formula = raw.get("attendance_formula", {})
    weights: list[tuple[str, float | None]] = []
    for status, weight in formula.get("weights", {}).items():
        if status not in AttendanceStatus.__members__:
            raise SeedError(f"{tenant_id}: unknown status {status!r} in attendance_formula.")
        if weight is not None and not 0 <= float(weight) <= 1:
            raise SeedError(f"{tenant_id}: weight for {status} must be between 0 and 1, or null.")
        weights.append((status, None if weight is None else float(weight)))
    status_codes = {str(raw_code): str(status) for raw_code, status in raw.get("status_codes", {}).items()}
    for status in status_codes.values():
        if status not in AttendanceStatus.__members__:
            raise SeedError(f"{tenant_id}: status code maps to unknown status {status!r}.")
    return TenantConfig(
        timezone=str(raw.get("timezone", "UTC")),
        date_format=str(raw.get("date_format", "YYYY-MM-DD")),
        week_off_days=tuple(str(day) for day in raw.get("week_off_days", [])),
        late_arrival_after=_matches(
            _CLOCK_TIME, raw.get("late_arrival_after", "09:30"), "late_arrival_after"
        ),
        ocr_review_threshold=float(raw.get("ocr_review_threshold", 0.8)),
        status_codes=MappingProxyType(status_codes),
        attendance_weights=tuple(sorted(weights)),
        rounding_decimals=int(formula.get("rounding", {}).get("decimals", 2)),
    )


def _parse_tenant(raw: Mapping[str, Any], roles: Mapping[str, RoleDefinition]) -> Tenant:
    tenant_id = _matches(_TENANT_ID, raw.get("tenant_id"), "tenant_id")
    departments: dict[str, Department] = {}
    for item in raw.get("departments", []):
        entity_id = _code(item.get("entity_id"), "entity_id")
        departments[entity_id] = Department(
            entity_id=entity_id,
            name=str(item.get("name", entity_id)),
            manager_employee_id=item.get("manager_employee_id"),
        )
    employees: dict[str, Employee] = {}
    for item in raw.get("employees", []):
        employee_id = _code(item.get("employee_id"), "employee_id")
        entity_id = item.get("entity_id")
        if entity_id not in departments:
            raise SeedError(f"{tenant_id}/{employee_id}: unknown department {entity_id!r}.")
        employees[employee_id] = Employee(
            employee_id=employee_id,
            name=str(item.get("name", "")),
            entity_id=entity_id,
            email=item.get("email"),
            is_manager=bool(item.get("is_manager", False)),
        )
    users: dict[str, User] = {}
    for item in raw.get("users", []):
        user = _parse_user(item, tenant_id, roles, departments, employees)
        if user.user_id in users:
            raise SeedError(f"{tenant_id}: duplicate user_id {user.user_id}.")
        users[user.user_id] = user
    try:
        holidays = tuple(date.fromisoformat(item["date"]) for item in raw.get("holidays", []))
    except (KeyError, TypeError, ValueError) as exc:
        raise SeedError(f"{tenant_id}: invalid holiday entry: {exc}") from exc
    return Tenant(
        tenant_id=tenant_id,
        name=str(raw.get("name", tenant_id)),
        config=_parse_config(raw.get("config", {}), tenant_id),
        departments=MappingProxyType(departments),
        employees=MappingProxyType(employees),
        users=MappingProxyType(users),
        holidays=holidays,
    )


def _parse_user(
    raw: Mapping[str, Any],
    tenant_id: str,
    roles: Mapping[str, RoleDefinition],
    departments: Mapping[str, Department],
    employees: Mapping[str, Employee],
) -> User:
    user_id = _matches(_USER_ID, raw.get("user_id"), "user_id")
    if raw.get("tenant_id", tenant_id) != tenant_id:
        raise SeedError(f"{user_id}: tenant_id does not match its tenant ({tenant_id}).")
    role = roles.get(str(raw.get("role")))
    if role is None:
        raise SeedError(f"{user_id}: unknown role {raw.get('role')!r}.")
    employee_id = raw.get("employee_id")
    if employee_id is not None and employee_id not in employees:
        raise SeedError(f"{user_id}: unknown employee_id {employee_id!r}.")
    entity_scope = tuple(raw.get("entity_scope") or ())
    unknown = [entity for entity in entity_scope if entity not in departments]
    if unknown:
        raise SeedError(f"{user_id}: unknown departments in entity_scope: {unknown}.")
    if role.scope is Scope.DEPARTMENT and not entity_scope:
        raise SeedError(f"{user_id}: a department-scoped role needs a non-empty entity_scope.")
    if role.scope is Scope.SELF and employee_id is None:
        raise SeedError(f"{user_id}: a self-scoped role needs an employee_id.")
    return User(
        user_id=user_id,
        tenant_id=tenant_id,
        role=role.name,
        display_name=str(raw.get("display_name", user_id)),
        employee_id=employee_id,
        entity_scope=entity_scope,
    )
