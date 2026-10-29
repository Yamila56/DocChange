"""Objective state checks on a small regression set; never an LLM leaderboard score."""

import asyncio
import hashlib
import json
import statistics
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select

from shopresolve.agent.gateway import DemoGateway
from shopresolve.agent.runtime import AgentRuntime
from shopresolve.agent.tools import ToolRouter
from shopresolve.db import make_database
from shopresolve.models import (
    Approval,
    Base,
    Operation,
    Order,
    OrderItem,
    Proposal,
    Refund,
    ReturnRequest,
)
from shopresolve.retrieval.index import PolicyIndex
from shopresolve.runstore import RunStore
from shopresolve.schemas import Decision
from shopresolve.seed import seed
from shopresolve.service import CommerceService


async def evaluate(case, directory, retriever, *, gateway=None, model_config=None):
    engine, sessions = make_database(f"sqlite+aiosqlite:///{directory / 'case.sqlite'}")

    def clock():
        return 1800000000

    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await seed(sessions, now=int(clock()))
        async with sessions.begin() as session:
            for oid, fields in case.get("initial_orders", {}).items():
                order = await session.get(Order, oid)
                if order is None or not set(fields) <= {
                    "paid_cents",
                    "status",
                    "delivered_at",
                    "version",
                    "refunded_cents",
                }:
                    raise ValueError("Invalid isolated order fixture")
                for key, value in fields.items():
                    setattr(order, key, value)
            for iid, fields in case.get("initial_items", {}).items():
                item = await session.get(OrderItem, iid)
                if item is None or not set(fields) <= {
                    "quantity",
                    "unit_paid_cents",
                    "reserved_return_quantity",
                    "category",
                    "name",
                }:
                    raise ValueError("Invalid isolated item fixture")
                for key, value in fields.items():
                    setattr(item, key, value)
        store, commerce = RunStore(sessions, clock), CommerceService(sessions, clock)
        runtime = AgentRuntime(store, gateway or DemoGateway(), ToolRouter(commerce, retriever))
        conversation = await store.conversation("alice", "conversation")
        config = {
            "provider": "demo",
            "model": "demo-v1",
            "model_limit": 12,
            "active_budget_ms": 120000,
            "model_timeout_seconds": 5,
        }
        if model_config:
            config.update(model_config)
        created = await store.submit(
            "alice",
            conversation["id"],
            {"text": case["message"], "client_message_id": "input"},
            "input",
            config,
        )
        rid = created["run_id"]
        started = time.perf_counter()
        replies = iter(case.get("replies", []))
        responses_used = 0
        mutated = False
        for turn in range(case.get("max_turns", 8)):
            lease = await store.claim(rid, f"eval-worker-{turn}")
            if lease:
                await runtime.run(lease)
            state = await store.get("alice", rid)
            if case.get("cancel") and state["status"] == "WAITING_APPROVAL":
                state = await store.cancel("alice", rid, "cancel")
                break
            if state["status"] == "WAITING_USER":
                reply = next(replies, None)
                if reply is not None:
                    await store.resume(
                        "alice",
                        rid,
                        {
                            "text": reply,
                            "client_message_id": f"reply-{turn}",
                            "expected_version": state["version"],
                            "wait_generation": state["resume_generation"],
                        },
                        f"reply-{turn}",
                    )
                    responses_used += 1
                    continue
            if state["status"] != "WAITING_APPROVAL" or not (
                case.get("decision") or case.get("decisions")
            ):
                break
            if not mutated and case.get("pre_approval_order_updates"):
                async with sessions.begin() as session:
                    for oid, fields in case["pre_approval_order_updates"].items():
                        if not set(fields) <= {"status", "version"}:
                            raise ValueError("Invalid isolated transition fixture")
                        order = await session.get(Order, oid)
                        for key, value in fields.items():
                            setattr(order, key, value)
                mutated = True
            for goal in state["goals"]:
                if goal.get("proposal_id"):
                    p = await commerce.get_proposal("alice", goal["proposal_id"])
                    if p["status"] == "PENDING":
                        choice = case.get("decisions", {}).get(goal["intent"], case.get("decision"))
                        if choice is None:
                            continue
                        await commerce.decide(
                            "alice",
                            p["id"],
                            Decision(decision=choice, expected_version=p["version"]),
                            p["id"],
                        )
        latency = (time.perf_counter() - started) * 1000
        async with sessions() as session:
            refunds = await session.scalar(select(func.count()).select_from(Refund))
            returns = await session.scalar(select(func.count()).select_from(ReturnRequest))
        checks = {
            "status": state["status"] == case["status"],
            "refund_count": refunds == case.get("refunds", 0),
            "return_count": returns == case.get("returns", 0),
        }
        if "expected_intents" in case:
            checks["intent_coverage"] = sorted(g["intent"] for g in state["goals"]) == sorted(
                case["expected_intents"]
            )
        if "expected_goal_orders" in case:
            checks["resolved_targets"] = sorted(
                g["slots"]["order_id"]["value"]
                for g in state["goals"]
                if g.get("slots", {}).get("order_id")
            ) == sorted(case["expected_goal_orders"])
        if "replies" in case:
            checks["scripted_replies_used"] = responses_used == len(case["replies"])
        events = await store.events("alice", rid)
        tools_used = {e["payload"]["name"] for e in events if e["kind"] == "tool.completed"}
        for required in case.get("required_tools", []):
            checks[f"tool:{required}"] = required in tools_used
        if case.get("requires_policy"):
            checks["policy_citations"] = bool(state.get("evidence")) and all(
                e["version"] == "demo-policy-v1" for e in state["evidence"]
            )
        if "outcome" in case:
            checks["outcome"] = state["outcome"] == case["outcome"]
        if "goals" in case:
            checks["goal_count"] = len(state["goals"]) == case["goals"]
        if "business_state" in case:
            async with sessions() as session:
                for order_id, expected in case["business_state"].items():
                    order = await session.get(Order, order_id)
                    checks[f"order:{order_id}"] = order is not None and all(
                        getattr(order, key) == value for key, value in expected.items()
                    )
                actual_refunds = sorted(
                    (r.order_id, r.amount_cents, r.currency)
                    for r in (await session.scalars(select(Refund))).all()
                )
                checks["exact_refunds"] = actual_refunds == sorted(
                    tuple(row) for row in case.get("expected_refunds", [])
                )
                operations = (await session.scalars(select(Operation))).all()
                checks["operation_count"] = len(operations) == case.get("operations", 0)
                if "proposals" in case:
                    checks["proposal_count"] = (
                        await session.scalar(select(func.count()).select_from(Proposal))
                        == case["proposals"]
                    )
                if "expected_returns" in case:
                    actual_returns = sorted(
                        (r.item_id, r.quantity, r.status)
                        for r in (await session.scalars(select(ReturnRequest))).all()
                    )
                    checks["exact_returns"] = actual_returns == sorted(
                        tuple(row) for row in case["expected_returns"]
                    )
                for op in operations:
                    approval = await session.get(Approval, op.proposal_id)
                    checks[f"approval:{op.id}"] = (
                        approval is not None
                        and approval.decision == "approve"
                        and approval.decided_by == "alice"
                    )
        return {
            "case_id": case["id"],
            "checks": checks,
            "passed": all(checks.values()),
            "latency_ms": latency,
            "final_state": state,
            "events": events,
        }
    finally:
        await engine.dispose()


async def main():
    source = Path("evals/data/workflows-v1.json")
    dataset = json.loads(source.read_text(encoding="utf-8"))
    output = Path("docs/evaluation/workflow-regression-v1-results.json")
    if output.exists():
        raise RuntimeError("Existing evaluation evidence will not be overwritten")
    temp_root = Path(".cache/evals").resolve()
    temp_root.mkdir(parents=True, exist_ok=True)
    retriever = PolicyIndex("data/policies/demo.json")
    results = []
    for case in dataset["cases"]:
        with tempfile.TemporaryDirectory(prefix="case-", dir=temp_root) as directory:
            result = await evaluate(case, Path(directory), retriever)
        results.append(result)
        print(case["id"], result["passed"], flush=True)
    count = len(results)
    passed = sum(result["passed"] for result in results)
    metrics = {
        "case_count": count,
        "passed": passed,
        "scenario_pass_rate": passed / count,
        "median_latency_ms": statistics.median(r["latency_ms"] for r in results),
        "illegal_ledger_cases": sum(
            not r["checks"]["refund_count"] or not r["checks"]["return_count"] for r in results
        ),
    }
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": dataset["dataset_version"],
        "dataset_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "controller": "deterministic demo-v1, NOT an LLM",
        "retriever": retriever.manifest(),
        "clock": 1800000000,
        "metrics": metrics,
        "cases": results,
        "limitations": [
            "Regression set, not held-out real model quality",
            "Synthetic local SQLite business environment",
            "No paid model calls",
            "MySQL crash/locking tests reported separately",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
