import asyncio

import pytest
from sqlalchemy import func, select

from shopresolve.agent.gateway import DemoGateway, ScriptedGateway
from shopresolve.agent.runtime import AgentRuntime
from shopresolve.agent.tools import ToolRouter
from shopresolve.errors import DomainError
from shopresolve.models import Checkpoint, Operation, Outbox, Refund, Run
from shopresolve.runstore import RunStore
from shopresolve.schemas import Decision

CONFIG = {
    "provider": "demo",
    "model": "demo-v1",
    "model_limit": 12,
    "active_budget_ms": 120000,
    "model_timeout_seconds": 5,
}


async def new_run(commerce, message="查询 order-pending", key="one"):
    service, sessions, clock = commerce
    store = RunStore(sessions, clock)
    conversation = await store.conversation("alice", f"conversation-{key}")
    response = await store.submit(
        "alice",
        conversation["id"],
        {
            "text": message,
            "client_message_id": key,
        },
        key,
        CONFIG,
    )
    return store, response["run_id"], AgentRuntime(store, DemoGateway(), ToolRouter(service))


async def drive(store, run_id, runtime, worker="test-worker"):
    lease = await store.claim(run_id, worker)
    assert lease is not None
    await runtime.run(lease)
    return await store.get("alice", run_id)


async def test_read_only_agent_completes_from_real_order(commerce):
    store, rid, runtime = await new_run(commerce)
    result = await drive(store, rid, runtime)
    assert result["status"] == "SUCCEEDED", result
    assert result["outcome"] == "FULFILLED"
    assert "19900" in result["answer"]
    events = await store.events("alice", rid)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert any(e["kind"] == "tool.completed" for e in events)


async def test_parallel_reads_keep_durable_event_sequence_unique(commerce):
    store, rid, runtime = await new_run(commerce)
    runtime.gateway = ScriptedGateway(
        [
            DemoGateway()._intents,
            {
                "kind": "tool_calls",
                "calls": [
                    {
                        "call_id": name,
                        "name": name,
                        "arguments": {"goal_id": "g1", "order_id": "order-pending"},
                    }
                    for name in ("get_order", "get_shipment", "calculate_eligibility")
                ],
            },
        ]
    )
    original_execute = runtime.tools.execute
    all_started = asyncio.Event()
    started = []

    async def synchronized_reads(owner, name, arguments):
        started.append(name)
        if len(started) == 3:
            all_started.set()
        await asyncio.wait_for(all_started.wait(), timeout=5)
        return await original_execute(owner, name, arguments)

    runtime.tools.execute = synchronized_reads
    result = await drive(store, rid, runtime)
    assert result["status"] == "SUCCEEDED", result
    events = await store.events("alice", rid)
    assert sum(e["kind"] == "tool.completed" for e in events) == 3
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))


async def test_action_waits_for_approval_then_executes_once(commerce):
    service, sessions, _ = commerce
    store, rid, runtime = await new_run(commerce, "帮我取消 order-pending")
    waiting = await drive(store, rid, runtime)
    assert waiting["status"] == "WAITING_APPROVAL", waiting
    assert (await service.get_order("alice", "order-pending"))["status"] == "PENDING_SHIPMENT"
    proposal_id = waiting["goals"][0]["proposal_id"]
    await service.decide(
        "alice", proposal_id, Decision(decision="approve", expected_version=1), "approval-key"
    )
    final = await drive(store, rid, runtime, "new-worker")
    assert final["status"] == "SUCCEEDED", final
    assert final["outcome"] == "FULFILLED"
    assert final["results"][0]["kind"] == "SIMULATED_CANCELLATION"
    assert await store.claim(rid, "duplicate-worker") is None
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Refund)) == 1
        assert await session.scalar(select(func.count()).select_from(Operation)) == 1


async def test_inquiry_does_not_create_proposal(commerce):
    store, rid, runtime = await new_run(commerce, "取消订单的条件是什么？")
    result = await drive(store, rid, runtime)
    assert result["status"] == "SUCCEEDED", result
    assert not result["results"]
    assert "proposal_id" not in result["goals"][0]


async def test_two_goals_create_separate_proposals(commerce):
    store, rid, runtime = await new_run(
        commerce,
        "帮我取消 order-pending；申请退货 order-delivered item-delivered 数量1 原因：单侧无声音",
    )
    result = await drive(store, rid, runtime)
    assert result["status"] == "WAITING_APPROVAL", result
    assert len(result["goals"]) == 2
    assert len({g["proposal_id"] for g in result["goals"]}) == 2


async def test_missing_quantity_asks_and_preserves_memory(commerce):
    store, rid, runtime = await new_run(
        commerce, "申请退货 order-delivered item-delivered 原因：单侧无声音"
    )
    waiting = await drive(store, rid, runtime)
    assert waiting["status"] == "WAITING_USER", waiting
    await store.resume(
        "alice",
        rid,
        {
            "text": "数量1",
            "client_message_id": "answer1",
            "expected_version": waiting["version"],
            "wait_generation": waiting["resume_generation"],
        },
        "answer1",
    )
    result = await drive(store, rid, runtime)
    assert result["status"] == "WAITING_APPROVAL", result
    assert len(result["goals"]) == 1
    assert result["goals"][0]["slots"]["quantity"]["value"] == 1


async def test_rejected_action_has_no_side_effect(commerce):
    service, sessions, _ = commerce
    store, rid, runtime = await new_run(commerce, "帮我取消 order-pending")
    waiting = await drive(store, rid, runtime)
    await service.decide(
        "alice",
        waiting["goals"][0]["proposal_id"],
        Decision(decision="reject", expected_version=1),
        "reject",
    )
    result = await drive(store, rid, runtime)
    assert result["outcome"] == "DECLINED", result
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Refund)) == 0


async def test_cancelled_run_cannot_be_claimed_or_executed(commerce):
    service, _, _ = commerce
    store, rid, runtime = await new_run(commerce, "帮我取消 order-pending")
    waiting = await drive(store, rid, runtime)
    await store.cancel("alice", rid, "cancel")
    assert await store.claim(rid, "late-worker") is None
    with pytest.raises(DomainError):
        await service.decide(
            "alice",
            waiting["goals"][0]["proposal_id"],
            Decision(decision="approve", expected_version=2),
            "late",
        )


async def test_cross_owner_run_is_not_visible(commerce):
    store, rid, _ = await new_run(commerce)
    for operation in (store.get("bob", rid), store.events("bob", rid)):
        with pytest.raises(DomainError) as error:
            await operation
        assert error.value.status == 404


async def test_resume_requires_current_generation_and_version(commerce):
    store, rid, runtime = await new_run(commerce, "取消订单")
    waiting = await drive(store, rid, runtime)
    with pytest.raises(DomainError) as error:
        await store.resume(
            "alice",
            rid,
            {
                "text": "order-pending",
                "client_message_id": "a",
                "expected_version": waiting["version"] - 1,
                "wait_generation": 0,
            },
            "a",
        )
    assert error.value.code == "RUN_CONFLICT"


async def test_corrupt_checkpoint_fails_without_execution(commerce):
    _, sessions, _ = commerce
    store, rid, _ = await new_run(commerce)
    async with sessions.begin() as session:
        run = await session.get(Run, rid)
        run.state_hash = "0" * 64
    assert await store.claim(rid, "worker") is None
    assert (await store.get("alice", rid))["stop_reason"] == "CHECKPOINT_INVALID"


async def test_duplicate_creation_has_one_outbox(commerce):
    _, sessions, clock = commerce
    store = RunStore(sessions, clock)
    conversation = await store.conversation("alice", "conversation")
    body = {"text": "查询 order-pending", "client_message_id": "message"}
    first = await store.submit("alice", conversation["id"], body, "key", CONFIG)
    assert await store.submit("alice", conversation["id"], body, "key", CONFIG) == first
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Outbox)) == 1
        assert await session.scalar(select(func.count()).select_from(Checkpoint)) == 1


async def test_fenced_worker_cannot_commit_after_takeover(commerce):
    _, sessions, _ = commerce
    store, rid, _ = await new_run(commerce)
    first = await store.claim(rid, "old")
    async with sessions.begin() as session:
        (await session.get(Run, rid)).lease_until = 0
    second = await store.claim(rid, "new")
    assert second.token > first.token
    with pytest.raises(DomainError) as error:
        await store.save(first, {})
    assert error.value.code == "LEASE_LOST"


class SimulatedProcessExit(BaseException):
    pass


async def test_recover_after_business_commit_before_checkpoint(commerce):
    service, sessions, _ = commerce
    store, rid, runtime = await new_run(commerce, "帮我取消 order-pending")
    waiting = await drive(store, rid, runtime)
    await service.decide(
        "alice",
        waiting["goals"][0]["proposal_id"],
        Decision(decision="approve", expected_version=1),
        "approval",
    )
    lease = await store.claim(rid, "crashing")

    async def crash(point, _):
        if point == "after_tool_commit":
            raise SimulatedProcessExit()

    runtime.fault_hook = crash
    with pytest.raises(SimulatedProcessExit):
        await runtime.run(lease)
    async with sessions.begin() as session:
        (await session.get(Run, rid)).lease_until = 0
    runtime.fault_hook = None
    result = await drive(store, rid, runtime, "recovered")
    assert result["outcome"] == "FULFILLED", result
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Refund)) == 1


async def test_bad_model_output_is_bounded(commerce):
    store, rid, runtime = await new_run(commerce)
    runtime.gateway = ScriptedGateway([{"invalid": "frame"}] * 3)
    result = await drive(store, rid, runtime)
    assert result["status"] == "FAILED", result
    assert result["model_calls"] == 2


async def test_repair_context_explains_premature_final_and_bad_citation(commerce):
    store, rid, runtime = await new_run(commerce)

    def repair(context):
        assert context["last_error"] == "PREMATURE_FINAL"
        assert "INVALID_EVIDENCE" in context["last_error_detail"]
        assert "GOAL_NOT_FINISHED:g1" in context["last_error_detail"]
        return {
            "kind": "tool_calls",
            "calls": [
                {
                    "call_id": "read",
                    "name": "get_order",
                    "arguments": {"goal_id": "g1", "order_id": "order-pending"},
                }
            ],
        }

    runtime.gateway = ScriptedGateway(
        [
            DemoGateway()._intents,
            {
                "kind": "final_answer",
                "text": "premature",
                "goal_ids": ["g1"],
                "evidence_ids": ["call-id-is-not-evidence"],
            },
            repair,
        ]
    )
    result = await drive(store, rid, runtime)
    assert result["status"] == "SUCCEEDED", result


async def test_sse_cursor_replays_only_later_events(commerce):
    store, rid, runtime = await new_run(commerce)
    await drive(store, rid, runtime)
    all_events = await store.events("alice", rid)
    last = all_events[len(all_events) // 2]["seq"]
    later = await store.events("alice", rid, after=last)
    assert later and all(e["seq"] > last for e in later)


@pytest.mark.mysql
async def test_mysql_two_workers_cannot_claim_same_run(commerce):
    _, sessions, _ = commerce
    async with sessions() as session:
        if session.bind.dialect.name != "mysql":
            pytest.skip("Requires MySQL locks")
    store, rid, _ = await new_run(commerce)
    leases = await asyncio.gather(store.claim(rid, "one"), store.claim(rid, "two"))
    assert sum(lease is not None for lease in leases) == 1
