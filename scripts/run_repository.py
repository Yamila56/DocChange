"""Generate an approval-only documentation patch from a local Git snapshot.

The caller selects the roots explicitly. No cloning, code execution, commit,
working-tree application or automatic publication is performed by the agent.
"""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from scripts.evaluate_models import EvaluationBudget, RecordedGateway
from shopresolve.config import Settings
from shopresolve.db import make_database
from shopresolve.documents.repository import (
    RepositoryEnvironment,
    RepositoryGateway,
    RepositoryRuntime,
)
from shopresolve.models import Base
from shopresolve.runstore import RunStore


async def main(args):
    docs = args.docs_root.resolve()
    if not (docs / ".git").exists():
        raise ValueError("--docs-root must be an existing Git checkout")
    task = args.task_file.read_text(encoding="utf-8")
    if not task.strip() or len(task.encode()) > 24000:
        raise ValueError("Provide a nonempty task under 24KB")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    settings = Settings(
        provider="deepseek",
        model="deepseek-flash",
        model_base_url="https://api.deepseek.com",
        allow_paid_model=True,
        model_timeout_seconds=85,
    )
    if not settings.model_api_key:
        raise RuntimeError("Configure the local provider key in .env")
    ledger = Path(".cache/docchange/repository-budget.sqlite")
    ledger.parent.mkdir(parents=True, exist_ok=True)
    budget = EvaluationBudget(ledger, 100, 2, 0)
    gateway = RepositoryGateway(settings)
    wrapped = RecordedGateway(gateway, budget, output / "model-calls.jsonl")
    engine, sessions = make_database(f"sqlite+aiosqlite:///{output / 'runs.sqlite'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store = RunStore(sessions)
        conversation = await store.conversation("local-owner", str(uuid4()))
        cfg = {
            "provider": "deepseek",
            "model": settings.model,
            "domain": "repository",
            "model_limit": 16,
            "active_budget_ms": 900000,
        }
        rid = (
            await store.submit(
                "local-owner",
                conversation["id"],
                {"text": task, "client_message_id": str(uuid4())},
                str(uuid4()),
                cfg,
            )
        )["run_id"]
        lease = await store.claim(rid, "local-repository", lease_seconds=180)
        env = RepositoryEnvironment(docs, args.code_root, output / "controller")
        async with asyncio.timeout(900):
            result = await RepositoryRuntime(
                store, wrapped, env, output / "docchange.events.jsonl"
            ).run(lease, task)
        if result.get("patch"):
            (output / "candidate.patch").write_text(result["patch"], encoding="utf-8")
        (output / "result.json").write_text(
            json.dumps(
                {**result, "run_id": rid, "approval_required": result["decision"] == "patch"},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(
            json.dumps(
                {"run_id": rid, "decision": result["decision"], "output": str(output)},
                ensure_ascii=False,
            )
        )
    finally:
        await gateway.aclose()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs-root", type=Path, required=True)
    parser.add_argument("--code-root", type=Path)
    parser.add_argument("--task-file", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=Path(".cache/docchange/repository") / str(uuid4())
    )
    asyncio.run(main(parser.parse_args()))
