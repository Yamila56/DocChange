"""Full released DoGBench candidate generation with the DocChange tool adapter.

Run on Linux after prepare_dogbench.py. Official labels/rubrics are deliberately
not loaded by candidate generation. The controller alone sees preparation IDs.
"""

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from scripts.atomic_report import atomic, atomic_text
from scripts.dogbench_outcomes import official_prediction, runtime_result
from scripts.evaluate_models import EvaluationBudget, RecordedGateway
from shopresolve.config import Settings
from shopresolve.db import make_database
from shopresolve.documents.model_profile import PRO_MODEL, cache_hit_rate
from shopresolve.documents.repository import (
    RepositoryDecision,
    RepositoryEnvironment,
    RepositoryGateway,
    RepositoryRuntime,
)
from shopresolve.documents.repository_search import encoder_for
from shopresolve.models import Base
from shopresolve.runstore import RunStore

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / ".cache/upstream/dogbench"
sys.path.insert(0, str(UPSTREAM))
from dogbench import trace_audit  # noqa: E402
from dogbench.research_inputs import verify_research_workspace  # noqa: E402
from dogbench.verify import verify_attestation  # noqa: E402


async def main(args):
    if args.workers < 1 or args.model_limit < 1:
        raise ValueError("workers and model-limit must be positive")
    task_timeout = getattr(args, "task_timeout_seconds", 900)
    request_timeout = getattr(args, "request_timeout_seconds", None) or (
        120 if args.model == PRO_MODEL else 85
    )
    if task_timeout <= 0 or request_timeout <= 0 or request_timeout > task_timeout:
        raise ValueError("Require 0 < request timeout <= task timeout")
    if args.retrieval_provider == "CUDAExecutionProvider":
        import onnxruntime as ort

        if args.cuda_dll_dir:
            ort.preload_dlls(directory=str(args.cuda_dll_dir))
    if args.api_only_symlinks:
        from scripts.dogbench_compat import install_api_only_symlink_policy

        install_api_only_symlink_policy()
    os.environ.pop("GH_TOKEN", None)
    os.environ.pop("GITHUB_TOKEN", None)
    items_path = ROOT / ".cache/benchmarks/dogbench-data/items.jsonl"
    items = [json.loads(line) for line in items_path.read_text().splitlines() if line]
    cohort = getattr(args, "cohort_predictions", None)
    if cohort:
        # Read IDs only. Prior outputs and scores never enter candidate context.
        ids = [
            json.loads(line)["instance_id"]
            for line in cohort.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not ids or len(ids) != len(set(ids)) or set(ids) - {i["instance_id"] for i in items}:
            raise ValueError("Cohort must contain unique released task IDs")
        items = [item for item in items if item["instance_id"] in set(ids)]
    if args.limit:
        items = items[: args.limit]
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    sources = list((ROOT / "src/shopresolve/documents").glob("*.py")) + [
        Path(__file__),
        ROOT / "scripts/evaluate_models.py",
        ROOT / "scripts/atomic_report.py",
        ROOT / "scripts/dogbench_outcomes.py",
        ROOT / "src/shopresolve/db.py",
        ROOT / "src/shopresolve/runstore.py",
        ROOT / "src/shopresolve/service.py",
        ROOT / "scripts/verify_repository_input.py",
        ROOT / "scripts/repository_index_bridge.py",
    ]
    if getattr(args, "capture_error_usage", False):
        sources.append(ROOT / "scripts/resume_offpeak_dogbench.py")
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
        "task_timeout_seconds": task_timeout,
        "max_requests": len(items) * args.model_limit if args.subscription else 2800,
        "cap_cny": None if args.subscription else args.cap_cny,
        "billing": "Codex subscription; cost unknown" if args.subscription else "DeepSeek API",
        "cache_hit_rate_cny_per_million": cache_hit_rate(args.model) if args.cache_aware else None,
        "retrieval_mode": args.retrieval_mode,
        "repository_policy": getattr(args, "repository_policy", "coverage-v1"),
        "retrieval_provider": args.retrieval_provider,
        "thinking": "enabled/high" if args.model == PRO_MODEL else "disabled",
        "source_sha256": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources
        },
        "isolation": "API tool allowlist; no candidate shell/network; NOT upstream Docker CLI lane",
        "preparation": "Exact released prompts/trees; original prose overlap findings retained",
        "score_contract": "Unmodified official rubrics, evidence validator and numeric scorer",
    }
    if os.name == "nt" and plan["repository_policy"] == "taskgraph-v2":
        plan["implementation_index_io"] = "WSL-local reader for verified Linux workspaces"
    if cohort:
        plan["cohort_ids_sha256"] = hashlib.sha256(
            json.dumps(sorted(ids), separators=(",", ":")).encode()
        ).hexdigest()
    if getattr(args, "request_timeout_seconds", None) is not None:
        plan["request_timeout_seconds"] = request_timeout
    if getattr(args, "capture_error_usage", False):
        plan["capture_error_usage"] = True
    if (out / "plan.json").exists():
        previous = json.loads((out / "plan.json").read_text())
        if previous != plan and (out / "source-repair-amendment.json").exists():
            repair = json.loads((out / "source-repair-amendment.json").read_text())
            for name, change in repair["files"].items():
                if previous["source_sha256"].get(name) != change["before_sha256"]:
                    raise RuntimeError("Repair amendment does not match original frozen source")
                previous["source_sha256"][name] = change["after_sha256"]
        if previous != plan:
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
            cache_hit_rate=cache_hit_rate(args.model) if args.cache_aware else None,
        )
    )
    trace_audit.PRIMARY_AGENT_TRACE_NAMES.add("docchange.events.jsonl")
    semaphore = asyncio.Semaphore(args.workers)
    budget_exhausted = asyncio.Event()
    settings = Settings(
        provider="deepseek",
        model=args.model,
        model_base_url="https://api.deepseek.com",
        allow_paid_model=True,
        model_max_output_tokens=8192 if args.model == PRO_MODEL else 4096,
        model_timeout_seconds=request_timeout,
        model_max_input_bytes=120000,
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
            if budget_exhausted.is_set():
                return None
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
                if getattr(args, "capture_error_usage", False):
                    from scripts.resume_offpeak_dogbench import UsageCapturingGateway

                    gateway = UsageCapturingGateway(settings)
                else:
                    gateway = RepositoryGateway(settings)
            engine, sessions = make_database(f"sqlite+aiosqlite:///{folder / 'runs.sqlite'}")
            try:
                native_windows = os.name == "nt" and prepared["path"].startswith("/")
                workspace = (
                    Path("//wsl.localhost/Ubuntu" + prepared["path"])
                    if native_windows
                    else Path(prepared["path"])
                )
                if args.prune_candidates:
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
                prompt = (workspace / "TASK.md").read_bytes()
                if native_windows:
                    wsl_command = [
                        "wsl",
                        "-d",
                        "Ubuntu",
                        "--cd",
                        "/mnt/" + ROOT.drive[0].lower() + ROOT.as_posix()[2:],
                        "--",
                        ".venv-bench-linux/bin/python",
                    ]
                    command = [*wsl_command, "-m", "scripts.verify_repository_input", key]
                    verified = await asyncio.to_thread(
                        subprocess.run, command, capture_output=True, check=True
                    )
                    attestation = json.loads(verified.stdout.decode("utf-8"))
                    if (
                        not attestation["ok"]
                        or attestation["prompt_sha256"] != hashlib.sha256(prompt).hexdigest()
                    ):
                        raise ValueError(
                            "Verified Linux prompt differs from native candidate input"
                        )
                else:
                    await asyncio.to_thread(
                        verify_attestation, args.prepared / "sealed", Path(prepared["attestation"])
                    )
                    await asyncio.to_thread(verify_research_workspace, item, workspace, prompt)
                encoder = (
                    encoder_for(".cache/models", args.retrieval_provider)
                    if args.retrieval_mode != "section_bm25"
                    else None
                )
                environment_type = RepositoryEnvironment
                environment_options = {}
                if native_windows and plan["repository_policy"] == "taskgraph-v2":
                    from scripts.repository_index_bridge import WslRepositoryEnvironment

                    environment_type = WslRepositoryEnvironment
                    environment_options = {
                        "wsl_command": wsl_command,
                        "workspace": prepared["path"],
                    }
                environment = environment_type(
                    workspace / "docs",
                    workspace / "code" if prepared["has_code"] else None,
                    folder / "controller",
                    retrieval_mode=args.retrieval_mode,
                    encoder=encoder,
                    **environment_options,
                )
                await asyncio.to_thread(
                    environment.execute, RepositoryDecision(kind="search", query="documentation")
                )
                environment.evidence.clear()
                environment.read_paths.clear()
                environment.observed_spans.clear()
                async with engine.begin() as connection:
                    await connection.run_sync(Base.metadata.create_all)
                store = RunStore(sessions)
                cfg = {
                    "provider": "deepseek",
                    "model": settings.model,
                    "domain": "repository",
                    "repository_policy": getattr(args, "repository_policy", "coverage-v1"),
                    "model_limit": args.model_limit,
                    "model_timeout_seconds": settings.model_timeout_seconds,
                    "active_budget_ms": task_timeout * 1000,
                }
                execution_path = folder / "execution.json"
                if execution_path.exists():
                    rid = json.loads(execution_path.read_text())["run_id"]
                else:
                    conversation = await store.conversation("benchmark", str(uuid4()))
                    rid = (
                        await store.submit(
                            "benchmark",
                            conversation["id"],
                            {"text": prompt.decode(), "client_message_id": str(uuid4())},
                            str(uuid4()),
                            cfg,
                        )
                    )["run_id"]
                    atomic(execution_path, {"run_id": rid, "config": cfg})
                lease = await store.claim(rid, "dogbench-docchange", lease_seconds=180)
                if lease is None:
                    raise RuntimeError(
                        "Persisted run is still leased or terminal; do not regenerate it"
                    )
                if args.subscription:
                    from scripts.codex_repository_gateway import SubscriptionRecordedGateway

                    wrapped = SubscriptionRecordedGateway(gateway, folder / "model-calls.jsonl")
                else:
                    wrapped = RecordedGateway(gateway, budget, folder / "model-calls.jsonl")
                runtime = RepositoryRuntime(
                    store, wrapped, environment, folder / "docchange.events.jsonl"
                )
                async with asyncio.timeout(task_timeout):
                    decision = await runtime.run(lease, prompt.decode())
                row.update(runtime_result(decision))
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
                if getattr(exc, "code", None) == "EVALUATION_BUDGET":
                    # Account limits are an incomplete experiment, not an agent failure.
                    budget_exhausted.set()
                    row.update(status="deferred", error="Existing-balance evaluation cap reached")
                else:
                    row.update(
                        status="timeout" if isinstance(exc, TimeoutError) else "error",
                        error_type=type(exc).__name__,
                        error=str(exc)[:1600],
                    )
            finally:
                await gateway.aclose()
                await engine.dispose()
            row["latency_ms"] = (time.perf_counter() - start) * 1000
            if row["status"] == "deferred":
                atomic(folder / "deferred.json", row)
                return None
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
                result = task.result()
                if result is not None:
                    results.append(result)
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
            "delivered": sum(
                r["status"] == "completed" and r.get("decision") in {"patch", "abstain"}
                for r in results
            ),
            "unresolved": sum(
                r["status"] == "completed" and r.get("decision") == "unresolved" for r in results
            ),
            "execution_errors": sum(r["status"] != "completed" for r in results),
            "budget_exhausted": budget_exhausted.is_set(),
            "complete": len(results) == len(items),
            "budget": budget.summary()
            if budget is not None
            else {"billing": "Codex subscription", "monetary_cost_cny": None},
        }
        atomic(out / "summary.json", public)
        predictions = [official_prediction(row) for row in results]
        atomic_text(out / "predictions.jsonl", "".join(json.dumps(p) + "\n" for p in predictions))
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
    parser.add_argument("--model", default=PRO_MODEL)
    parser.add_argument(
        "--retrieval-mode", choices=["section_bm25", "dense", "hybrid"], default="hybrid"
    )
    parser.add_argument("--model-limit", type=int, default=16)
    parser.add_argument(
        "--repository-policy", choices=["coverage-v1", "taskgraph-v2"], default="coverage-v1"
    )
    parser.add_argument("--task-timeout-seconds", type=int, default=900)
    parser.add_argument("--request-timeout-seconds", type=int)
    parser.add_argument("--cohort-predictions", type=Path)
    parser.add_argument("--capture-error-usage", action="store_true")
    parser.add_argument(
        "--retrieval-provider",
        choices=["CPUExecutionProvider", "CUDAExecutionProvider"],
        default="CPUExecutionProvider",
    )
    parser.add_argument("--cuda-dll-dir", type=Path)
    parser.add_argument("--subscription", action="store_true")
    parser.add_argument("--cache-aware", action="store_true")
    parser.add_argument("--api-only-symlinks", action="store_true")
    parser.add_argument("--prune-candidates", action="store_true")
    parser.add_argument("--wait-preparation", action="store_true")
    asyncio.run(main(parser.parse_args()))
