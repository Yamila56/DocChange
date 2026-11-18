"""Generate an approval-only documentation patch from a local Git snapshot.

The caller selects the roots explicitly. No cloning, code execution, commit,
working-tree application or automatic publication is performed by the agent.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path
from uuid import uuid4

from scripts.evaluate_models import EvaluationBudget, RecordedGateway
from shopresolve.db import make_database
from shopresolve.documents.maintenance_batched_protocol import BATCHED_POLICY, TASKGRAPH_POLICIES
from shopresolve.documents.maintenance_belief_protocol import BELIEF_MODEL_LIMIT, BELIEF_POLICY
from shopresolve.documents.model_profile import pro_settings
from shopresolve.documents.repository import (
    RepositoryDecision,
    RepositoryEnvironment,
    RepositoryGateway,
    RepositoryRuntime,
)
from shopresolve.documents.repository_search import encoder_for
from shopresolve.models import Base, Run
from shopresolve.runstore import RunStore


async def main(args):
    if args.batched_thinking and args.policy != BATCHED_POLICY:
        raise ValueError("--batched-thinking requires --policy batched-react-v1")
    docs = args.docs_root.resolve()
    if not (docs / ".git").exists():
        raise ValueError("--docs-root must be an existing Git checkout")
    task = args.task_file.read_text(encoding="utf-8")
    if not task.strip() or len(task.encode()) > 24000:
        raise ValueError("Provide a nonempty task under 24KB")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    settings = pro_settings(**({"model": "deepseek-flash"} if args.policy == BELIEF_POLICY else {}))
    if not settings.model_api_key:
        raise RuntimeError("Configure the local provider key in .env")
    ledger = Path(".cache/docchange/repository-pro-budget.sqlite")
    ledger.parent.mkdir(parents=True, exist_ok=True)
    budget = EvaluationBudget(ledger, 100, 2, 0, cache_hit_rate=0.30)
    gateway = RepositoryGateway(settings)
    wrapped = RecordedGateway(gateway, budget, output / "model-calls.jsonl")
    engine, sessions = make_database(f"sqlite+aiosqlite:///{output / 'runs.sqlite'}")
    try:
        # Build reusable local indexes before accepting a run or paying for a model
        # decision. Cold repository indexing must not consume a worker lease.
        if args.cuda_dll_dir:
            import onnxruntime as ort

            ort.preload_dlls(directory=str(args.cuda_dll_dir))
        encoder = (
            encoder_for(settings.model_cache_dir, args.retrieval_provider)
            if args.retrieval_mode != "section_bm25"
            else None
        )
        env = RepositoryEnvironment(
            docs,
            args.code_root,
            output / "controller",
            retrieval_mode=args.retrieval_mode,
            encoder=encoder,
            model_cache_dir=settings.model_cache_dir,
        )
        started = time.perf_counter()
        print("Preparing authorized repository index before model execution...", flush=True)
        await asyncio.to_thread(
            env.execute,
            RepositoryDecision(
                kind="search", query="documentation", retrieval_mode=args.retrieval_mode
            ),
        )
        # Warmup observations were never delivered to the agent and cannot authorize edits.
        env.evidence.clear()
        env.read_paths.clear()
        env.observed_spans.clear()
        (output / "preparation.json").write_text(
            json.dumps(
                {
                    "ready": True,
                    "seconds": time.perf_counter() - started,
                    "retrieval_mode": args.retrieval_mode,
                    "generative_api_requests": 0,
                    "warmup_grants_edit_authority": False,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store = RunStore(sessions)
        conversation = await store.conversation("local-owner", str(uuid4()))
        cfg = {
            "provider": "deepseek",
            "model": settings.model,
            "domain": "repository",
            "repository_policy": args.policy,
            "model_limit": BELIEF_MODEL_LIMIT if args.policy == BELIEF_POLICY else 16,
            "model_timeout_seconds": settings.model_timeout_seconds,
            "active_budget_ms": 900000,
        }
        if args.policy == BATCHED_POLICY:
            cfg["batched_thinking"] = args.batched_thinking
        if args.policy == BELIEF_POLICY:
            cfg["belief_thinking"] = not args.no_belief_thinking
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
        if args.policy in TASKGRAPH_POLICIES:
            async with sessions() as session:
                saved = await session.get(Run, rid)
                maintenance = saved.state["maintenance"]
            (output / "maintenance-graph.json").write_text(
                json.dumps(maintenance["graph"], ensure_ascii=False, indent=2), encoding="utf-8"
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
    parser.add_argument(
        "--policy", choices=("coverage-v1", *TASKGRAPH_POLICIES), default=BELIEF_POLICY
    )
    parser.add_argument("--batched-thinking", action="store_true")
    parser.add_argument("--no-belief-thinking", action="store_true")
    parser.add_argument("--docs-root", type=Path, required=True)
    parser.add_argument("--code-root", type=Path)
    parser.add_argument("--task-file", type=Path, required=True)
    parser.add_argument(
        "--retrieval-mode", choices=["section_bm25", "dense", "hybrid"], default="hybrid"
    )
    parser.add_argument(
        "--retrieval-provider",
        default="CPUExecutionProvider",
        choices=["CPUExecutionProvider", "CUDAExecutionProvider"],
    )
    parser.add_argument("--cuda-dll-dir", type=Path)
    parser.add_argument(
        "--output", type=Path, default=Path(".cache/docchange/repository") / str(uuid4())
    )
    asyncio.run(main(parser.parse_args()))
