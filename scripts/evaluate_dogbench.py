"""Full released DoGBench candidate generation with the DocChange tool adapter.

Run on Linux after prepare_dogbench.py. Official labels/rubrics are deliberately
not loaded by candidate generation. The controller alone sees preparation IDs.
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
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

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / ".cache/upstream/dogbench"
sys.path.insert(0, str(UPSTREAM))
from dogbench import trace_audit  # noqa: E402
from dogbench.research_inputs import verify_research_workspace  # noqa: E402
from dogbench.verify import verify_attestation  # noqa: E402


def atomic(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


async def main(args):
    if args.workers < 1 or args.model_limit < 1:
        raise ValueError("workers and model-limit must be positive")
    if args.api_only_symlinks:
        from scripts.dogbench_compat import install_api_only_symlink_policy

        install_api_only_symlink_policy()
    os.environ.pop("GH_TOKEN", None)
    os.environ.pop("GITHUB_TOKEN", None)
    items_path = ROOT / ".cache/benchmarks/dogbench-data/items.jsonl"
    items = [json.loads(line) for line in items_path.read_text().splitlines() if line]
    if args.limit:
        items = items[: args.limit]
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    sources = list((ROOT / "src/shopresolve/documents").glob("*.py")) + [
        Path(__file__),
        ROOT / "scripts/evaluate_models.py",
    ]
    if args.subscription:
        sources += [
            ROOT / "scripts/codex_repository_gateway.py",
            ROOT / "scripts/score_dogbench.py",
            ROOT / "scripts/judge_windows_bridge.py",
            ROOT / "scripts/run_benchmark_linux.py",
        ]
    plan = {
        "protocol": "docchange-api-only-repository-adapter-v1",
        "dataset_sha256": hashlib.sha256(items_path.read_bytes()).hexdigest(),
        "items": [i["instance_id"] for i in items],
        "model": args.model,
        "model_limit": args.model_limit,
        "task_timeout_seconds": 900,
        "max_requests": len(items) * args.model_limit if args.subscription else 2800,
        "cap_cny": None if args.subscription else args.cap_cny,
        "billing": "Codex subscription; cost unknown" if args.subscription else "DeepSeek API",
        "cache_hit_rate_cny_per_million": 0.04 if args.cache_aware else None,
        "source_sha256": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources
        },
        "isolation": "API tool allowlist; no candidate shell/network; NOT upstream Docker CLI lane",
        "preparation": "Exact released prompts/trees; original prose overlap findings retained",
        "score_contract": "Unmodified official rubrics, evidence validator and numeric scorer",
    }
    if (out / "plan.json").exists():
        if json.loads((out / "plan.json").read_text()) != plan:
            raise RuntimeError("Frozen evaluation source/config changed; use a new batch")
    else:
        atomic(out / "plan.json", plan)
        for p in sources:
            target = out / "source" / p.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(p.read_bytes())
    budget = (
        None
        if args.subscription
        else EvaluationBudget(
            out / "budget.sqlite",
            2800,
            args.cap_cny,
            0,
            cache_hit_rate=0.04 if args.cache_aware else None,
        )
    )
    trace_audit.PRIMARY_AGENT_TRACE_NAMES.add("docchange.events.jsonl")
    semaphore = asyncio.Semaphore(args.workers)
    settings = Settings(
        provider="deepseek",
        model=args.model,
        model_base_url="https://api.deepseek.com",
        allow_paid_model=True,
        model_max_output_tokens=4096,
        model_timeout_seconds=85,
    )
    if not settings.model_api_key and not args.subscription:
        raise RuntimeError("Local .env must contain the configured provider key")

    async def one(item, prepared):
        key = item["instance_id"]
        folder = out / "tasks" / key
        folder.mkdir(parents=True, exist_ok=True)
        result_path = folder / "result.json"
        if result_path.exists():
            return json.loads(result_path.read_text())
        async with semaphore:
            start = time.perf_counter()
            row = {
                "instance_id": key,
                "strict_context_eligible": prepared.get("strict_context_eligible", True),
                "context_findings": prepared.get("context_findings", []),
            }
            if args.subscription:
                from scripts.codex_repository_gateway import CodexRepositoryGateway

                gateway = CodexRepositoryGateway(settings)
            else:
                gateway = RepositoryGateway(settings)
            engine, sessions = make_database(f"sqlite+aiosqlite:///{folder / 'runs.sqlite'}")
            try:
                workspace = Path(prepared["path"])
                if args.prune_candidates:
                    import subprocess

                    if not workspace.resolve().is_relative_to(
                        (args.prepared / "sealed/workspaces").resolve()
                    ):
                        raise ValueError("Only anonymous benchmark workspaces may be pruned")
                    for name in ("docs", "code"):
                        root = workspace / name
                        if root.exists():
                            clean = subprocess.run(
                                ["git", "-C", str(root), "status", "--porcelain"],
                                capture_output=True,
                                check=True,
                            )
                            if clean.stdout.strip():
                                raise ValueError("Benchmark working tree changed; preserved")
                            subprocess.run(
                                ["git", "-C", str(root), "gc", "--prune=now", "--quiet"], check=True
                            )
                # Verification runs before exporting any candidate Git object.
                await asyncio.to_thread(
                    verify_attestation, args.prepared / "sealed", Path(prepared["attestation"])
                )
                prompt = (workspace / "TASK.md").read_bytes()
                await asyncio.to_thread(verify_research_workspace, item, workspace, prompt)
                environment = RepositoryEnvironment(
                    workspace / "docs",
                    workspace / "code" if prepared["has_code"] else None,
                    folder / "controller",
                )
                async with engine.begin() as connection:
                    await connection.run_sync(Base.metadata.create_all)
                store = RunStore(sessions)
                conversation = await store.conversation("benchmark", str(uuid4()))
                cfg = {
                    "provider": "deepseek",
                    "model": settings.model,
                    "domain": "repository",
                    "model_limit": args.model_limit,
                    "active_budget_ms": 900000,
                }
                rid = (
                    await store.submit(
                        "benchmark",
                        conversation["id"],
                        {"text": prompt.decode(), "client_message_id": str(uuid4())},
                        str(uuid4()),
                        cfg,
                    )
                )["run_id"]
                lease = await store.claim(rid, "dogbench-docchange", lease_seconds=180)
                if args.subscription:
                    from scripts.codex_repository_gateway import SubscriptionRecordedGateway

                    wrapped = SubscriptionRecordedGateway(gateway, folder / "model-calls.jsonl")
                else:
                    wrapped = RecordedGateway(gateway, budget, folder / "model-calls.jsonl")
                runtime = RepositoryRuntime(
                    store, wrapped, environment, folder / "docchange.events.jsonl"
                )
                async with asyncio.timeout(900):
                    decision = await runtime.run(lease, prompt.decode())
                row.update(status="completed", **decision)
                row["run"] = await store.get("benchmark", rid)
                if row.get("patch"):
                    (folder / "candidate.patch").write_text(row["patch"], encoding="utf-8")
                repo = item["source_url"].split("github.com/", 1)[-1].split("/pull/")[0]
                pr = int(item["source_url"].rsplit("/", 1)[-1])
                audit = trace_audit.audit_post_dispatch_agent_trace(
                    folder,
                    source_repo=repo,
                    source_pr_number=pr,
                    agent="docchange",
                    protected_host_paths=(str(args.prepared), str(UPSTREAM)),
                )
                atomic(folder / "trace-audit.json", audit)
                row["trace_audit"] = audit
                if not audit.get("ok", False):
                    row.update(
                        status="invalid", error="Official trace audit rejected candidate actions"
                    )
                    row.pop("decision", None)
                    row.pop("patch", None)
            except Exception as exc:
                row.update(
                    status="timeout" if isinstance(exc, TimeoutError) else "error",
                    error_type=type(exc).__name__,
                    error=str(exc)[:1600],
                )
            finally:
                await gateway.aclose()
                await engine.dispose()
            row["latency_ms"] = (time.perf_counter() - start) * 1000
            atomic(result_path, row)
            print(
                json.dumps({k: row.get(k) for k in ("instance_id", "status", "decision", "error")}),
                flush=True,
            )
            return row

    pending = {i["instance_id"]: i for i in items}
    running, results = {}, []
    while pending or running:
        for key, item in list(pending.items()):
            path = args.prepared / f"{key}.json"
            if not path.exists():
                continue
            try:
                prepared = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
            if prepared["status"] == "prepared":
                running[asyncio.create_task(one(item, prepared))] = key
                del pending[key]
            elif not args.wait_preparation:
                results.append(
                    {
                        "instance_id": key,
                        "status": "error",
                        "error": "Preparation failed; candidate was not called",
                        "preparation_error": prepared.get("error"),
                    }
                )
                del pending[key]
        if running:
            done, _ = await asyncio.wait(running, timeout=5, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                results.append(task.result())
                del running[task]
        elif pending and not args.wait_preparation:
            raise RuntimeError("Preparation missing; run preparation or use --wait-preparation")
        else:
            await asyncio.sleep(5)
        public = {
            "tasks": len(items),
            "finished": len(results),
            "awaiting_preparation": len(pending),
            "completed": sum(r["status"] == "completed" for r in results),
            "budget": budget.summary()
            if budget is not None
            else {"billing": "Codex subscription", "monetary_cost_cny": None},
        }
        atomic(out / "summary.json", public)
        predictions = [
            {
                k: v
                for k, v in row.items()
                if k in {"instance_id", "status", "decision", "patch", "error"}
            }
            for row in results
        ]
        (out / "predictions.jsonl").write_text(
            "".join(json.dumps(p) + "\n" for p in predictions), encoding="utf-8"
        )
    report = {"plan": plan, "summary": public, "results": results}
    atomic(out / "results.json", report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--prepared", type=Path, default=Path.home() / ".cache/docchange/dogbench-prepared"
    )
    parser.add_argument("--output", type=Path, default=Path(".cache/evals/dogbench-20261102"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--cap-cny", type=float, default=15)
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--model-limit", type=int, default=16)
    parser.add_argument("--subscription", action="store_true")
    parser.add_argument("--cache-aware", action="store_true")
    parser.add_argument("--api-only-symlinks", action="store_true")
    parser.add_argument("--prune-candidates", action="store_true")
    parser.add_argument("--wait-preparation", action="store_true")
    asyncio.run(main(parser.parse_args()))
