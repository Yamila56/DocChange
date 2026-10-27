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
from shopresolve.models import Base, Refund, ReturnRequest
from shopresolve.retrieval.index import PolicyIndex
from shopresolve.runstore import RunStore
from shopresolve.schemas import Decision
from shopresolve.seed import seed
from shopresolve.service import CommerceService


async def evaluate(case, directory, retriever):
    engine, sessions = make_database(f"sqlite+aiosqlite:///{directory / 'case.sqlite'}")

    def clock():
        return 1800000000

    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await seed(sessions, now=int(clock()))
        store, commerce = RunStore(sessions, clock), CommerceService(sessions, clock)
        runtime = AgentRuntime(store, DemoGateway(), ToolRouter(commerce, retriever))
        conversation = await store.conversation("alice", "conversation")
        config = {
            "provider": "demo",
            "model": "demo-v1",
            "model_limit": 12,
            "active_budget_ms": 120000,
            "model_timeout_seconds": 5,
        }
        created = await store.submit(
            "alice",
            conversation["id"],
            {"text": case["message"], "client_message_id": "input"},
            "input",
            config,
        )
        rid = created["run_id"]
        started = time.perf_counter()
        for turn in range(4):
            lease = await store.claim(rid, f"eval-worker-{turn}")
            if lease:
                await runtime.run(lease)
            state = await store.get("alice", rid)
            if case.get("cancel") and state["status"] == "WAITING_APPROVAL":
                state = await store.cancel("alice", rid, "cancel")
                break
            if state["status"] != "WAITING_APPROVAL" or not case.get("decision"):
                break
            for goal in state["goals"]:
                if goal.get("proposal_id"):
                    p = await commerce.get_proposal("alice", goal["proposal_id"])
                    if p["status"] == "PENDING":
                        await commerce.decide(
                            "alice",
                            p["id"],
                            Decision(decision=case["decision"], expected_version=p["version"]),
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
        if "outcome" in case:
            checks["outcome"] = state["outcome"] == case["outcome"]
        if "goals" in case:
            checks["goal_count"] = len(state["goals"]) == case["goals"]
        return {
            "case_id": case["id"],
            "checks": checks,
            "passed": all(checks.values()),
            "latency_ms": latency,
            "final_state": state,
            "events": await store.events("alice", rid),
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
