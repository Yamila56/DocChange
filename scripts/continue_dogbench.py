"""Resume interrupted official tasks; never replace a completed semantic decision.

The first-stage record is immutable. Additional model calls share the original
16-call task cap. A separate 5 CNY ledger bounds this recovery stage.
"""

import argparse
import asyncio
import copy
import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from scripts.evaluate_dogbench import (
    ROOT,
    atomic,
    trace_audit,
    verify_attestation,
    verify_research_workspace,
)
from scripts.evaluate_models import EvaluationBudget, RecordedGateway
from shopresolve.config import Settings
from shopresolve.db import make_database
from shopresolve.documents.repository import (
    RepositoryEnvironment,
    RepositoryGateway,
    RepositoryRuntime,
)
from shopresolve.models import Base, Run
from shopresolve.runstore import RunStore


async def main(args):
    if args.api_only_symlinks:
        from scripts.dogbench_compat import install_api_only_symlink_policy

        install_api_only_symlink_policy()
    original = args.parent.resolve()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    first = ROOT / ".cache/evals/dogbench-20261102"
    old_plan = json.loads((first / "plan.json").read_text(encoding="utf-8"))
    for name, checksum in old_plan["source_sha256"].items():
        if (
            name.startswith("src/")
            and hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != checksum
        ):
            raise RuntimeError("Evaluated source drifted; recovery cannot use a different policy")
    atomic(
        out / "plan.json",
        {
            "stage": "checkpoint-recovery-v1",
            "parent_plan_sha256": hashlib.sha256((original / "plan.json").read_bytes()).hexdigest(),
            "extra_cap_cny": args.cap_cny,
            "completed_decisions_repeated": False,
            "combined_task_call_limit": 16,
        },
    )
    items = [
        json.loads(line)
        for line in (ROOT / ".cache/benchmarks/dogbench-data/items.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    budget = EvaluationBudget(out / "budget.sqlite", 1800, args.cap_cny, 0)
    settings = Settings(
        provider="deepseek",
        model="deepseek-flash",
        model_base_url="https://api.deepseek.com",
        allow_paid_model=True,
        model_timeout_seconds=85,
    )
    semaphore = asyncio.Semaphore(3)
    trace_audit.PRIMARY_AGENT_TRACE_NAMES.add("docchange.events.jsonl")

    async def one(item):
        key = item["instance_id"]
        folder = out / "tasks" / key
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "result.json"
        if target.exists():
            return json.loads(target.read_text(encoding="utf-8"))
        old_folder = original / "tasks" / key
        prior = (
            json.loads((old_folder / "result.json").read_text(encoding="utf-8"))
            if (old_folder / "result.json").exists()
            else None
        )
        if prior and (
            prior["status"] == "completed"
            or "model-call budget exhausted" in prior.get("error", "").lower()
        ):
            atomic(target, prior)
            return prior
        row = {"instance_id": key}
        prepared_path = args.prepared / f"{key}.json"
        if not prepared_path.exists():
            row.update(
                status="error",
                error="Preparation did not complete; candidate not called",
                preparation_failed=True,
            )
            atomic(target, row)
            return row
        prepared = json.loads(prepared_path.read_text(encoding="utf-8"))
        if prepared["status"] != "prepared":
            row.update(
                status="error",
                error="Official preparation failed; candidate not called",
                preparation_failed=True,
                preparation_error=prepared.get("error"),
            )
            atomic(target, row)
            return row
        async with semaphore:
            start = time.perf_counter()
            gateway = RepositoryGateway(settings)
            engine, sessions = make_database(f"sqlite+aiosqlite:///{folder / 'runs.sqlite'}")
            try:
                workspace = Path(prepared["path"])
                await asyncio.to_thread(
                    verify_attestation, args.prepared / "sealed", Path(prepared["attestation"])
                )
                prompt = (workspace / "TASK.md").read_bytes()
                await asyncio.to_thread(verify_research_workspace, item, workspace, prompt)
                saved, used = None, 0
                if (old_folder / "runs.sqlite").exists():
                    old_engine, old_sessions = make_database(
                        f"sqlite+aiosqlite:///{old_folder / 'runs.sqlite'}"
                    )
                    async with old_sessions() as session:
                        old_run = await session.scalar(select(Run))
                        if old_run:
                            saved = copy.deepcopy(old_run.state)
                            used = 16 - saved["config"]["model_limit"] + old_run.model_calls
                    await old_engine.dispose()
                if used >= 16:
                    raise RuntimeError("Original per-task model-call budget exhausted")
                async with engine.begin() as connection:
                    await connection.run_sync(Base.metadata.create_all)
                store = RunStore(sessions)
                conversation = await store.conversation("benchmark", str(uuid4()))
                cfg = {
                    "provider": "deepseek",
                    "model": settings.model,
                    "domain": "repository",
                    "model_limit": 16 - used,
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
                lease = await store.claim(rid, "dogbench-recovery", lease_seconds=180)
                if saved:
                    saved["config"], saved["pending"] = cfg, None
                    saved.pop("answer", None)
                    await store.save(
                        lease,
                        saved,
                        kind="repository.recovery_import",
                        payload={"prior_model_calls": used},
                    )
                environment = RepositoryEnvironment(
                    workspace / "docs",
                    workspace / "code" if prepared["has_code"] else None,
                    folder / "controller",
                )
                wrapped = RecordedGateway(gateway, budget, folder / "model-calls.jsonl")
                async with asyncio.timeout(900):
                    decision = await RepositoryRuntime(
                        store, wrapped, environment, folder / "docchange.events.jsonl"
                    ).run(lease, prompt.decode())
                row.update(
                    status="completed",
                    **decision,
                    run=await store.get("benchmark", rid),
                    prior_model_calls=used,
                    strict_context_eligible=prepared.get("strict_context_eligible", True),
                    context_findings=prepared.get("context_findings", []),
                )
                audit = trace_audit.audit_post_dispatch_agent_trace(
                    folder,
                    source_repo=item["source_url"].split("github.com/")[-1].split("/pull/")[0],
                    source_pr_number=int(item["source_url"].rsplit("/", 1)[-1]),
                    agent="docchange",
                )
                row["trace_audit"] = audit
                if not audit["ok"]:
                    row.update(
                        status="invalid", error="Official action trace audit rejected recovery"
                    )
                    row.pop("decision", None)
                    row.pop("patch", None)
            except Exception as exc:
                row.update(status="error", error=str(exc)[:1500], error_type=type(exc).__name__)
            finally:
                await gateway.aclose()
                await engine.dispose()
            row["latency_ms"] = (time.perf_counter() - start) * 1000
            if prior:
                row["first_attempt_status"] = prior["status"]
                row["first_attempt_error"] = prior.get("error")
            atomic(target, row)
            print(
                json.dumps({k: row.get(k) for k in ("instance_id", "status", "decision", "error")}),
                flush=True,
            )
            return row

    rows = []
    for future in asyncio.as_completed([one(item) for item in items]):
        rows.append(await future)
        atomic(
            out / "summary.json",
            {
                "tasks": 175,
                "finished": len(rows),
                "completed": sum(r["status"] == "completed" for r in rows),
                "budget": budget.summary(),
            },
        )
        fields = {"instance_id", "status", "decision", "patch", "error"}
        (out / "predictions.jsonl").write_text(
            "".join(json.dumps({k: v for k, v in r.items() if k in fields}) + "\n" for r in rows),
            encoding="utf-8",
        )
    atomic(out / "results.json", {"results": rows, "budget": budget.summary()})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--prepared", type=Path, default=Path.home() / ".cache/docchange/dogbench-prepared"
    )
    parser.add_argument(
        "--output", type=Path, default=Path(".cache/evals/dogbench-recovery-20261102")
    )
    parser.add_argument("--parent", type=Path, default=Path(".cache/evals/dogbench-20261102"))
    parser.add_argument("--cap-cny", type=float, default=5)
    parser.add_argument("--api-only-symlinks", action="store_true")
    asyncio.run(main(parser.parse_args()))
