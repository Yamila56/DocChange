"""Small free engineering task suite. Never substitutes demo results for LLM accuracy."""

import argparse
import asyncio
import copy
import json
import statistics
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from sqlalchemy import func, select

from shopresolve.db import make_database
from shopresolve.documents.models import Revision
from shopresolve.documents.runtime import DocumentDemoGateway, DocumentRuntime
from shopresolve.documents.service import DocumentService
from shopresolve.models import Base, ModelStep, ToolInvocation
from shopresolve.runstore import RunStore
from shopresolve.service import digest

DOCUMENTS = {
    "guide.md": {
        "role": "document",
        "content": "# 指南\n\n使用 user_id。user_id 为必填参数。\n[API](api.md)\n",
    },
    "api.md": {
        "role": "document",
        "content": "# API\n\nuser_id 为必填字符串。\nTimeout 30 seconds.\n",
    },
    "release.md": {
        "role": "source",
        "content": "# 发布说明\n任务指定修改仅适用于当前文档。保留历史内容。\n",
    },
    "history.md": {"role": "archive", "content": "# 历史\nuser_id 必填。Timeout 30 seconds.\n"},
}


async def execute(sessions, service, wid, case):
    store = RunStore(sessions)
    cid = (await store.conversation("alice", str(uuid4())))["id"]
    snap = await service.get("alice", wid)
    task = {
        "workspace_id": wid,
        "revision": snap["revision"],
        "mode": case["mode"],
        "text": case.get("text", "根据指定目标更新当前文档，保留其他内容。"),
        "old": case.get("old", ""),
        "new": case.get("new", ""),
        **{
            k: case[k]
            for k in ("editable_paths", "required_absent", "required_present")
            if k in case
        },
    }
    cfg = {
        "domain": "document",
        "document": task,
        "provider": "demo",
        "model": "demo-v1",
        "model_limit": 12,
        "model_timeout_seconds": 30,
        "active_budget_ms": 120000,
    }
    rid = (
        await store.submit(
            "alice",
            cid,
            {"text": task["text"], "client_message_id": str(uuid4())},
            str(uuid4()),
            cfg,
        )
    )["run_id"]
    lease = await store.claim(rid, "document-evaluation", lease_seconds=180)
    await DocumentRuntime(store, DocumentDemoGateway()).run(lease)
    return await store.get("alice", rid)


async def evaluate(output):
    dataset_path = Path("evals/data/document-tasks-v1.json")
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    rows = []
    with TemporaryDirectory(prefix="docchange-") as folder:
        engine, sessions = make_database(
            f"sqlite+aiosqlite:///{Path(folder) / 'evaluation.sqlite'}"
        )
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            service = DocumentService(sessions)
            for case in dataset["cases"]:
                started = time.perf_counter()
                w = await service.create("alice", case["id"], DOCUMENTS, str(uuid4()))
                run = await execute(sessions, service, w["id"], case)
                observed = run["status"]
                if run["changeset_id"]:
                    change = await service.change("alice", run["changeset_id"])
                    if case.get("stale"):
                        other = await execute(sessions, service, w["id"], case)
                        oc = await service.change("alice", other["changeset_id"])
                        await service.decide(
                            "alice", oc["id"], True, oc["payload_hash"], str(uuid4())
                        )
                    result = await service.decide(
                        "alice", change["id"], case["approve"], change["payload_hash"], str(uuid4())
                    )
                    observed = result["status"]
                after = await service.get("alice", w["id"])
                expected = copy.deepcopy(DOCUMENTS)
                if observed == "PUBLISHED" or case.get("stale"):
                    for doc in expected.values():
                        if doc["role"] == "document":
                            doc["content"] = doc["content"].replace(case["old"], case["new"])
                checks = {
                    "disposition": observed == case["expected"],
                    "exact_documents": after["documents"] == expected,
                    "protected_unchanged": all(
                        after["documents"][p] == d
                        for p, d in DOCUMENTS.items()
                        if d["role"] != "document"
                    ),
                    "snapshot_index": after["index"]["source_hash"] == digest(after["documents"]),
                }
                async with sessions() as session:
                    revisions = await session.scalar(
                        select(func.count())
                        .select_from(Revision)
                        .where(Revision.workspace_id == w["id"])
                    )
                    steps = (
                        await session.scalars(
                            select(ModelStep).where(ModelStep.run_id == run["id"])
                        )
                    ).all()
                    tools = await session.scalar(
                        select(func.count())
                        .select_from(ToolInvocation)
                        .where(ToolInvocation.run_id == run["id"])
                    )
                checks["revision_count"] = revisions == (
                    2 if observed == "PUBLISHED" or case.get("stale") else 1
                )
                rows.append(
                    {
                        "id": case["id"],
                        "observed": observed,
                        "checks": checks,
                        "passed": all(checks.values()),
                        "calls": len(steps),
                        "tools": tools,
                        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    }
                )
        finally:
            await engine.dispose()
    result = {
        "dataset": dataset["version"],
        "dataset_sha256": digest(dataset),
        "controller": "deterministic-demo-NOT-LLM",
        "scope": "Development engineering tasks; overlapping mechanics; not a model benchmark",
        "paid_calls": 0,
        "total": len(rows),
        "passed": sum(r["passed"] for r in rows),
        "median_ms": statistics.median(r["latency_ms"] for r in rows),
        "cases": rows,
    }
    Path(output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "cases"}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="docs/evaluation/document-engineering-v1-results.json")
    asyncio.run(evaluate(parser.parse_args().output))
