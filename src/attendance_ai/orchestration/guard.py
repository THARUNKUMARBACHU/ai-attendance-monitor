"""Checks a question before any retrieval or model call:

- instruction-like questions ("ignore your rules...") are refused;
- a question naming another tenant is answered as unavailable, exactly like a question with no data,
  so the reply neither confirms nor denies that the other organisation exists;
- a question naming a department or employee outside the caller's scope is denied, so nothing about
  them is searched. Names are matched against the caller's own tenant only; another tenant's people
  are never looked up, so such questions simply find no data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from attendance_ai.core.access import AccessContext, Scope
from attendance_ai.core.directory import Department, Directory, Employee, Tenant
from attendance_ai.governance.injection import detect_injection

OTHER_TENANT = "No attendance data is available for that. Answers cover only your organisation's records."


@dataclass(frozen=True, slots=True)
class GuardDecision:
    allowed: bool
    reason_code: str | None = None
    message: str | None = None
    outcome: Literal["denied", "unavailable"] = "denied"


ALLOW = GuardDecision(allowed=True)


class QuestionGuard:
    def __init__(self, directory: Directory) -> None:
        self._directory = directory

    def check(self, ctx: AccessContext, question: str) -> GuardDecision:
        if detect_injection(question):
            return GuardDecision(
                False,
                "policy_violation",
                "This request tries to change how the system works, which is not allowed.",
            )
        if names_other_tenant(self._directory, ctx.tenant_id, question):
            return GuardDecision(False, "other_tenant", OTHER_TENANT, outcome="unavailable")
        tenant = self._directory.get_tenant(ctx.tenant_id)
        if tenant is None or ctx.scope is Scope.TENANT:
            return ALLOW

        departments, employees = mentioned_entities(tenant, question)

        if ctx.scope is Scope.SELF:
            others = [employee for employee in employees if employee.employee_id != ctx.employee_id]
            if others or departments:
                return GuardDecision(
                    False, "out_of_scope_entity", "You can only see your own attendance records."
                )
            return ALLOW

        visible = set(ctx.entity_scope)
        outside = [d.name for d in departments if d.entity_id not in visible] + [
            e.name for e in employees if e.entity_id not in visible
        ]
        if outside:
            allowed = ", ".join(tenant.departments[entity].name for entity in ctx.entity_scope)
            return GuardDecision(
                False,
                "out_of_scope_entity",
                f"You don't have access to {', '.join(outside)}. You can see: {allowed}.",
            )
        return ALLOW


def mentioned_entities(tenant: Tenant, text: str) -> tuple[list[Department], list[Employee]]:
    """The tenant's departments (by name or code) and employees (by name or ID) named in ``text``."""
    departments = [
        department
        for department in tenant.departments.values()
        if _mentions_name(text, department.name) or _mentions_code(text, department.entity_id)
    ]
    employees = [
        employee
        for employee in tenant.employees.values()
        if _mentions_name(text, employee.name) or _mentions_id(text, employee.employee_id)
    ]
    return departments, employees


def names_other_tenant(directory: Directory, tenant_id: str, text: str) -> bool:
    """Whether ``text`` names any tenant other than ``tenant_id``, by ID or name."""
    for other_id in directory.tenant_ids:
        other = directory.get_tenant(other_id)
        if other_id == tenant_id or other is None:
            continue
        if _mentions_name(text, other_id) or _mentions_name(text, other.name):
            return True
    return False


def foreign_names(directory: Directory, tenant_id: str) -> list[str]:
    """Names that belong only to other tenants: their company names and their people (a person whose
    name also exists in this tenant is not foreign)."""
    own = directory.get_tenant(tenant_id)
    own_names = {employee.name for employee in own.employees.values()} if own else set()
    names: set[str] = set()
    for other_id in directory.tenant_ids:
        other = directory.get_tenant(other_id)
        if other is None or other_id == tenant_id:
            continue
        names.add(other.name)
        names.update(e.name for e in other.employees.values() if e.name not in own_names)
    return sorted(names)


def mentions_name(text: str, name: str) -> bool:
    return _mentions_name(text, name)


def _mentions_name(text: str, name: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE) is not None


def _mentions_code(text: str, code: str) -> bool:
    # Department codes are matched case-sensitively ("HR", "OPS") so ordinary words don't collide.
    return len(code) >= 2 and re.search(rf"(?<![\w]){re.escape(code)}(?![\w])", text) is not None


def _mentions_id(text: str, employee_id: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(employee_id)}(?![\w])", text, re.IGNORECASE) is not None
