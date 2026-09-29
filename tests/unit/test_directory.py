import copy
import json
from typing import Any

import pytest

from attendance_ai.core.access import Scope, clearance_rank
from attendance_ai.core.directory import Directory, SeedError
from attendance_ai.core.errors import AuthenticationError, NotFoundError, PermissionDeniedError

from ..support import SEED_FILE


def context(directory: Directory, user_id: str, product_id: str = "hrms", module: str = "attendance"):  # type: ignore[no-untyped-def]
    user = directory.find_user(user_id)
    assert user is not None
    return directory.build_context(
        tenant_id=user.tenant_id,
        user_id=user_id,
        product_id=product_id,
        module=module,
        request_id="req_test0001",
    )


def test_loads_sample_seed(directory: Directory) -> None:
    assert set(directory.tenant_ids) == {"acme", "globex"}
    acme, globex = directory.get_tenant("acme"), directory.get_tenant("globex")
    assert acme is not None and globex is not None
    # The sample data deliberately reuses employee IDs and names across tenants.
    assert acme.employees["E001"].name == globex.employees["E001"].name


def test_admin_sees_whole_tenant(directory: Directory) -> None:
    ctx = context(directory, "acme.admin")
    assert ctx.scope is Scope.TENANT
    assert ctx.entity_scope == ()
    assert ctx.clearance == clearance_rank("restricted")
    assert {"ingest", "feedback:submit", "audit:read"} <= ctx.permissions


def test_manager_is_limited_to_their_departments(directory: Directory) -> None:
    ctx = context(directory, "acme.eng.manager")
    assert ctx.scope is Scope.DEPARTMENT
    assert ctx.entity_scope == ("ENG",)
    assert ctx.clearance == clearance_rank("confidential")
    assert "ingest" not in ctx.permissions


def test_employee_scope_comes_from_role_not_seed_entity_scope(directory: Directory) -> None:
    # The seed lists a department for this user; a self-scoped role must ignore it.
    ctx = context(directory, "acme.employee")
    assert ctx.scope is Scope.SELF
    assert ctx.employee_id == "E006"
    assert ctx.entity_scope == ()


def test_unknown_user_is_not_authenticated(directory: Directory) -> None:
    with pytest.raises(AuthenticationError):
        directory.build_context(
            tenant_id="acme",
            user_id="globex.admin",
            product_id="hrms",
            module="attendance",
            request_id="r" * 8,
        )


def test_unknown_product_or_module_is_denied(directory: Directory) -> None:
    with pytest.raises(PermissionDeniedError):
        context(directory, "acme.admin", product_id="grc")
    with pytest.raises(PermissionDeniedError):
        context(directory, "acme.admin", module="payroll")


def test_system_context_for_background_jobs(directory: Directory) -> None:
    ctx = directory.system_context(
        tenant_id="globex", product_id="hrms", module="attendance", request_id="job_1234"
    )
    assert ctx.scope is Scope.TENANT
    assert ctx.permissions == frozenset({"ingest"})
    with pytest.raises(NotFoundError):
        directory.system_context(
            tenant_id="nope", product_id="hrms", module="attendance", request_id="job_1234"
        )


def test_db_settings_carry_the_full_isolation_context(directory: Directory) -> None:
    settings = context(directory, "acme.eng.manager").db_settings()
    assert settings["app.tenant_id"] == "acme"
    assert settings["app.product_id"] == "hrms"
    assert settings["app.module"] == "attendance"
    assert settings["app.scope"] == "department"
    assert settings["app.entity_scope"] == "ENG"
    assert settings["app.clearance"] == str(clearance_rank("confidential"))
    weights = json.loads(settings["app.weights"])
    assert weights["PRESENT"] == 1.0 and weights["HALF_DAY"] == 0.5 and weights["HOLIDAY"] is None


def test_describe_exposes_no_internals(directory: Directory) -> None:
    described = context(directory, "acme.admin").describe()
    assert described["clearance"] == "restricted"
    assert "request_id" not in described
    assert "attendance_weights" not in described


def _seed() -> dict[str, Any]:
    return copy.deepcopy(json.loads(SEED_FILE.read_text(encoding="utf-8")))


def _break_comma_entity(seed: dict[str, Any]) -> None:
    seed["tenants"][0]["departments"][0]["entity_id"] = "ENG,SAL"


def _break_manager_scope(seed: dict[str, Any]) -> None:
    seed["tenants"][0]["users"][1]["entity_scope"] = []


def _break_duplicate_user(seed: dict[str, Any]) -> None:
    seed["tenants"][1]["users"][0]["user_id"] = "acme.admin"


def _break_unknown_role(seed: dict[str, Any]) -> None:
    seed["tenants"][0]["users"][0]["role"] = "superuser"


def _break_reserved_role(seed: dict[str, Any]) -> None:
    seed["roles"]["system"] = {"scope": "tenant", "clearance": "restricted", "permissions": []}


def _break_weight(seed: dict[str, Any]) -> None:
    seed["tenants"][0]["config"]["attendance_formula"]["weights"]["PRESENT"] = 2


@pytest.mark.parametrize(
    "breaker",
    [
        _break_comma_entity,
        _break_manager_scope,
        _break_duplicate_user,
        _break_unknown_role,
        _break_reserved_role,
        _break_weight,
    ],
)
def test_inconsistent_seed_is_rejected(breaker) -> None:  # type: ignore[no-untyped-def]
    seed = _seed()
    breaker(seed)
    with pytest.raises(SeedError):
        Directory.from_dict(seed)
