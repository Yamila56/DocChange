"""Budgeted distinct live document tasks; --execute required, no .env mutation."""

import argparse
import asyncio
import json
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from scripts.evaluate_models import EvaluationBudget, RecordedGateway
from shopresolve.config import Settings
from shopresolve.db import make_database
from shopresolve.documents.runtime import DocumentGateway, DocumentRuntime
from shopresolve.documents.service import DocumentService
from shopresolve.models import Base, ModelStep, ToolInvocation
from shopresolve.runstore import RunStore
from shopresolve.service import digest


async def main(execute, retest=False):
    data = json.loads(Path("evals/data/document-live-v1.json").read_text(encoding="utf-8"))
    folder = Path(".cache/evals/document-live-v1-20261029")
    carry, max_calls = 0, 20
    output = "docs/evaluation/document-live-v1-results.json"
    if retest:
        if Path(".cache/evals/document-live-v2-20261029/results.json").exists():
            folder = Path(".cache/evals/document-live-v2-20261029")
        parent = json.loads((folder / "results.json").read_text(encoding="utf-8"))
        failed = {r["id"] for r in parent["results"] if not r["passed"]}
        data["cases"] = [c for c in data["cases"] if c["id"] in failed]
        carry = parent["budget"]["estimated_total_with_carry_cny"]
        used = sum(v["calls"] for v in parent["budget"]["by_model"].values())
        if folder.name == "document-live-v2-20261029":
            original = json.loads(
                Path(".cache/evals/document-live-v1-20261029/results.json").read_text(
                    encoding="utf-8"
                )
            )
            used += sum(v["calls"] for v in original["budget"]["by_model"].values())
        max_calls = 20 - used
        version = "v3" if folder.name == "document-live-v2-20261029" else "v2"
        folder = Path(f".cache/evals/document-live-{version}-20261029")
        output = f"docs/evaluation/document-live-{version}-results.json"
    if not execute:
        print(
            json.dumps(
                {
                    "cases": [c["id"] for c in data["cases"]],
                    "max_calls": 20,
                    "cap_cny": 0.5,
                    "model": "deepseek-flash",
                    "execute": False,
                }
            )
        )
        return
    folder.mkdir(parents=True, exist_ok=True)
    plan = {
        "dataset": data,
        "source": {
            str(p): digest(p.read_text(encoding="utf-8"))
            for p in Path("src/shopresolve/documents").glob("*.py")
        },
        "max_calls": 20,
        "cap_cny": 0.5,
        "model": "deepseek-flash",
    }
    plan_path = folder / "plan.json"
    if plan_path.exists():
        raise RuntimeError("Batch exists; preserve it and explicitly design a new batch")
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    settings = Settings(
        provider="deepseek",
        model="deepseek-flash",
        model_base_url="https://api.deepseek.com",
        allow_paid_model=True,
        model_limit=8,
        model_timeout_seconds=30,
        model_max_output_tokens=2000,
    )
    budget = EvaluationBudget(folder / "budget.sqlite", max_calls, 0.5, carry)
    inner = DocumentGateway(settings)
    gateway = RecordedGateway(inner, budget, folder / "calls.jsonl")
    engine, sessions = make_database(f"sqlite+aiosqlite:///{folder / 'runs.sqlite'}")
    rows = []
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        service, store = DocumentService(sessions), RunStore(sessions)
        for case in data["cases"]:
            started = time.perf_counter()
            w = await service.create("alice", case["id"], case["documents"], str(uuid4()))
            conversation = await store.conversation("alice", str(uuid4()))
            cfg = {
                "provider": "deepseek",
                "model": "deepseek-flash",
                "domain": "document",
                "model_limit": 8,
                "active_budget_ms": 120000,
                "model_timeout_seconds": 30,
                "document": {
                    "workspace_id": w["id"],
                    "revision": 1,
                    "mode": case["mode"],
                    "text": case["text"],
                },
            }
            rid = (
                await store.submit(
                    "alice",
                    conversation["id"],
                    {"text": case["text"], "client_message_id": str(uuid4())},
                    str(uuid4()),
                    cfg,
                )
            )["run_id"]
            lease = await store.claim(rid, "document-live", lease_seconds=180)
            await DocumentRuntime(store, gateway).run(lease)
            run = await store.get("alice", rid)
            checks = {}
            if case["expect"] == "answer":
                checks["answer"] = run["status"] == "SUCCEEDED" and case["answer_contains"] in (
                    run["answer"] or ""
                )
                checks["citations"] = bool(run["evidence"])
                checks["no_proposal"] = run["changeset_id"] is None
            elif case["expect"] == "clarify":
                checks["clarified"] = run["status"] == "WAITING_USER" and bool(run["question"])
                checks["no_proposal"] = run["changeset_id"] is None
            else:
                checks["proposal"] = run["status"] == "WAITING_APPROVAL" and bool(
                    run["changeset_id"]
                )
                if checks["proposal"]:
                    change = await service.change("alice", run["changeset_id"])
                    result = await service.decide(
                        "alice", change["id"], True, change["payload_hash"], str(uuid4())
                    )
                    checks["published"] = result["status"] == "PUBLISHED"
            after = await service.get("alice", w["id"])
            for path, doc in case["documents"].items():
                if doc["role"] != "document" or path in case.get("unchanged", []):
                    checks["preserved:" + path] = after["documents"][path] == doc
            if case["expect"] != "publish":
                checks["no_write"] = after["documents"] == case["documents"]
            for path, words in case.get("required", {}).items():
                checks["required:" + path] = all(
                    word in after["documents"][path]["content"] for word in words
                )
            for path, words in case.get("forbidden", {}).items():
                checks["removed:" + path] = all(
                    word not in after["documents"][path]["content"] for word in words
                )
            async with sessions() as session:
                steps = (
                    await session.scalars(select(ModelStep).where(ModelStep.run_id == rid))
                ).all()
                tools = (
                    await session.scalars(
                        select(ToolInvocation).where(ToolInvocation.run_id == rid)
                    )
                ).all()
            rows.append(
                {
                    "id": case["id"],
                    "passed": all(checks.values()),
                    "checks": checks,
                    "run_id": rid,
                    "status": run["status"],
                    "stop_reason": run["stop_reason"],
                    "answer": run["answer"],
                    "question": run["question"],
                    "model_calls": len(steps),
                    "tools": len(tools),
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            )
            report = {
                "scope": "4 self-authored live acceptance cases; not a held-out benchmark",
                "source_plan_hash": digest(plan),
                "results": rows,
                "budget": budget.summary(),
            }
            (folder / "results.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
        public = {
            **report,
            "results": [{k: v for k, v in r.items() if k != "run_id"} for r in rows],
        }
        Path(output).write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(budget.summary()), flush=True)
    finally:
        await inner.aclose()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--retest-failures", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.execute, args.retest_failures))
