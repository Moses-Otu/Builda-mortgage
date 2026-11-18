"""Non-HTTP callers (future workers and integrations) use domain services through
``open_tenant_scope`` and a non-user ``Actor``, without impersonating anyone."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI

from meip.cases import service as cases
from meip.cases.schemas import CaseCreate, CaseTransition
from meip.core.errors import ForbiddenError
from meip.tenancy.context import Actor, ActorKind, OperationContext, open_tenant_scope
from meip.tenancy.models import Role
from meip.tenancy.permissions import Permission
from tests.conftest import Firm


async def test_system_actor_is_attributed_with_on_behalf_of(app: FastAPI, firm: Firm) -> None:
    me = (await firm.client.get("/api/v1/me", headers=firm.bearer(firm.admin_subject))).json()
    uploader = uuid.UUID(me["user_id"])
    actor = Actor.system("worker:test", frozenset({Permission.CASE_CREATE}), uploader)

    async with open_tenant_scope(app.state.sessionmaker, firm.tenant_id) as scope:
        ctx = OperationContext(scope=scope, actor=actor, correlation_id="job-123")
        created = await cases.create_case(ctx, CaseCreate(purpose="purchase"))
        await ctx.commit()

    assert created.created_by_user_id is None  # a system actor is not a user
    events = (await firm.request("GET", f"/api/v1/cases/{created.id}/events")).json()["items"]
    assert len(events) == 1
    event = events[0]
    assert event["actor_type"] == "system"
    assert event["actor_ref"] == "worker:test"
    assert event["actor_user_id"] is None
    assert event["on_behalf_of_user_id"] == str(uploader)


async def test_system_actor_conditional_checks_use_its_capabilities(
    app: FastAPI, firm: Firm
) -> None:
    case = await firm.create_case()
    actor = Actor.system("worker:test", frozenset({Permission.CASE_TRANSITION}))
    async with open_tenant_scope(app.state.sessionmaker, firm.tenant_id) as scope:
        ctx = OperationContext(scope=scope, actor=actor)
        with pytest.raises(ForbiddenError):  # closing needs CASE_CLOSE, which it lacks
            await cases.transition_case(
                ctx,
                uuid.UUID(case["id"]),
                CaseTransition(to_status="closed", version=1, reason="x"),
            )


async def test_scope_is_tenant_bound_and_uncommitted_work_is_discarded(
    app: FastAPI, firm: Firm, make_firm: object
) -> None:
    actor = Actor.system("worker:test", frozenset({Permission.CASE_CREATE}))
    async with open_tenant_scope(app.state.sessionmaker, firm.tenant_id) as scope:
        ctx = OperationContext(scope=scope, actor=actor)
        await cases.create_case(ctx, CaseCreate(purpose="purchase"))
        # no commit
    listing = (await firm.request("GET", "/api/v1/cases")).json()
    assert listing["total"] == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": ActorKind.USER},  # no user id or role
        {"kind": ActorKind.USER, "user_id": uuid.uuid4(), "role": Role.ADVISER, "ref": "x"},
        {"kind": ActorKind.SYSTEM},  # no ref
        {"kind": ActorKind.SYSTEM, "ref": "worker:x", "user_id": uuid.uuid4()},
        {"kind": ActorKind.OPERATOR, "ref": "operator:x", "on_behalf_of_user_id": uuid.uuid4()},
    ],
)
def test_inconsistent_actors_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="actor"):
        Actor(**kwargs)  # type: ignore[arg-type]


def test_user_actor_permissions_come_from_role() -> None:
    adviser = Actor.user(uuid.uuid4(), uuid.uuid4(), Role.ADVISER)
    assert adviser.can(Permission.CASE_CLOSE)
    assert not adviser.can(Permission.MEMBER_MANAGE)
    worker = Actor.system("worker:x")
    assert not worker.can(Permission.CASE_READ)
