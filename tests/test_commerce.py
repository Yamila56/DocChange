import asyncio

import pytest
from pydantic import ValidationError
from sqlalchemy import event, func, select

from shopresolve.errors import DomainError
from shopresolve.models import Approval, Operation, Order, Policy, Proposal, Refund, ReturnRequest
from shopresolve.schemas import Decision, PrepareAction


def cancel(order="order-pending"):
    return PrepareAction(action="CANCEL_ORDER", order_id=order)


def returning(order="order-delivered", item="item-delivered", quantity=1):
    return PrepareAction(
        action="REQUEST_RETURN",
        order_id=order,
        item_id=item,
        quantity=quantity,
        reason="单侧无声音",
    )


async def approved(service, request, key="prepare"):
    p = await service.prepare("alice", request, key)
    await service.decide("alice", p["id"], Decision(decision="approve", expected_version=1), key)
    return p


async def test_cancel_is_atomic_and_receipt_replays_after_expiry(commerce):
    service, sessions, clock = commerce
    p = await approved(service, cancel())
    result = await service.execute("alice", p["id"])
    clock.value += 99999
    assert await service.execute("alice", p["id"]) == result
    order = await service.get_order("alice", "order-pending")
    assert order["status"] == "CANCELLED"
    assert order["refunded_cents"] == 19900
    async with sessions() as s:
        assert await s.scalar(select(func.count()).select_from(Refund)) == 1
        assert await s.scalar(select(func.count()).select_from(Operation)) == 1


async def test_return_does_not_issue_refund(commerce):
    service, sessions, _ = commerce
    p = await approved(service, returning())
    result = await service.execute("alice", p["id"])
    assert result["kind"] == "RETURN_REQUESTED"
    assert result["refund_status"] == "NOT_ISSUED"
    assert result["amount_cents"] == 19900
    order = await service.get_order("alice", "order-delivered")
    assert order["items"][0]["reserved_return_quantity"] == 1
    assert order["refunded_cents"] == 0
    async with sessions() as s:
        assert await s.scalar(select(func.count()).select_from(Refund)) == 0
        assert await s.scalar(select(func.count()).select_from(ReturnRequest)) == 1


@pytest.mark.parametrize(
    ("action_request", "code"),
    [
        (cancel("order-shipped"), "NOT_CANCELLABLE"),
        (returning("order-expired", "item-expired"), "RETURN_WINDOW_CLOSED"),
        (returning("order-excluded", "item-excluded"), "CATEGORY_EXCLUDED"),
        (returning(quantity=3), "QUANTITY_EXCEEDED"),
        (returning(item="item-pending"), "NOT_FOUND"),
    ],
)
async def test_business_rejections(commerce, action_request, code):
    service, _, _ = commerce
    with pytest.raises(DomainError) as error:
        await service.prepare("alice", action_request, "key")
    assert error.value.code == code


async def test_owner_isolation(commerce):
    service, _, _ = commerce
    with pytest.raises(DomainError) as error:
        await service.get_order("bob", "order-pending")
    assert error.value.status == 404
    p = await approved(service, cancel())
    with pytest.raises(DomainError):
        await service.execute("bob", p["id"])
    assert (await service.get_order("alice", "order-pending"))["status"] == "PENDING_SHIPMENT"


async def test_idempotency_same_body_and_conflicting_body(commerce):
    service, _, _ = commerce
    p = await service.prepare("alice", cancel(), "key")
    assert await service.prepare("alice", cancel(), "key") == p
    with pytest.raises(DomainError) as error:
        await service.prepare("alice", returning(), "key")
    assert error.value.code == "IDEMPOTENCY_CONFLICT"


async def test_idempotency_keys_are_case_sensitive_even_on_mysql(commerce):
    service, _, _ = commerce
    first = await service.prepare("alice", cancel(), "Request-A")
    second = await service.prepare("alice", cancel(), "request-a")
    assert first["id"] != second["id"]


async def test_approval_required_and_rejection_is_final(commerce):
    service, _, _ = commerce
    p = await service.prepare("alice", cancel(), "key")
    with pytest.raises(DomainError):
        await service.execute("alice", p["id"])
    decision = Decision(decision="reject", expected_version=1)
    rejected = await service.decide("alice", p["id"], decision, "reject")
    assert rejected["status"] == "REJECTED"
    assert await service.decide("alice", p["id"], decision, "reject") == rejected
    with pytest.raises(DomainError):
        await service.decide(
            "alice", p["id"], Decision(decision="approve", expected_version=2), "new-key"
        )
    with pytest.raises(DomainError):
        await service.execute("alice", p["id"])


async def test_expired_proposal_cannot_be_approved(commerce):
    service, _, clock = commerce
    p = await service.prepare("alice", cancel(), "key")
    clock.value += 901
    result = await service.decide(
        "alice", p["id"], Decision(decision="approve", expected_version=1), "approve"
    )
    assert result["status"] == "EXPIRED"
    with pytest.raises(DomainError):
        await service.execute("alice", p["id"])


async def test_expired_approval_cannot_execute(commerce):
    service, _, clock = commerce
    p = await approved(service, cancel())
    clock.value += 901
    assert (await service.execute("alice", p["id"]))["status"] == "STALE"


async def test_order_change_invalidates_approval(commerce):
    service, sessions, _ = commerce
    p = await approved(service, cancel())
    async with sessions.begin() as s:
        order = await s.get(Order, "order-pending")
        order.status = "SHIPPED"
        order.version += 1
    assert (await service.execute("alice", p["id"]))["status"] == "STALE"


async def test_policy_change_invalidates_approval(commerce):
    service, sessions, _ = commerce
    p = await approved(service, cancel())
    async with sessions.begin() as s:
        (await s.get(Policy, "demo-policy-v1")).active = False
    assert (await service.execute("alice", p["id"]))["status"] == "STALE"


async def test_second_partial_return_remains_legal(commerce):
    service, _, _ = commerce
    p = await approved(service, returning(), "first")
    await service.execute("alice", p["id"])
    p2 = await approved(service, returning(), "second")
    assert p2["operation_id"] != p["operation_id"]
    await service.execute("alice", p2["id"])
    with pytest.raises(DomainError):
        await service.prepare("alice", returning(), "third")


async def test_proposal_tampering_cannot_change_authorized_amount(commerce):
    service, sessions, _ = commerce
    p = await approved(service, cancel())
    async with sessions.begin() as s:
        (await s.get(Proposal, p["id"])).amount_cents = 1
    with pytest.raises(DomainError) as error:
        await service.execute("alice", p["id"])
    assert error.value.code == "INTEGRITY_ERROR"
    assert (await service.get_order("alice", "order-pending"))["refunded_cents"] == 0


async def test_approval_hash_tampering_is_rejected(commerce):
    service, sessions, _ = commerce
    p = await approved(service, cancel())
    async with sessions.begin() as s:
        (await s.get(Approval, p["id"])).args_hash = "0" * 64
    with pytest.raises(DomainError):
        await service.execute("alice", p["id"])


async def test_ledger_insert_error_rolls_back_every_business_change(commerce):
    service, sessions, _ = commerce
    p = await approved(service, cancel())
    async with sessions() as session:
        sync_engine = session.bind.sync_engine

    def fail_refund(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("INSERT INTO REFUNDS"):
            raise RuntimeError("controlled test failure before refund insert")

    event.listen(sync_engine, "before_cursor_execute", fail_refund)
    try:
        with pytest.raises(RuntimeError, match="controlled test failure"):
            await service.execute("alice", p["id"])
    finally:
        event.remove(sync_engine, "before_cursor_execute", fail_refund)
    order = await service.get_order("alice", "order-pending")
    assert order["status"] == "PENDING_SHIPMENT"
    assert order["refunded_cents"] == 0
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Operation)) == 0
    # The same approved proposal can be retried after rollback.
    assert (await service.execute("alice", p["id"]))["status"] == "COMPLETED"


async def test_policy_document_mismatch_stops_new_proposal(commerce):
    service, sessions, _ = commerce
    async with sessions.begin() as session:
        (await session.get(Policy, "demo-policy-v1")).return_days = 365
    with pytest.raises(DomainError) as error:
        await service.prepare("alice", returning(), "key")
    assert error.value.code == "POLICY_UNAVAILABLE"


async def test_stale_browser_cannot_override_decision(commerce):
    service, _, _ = commerce
    p = await approved(service, cancel())
    with pytest.raises(DomainError) as error:
        await service.decide(
            "alice", p["id"], Decision(decision="reject", expected_version=1), "other"
        )
    assert error.value.code == "PROPOSAL_CONFLICT"


@pytest.mark.parametrize("quantity", [0, -1, True, 1.2, "1"])
def test_strict_quantity(quantity):
    with pytest.raises(ValidationError):
        returning(quantity=quantity)


@pytest.mark.mysql
async def test_mysql_duplicate_prepares_are_serialized(commerce):
    service, sessions, _ = commerce
    async with sessions() as s:
        if s.bind.dialect.name != "mysql":
            pytest.skip("MySQL row locking is not tested on SQLite")
    results = await asyncio.gather(*[service.prepare("alice", cancel(), "same") for _ in range(8)])
    assert len({p["id"] for p in results}) == 1


@pytest.mark.mysql
async def test_mysql_duplicate_execution_has_one_ledger_effect(commerce):
    service, sessions, _ = commerce
    async with sessions() as s:
        if s.bind.dialect.name != "mysql":
            pytest.skip("MySQL row locking is not tested on SQLite")
    p = await approved(service, cancel())
    results = await asyncio.gather(*[service.execute("alice", p["id"]) for _ in range(8)])
    assert all(r == results[0] for r in results)
    async with sessions() as s:
        assert await s.scalar(select(func.count()).select_from(Refund)) == 1


@pytest.mark.mysql
async def test_mysql_independent_proposals_cannot_cancel_twice(commerce):
    service, sessions, _ = commerce
    async with sessions() as s:
        if s.bind.dialect.name != "mysql":
            pytest.skip("MySQL row locking is not tested on SQLite")
    first = await approved(service, cancel(), "one")
    second = await approved(service, cancel(), "two")
    results = await asyncio.gather(
        service.execute("alice", first["id"]), service.execute("alice", second["id"])
    )
    assert sorted(r["status"] for r in results) == ["COMPLETED", "STALE"]
    async with sessions() as s:
        assert await s.scalar(select(func.count()).select_from(Refund)) == 1
