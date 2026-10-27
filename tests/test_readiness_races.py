import asyncio

import pytest
from sqlalchemy import func, select

from shopresolve.errors import DomainError
from shopresolve.models import Approval, Operation, Proposal, Refund
from shopresolve.schemas import Decision
from tests.test_runs import drive, new_run


async def prepared(commerce):
    store, rid, runtime = await new_run(commerce, "帮我取消 order-pending")
    waiting = await drive(store, rid, runtime)
    pid = waiting["goals"][0]["proposal_id"]
    return store, rid, runtime, waiting, pid


async def test_cancel_after_commit_before_loop_checkpoint_keeps_receipt_visible(commerce):
    service, sessions, _ = commerce
    store, rid, _, _, pid = await prepared(commerce)
    await service.decide("alice", pid, Decision(decision="approve", expected_version=1), "approve")
    lease = await store.claim(rid, "worker")
    receipt = await service.execute("alice", pid, lease=lease)
    cancelled = await store.cancel("alice", rid, "cancel")
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["outcome"] == "PARTIAL"
    assert [r["operation_id"] for r in cancelled["results"]] == [receipt["operation_id"]]
    assert cancelled["goals"][0]["status"] == "COMPLETED"
    assert "不会回滚" in cancelled["answer"]
    assert (await store.cancel("alice", rid, "cancel")) == cancelled
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Refund)) == 1


async def test_revision_retires_old_approval_and_preserves_committed_goal(commerce):
    service, sessions, _ = commerce
    store, rid, runtime = await new_run(
        commerce, "帮我取消 order-pending；申请退货 order-delivered item-delivered 数量1 原因：故障"
    )
    waiting = await drive(store, rid, runtime)
    old_pids = [g["proposal_id"] for g in waiting["goals"]]
    await store.resume(
        "alice",
        rid,
        {
            "text": "先别操作，我想修改需求",
            "client_message_id": "revision",
            "expected_version": waiting["version"],
            "wait_generation": waiting["resume_generation"],
        },
        "revision",
    )
    for pid in old_pids:
        assert (await service.get_proposal("alice", pid))["status"] == "WITHDRAWN"
        with pytest.raises(DomainError):
            await service.decide(
                "alice", pid, Decision(decision="approve", expected_version=1), pid
            )
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Operation)) == 0


@pytest.mark.mysql
async def test_mysql_revision_and_approval_have_one_legal_winner(commerce):
    service, sessions, _ = commerce
    async with sessions() as session:
        if session.bind.dialect.name != "mysql":
            pytest.skip("Requires MySQL row locks")
    store, rid, _, waiting, pid = await prepared(commerce)
    results = await asyncio.gather(
        service.decide("alice", pid, Decision(decision="approve", expected_version=1), "approve"),
        store.resume(
            "alice",
            rid,
            {
                "text": "先别取消",
                "client_message_id": "revision",
                "expected_version": waiting["version"],
                "wait_generation": waiting["resume_generation"],
            },
            "revision",
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(result, Exception) for result in results) == 1, results
    loser = next(result for result in results if isinstance(result, Exception))
    assert isinstance(loser, DomainError)
    async with sessions() as session:
        approval = await session.get(Approval, pid)
        proposal = await session.get(Proposal, pid)
        assert (approval is not None) == (proposal.status == "APPROVED")
        assert await session.scalar(select(func.count()).select_from(Refund)) == 0


@pytest.mark.mysql
async def test_mysql_cancel_and_execution_preserve_linearized_result(commerce):
    service, sessions, _ = commerce
    async with sessions() as session:
        if session.bind.dialect.name != "mysql":
            pytest.skip("Requires MySQL row locks")
    store, rid, _, _, pid = await prepared(commerce)
    await service.decide("alice", pid, Decision(decision="approve", expected_version=1), "approve")
    lease = await store.claim(rid, "worker")
    results = await asyncio.gather(
        service.execute("alice", pid, lease=lease),
        store.cancel("alice", rid, "cancel"),
        return_exceptions=True,
    )
    assert all(not isinstance(r, Exception) or isinstance(r, DomainError) for r in results), results
    final = await store.get("alice", rid)
    assert final["status"] == "CANCELLED"
    async with sessions() as session:
        count = await session.scalar(select(func.count()).select_from(Refund))
    assert count in {0, 1}
    assert len(final["results"]) == count
    assert final["outcome"] == ("PARTIAL" if count else "UNRESOLVED")
    assert (await service.get_order("alice", "order-pending"))["refunded_cents"] == count * 19900
