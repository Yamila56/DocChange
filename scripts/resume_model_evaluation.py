"""Complete budget-censored trials without changing frozen app/data/prompt sources.

Original trials and ledgers remain untouched. Known cache-token discounts are
reconciled at PEAK rates; this changes accounting, never the task grader.
"""

import argparse
import asyncio
import hashlib
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

from scripts.evaluate_models import (
    TARIFF,
    EvaluationBudget,
    RecordedGateway,
    canonical,
    source_manifest,
    summarize,
)
from scripts.evaluate_workflows import evaluate
from shopresolve.agent.gateway import CompatibleJsonGateway
from shopresolve.config import Settings
from shopresolve.retrieval.index import PolicyIndex


def peak_cached_carry(path, initial_carry):
    total = Decimal(str(initial_carry))
    with sqlite3.connect(path) as db:
        rows = db.execute("SELECT model,reserve,usage FROM attempts").fetchall()
    for model, reserve, raw in rows:
        if raw is None:
            total += Decimal(reserve)
            continue
        usage = json.loads(raw)
        prompt, completion = usage["prompt_tokens"], usage["completion_tokens"]
        hit = usage.get("prompt_cache_hit_tokens")
        miss = usage.get("prompt_cache_miss_tokens")
        if not (
            type(hit) is int
            and type(miss) is int
            and hit >= 0
            and miss >= 0
            and hit + miss == prompt
        ):
            hit, miss = 0, prompt
        cached_rate = Decimal("0.04") if model == "deepseek-flash" else Decimal("0.30")
        input_rate, output_rate = TARIFF[model]
        total += (
            Decimal(miss) * input_rate
            + Decimal(hit) * cached_rate
            + Decimal(completion) * output_rate
        ) / 1_000_000
    return total


async def main(args):
    original = Path(".cache/evals/formal-paired-20261028-v2")
    raw = (original / "results.json").read_bytes()
    previous = json.loads(raw)
    source = Path("evals/data/commerce-evaluation-v2.json")
    if source_manifest(source) != previous["plan"]["source_sha256"]:
        raise RuntimeError("Frozen experimental sources differ; cannot continue")
    data = json.loads(source.read_text(encoding="utf-8"))
    censored = [
        r
        for r in previous["results"]
        if r.get("final_state", {}).get("stop_reason") == "EVALUATION_BUDGET"
    ]
    existing = [r for r in previous["results"] if r not in censored]
    done = {(r["model"], r["case_id"], r["trial"]) for r in existing}
    pending = []
    models = list(TARIFF)
    for trial in range(2):
        for i, case in enumerate(data["cases"]):
            for model in models if (i + trial) % 2 == 0 else list(reversed(models)):
                if (model, case["id"], trial) not in done:
                    pending.append((model, case, trial))
    carry = peak_cached_carry(original / "budget.sqlite", previous["plan"]["carry_cny"])
    plan = {
        "original_results_sha256": hashlib.sha256(raw).hexdigest(),
        "unchanged_experiment": previous["plan"],
        "pending_trials": len(pending),
        "carry_peak_with_known_cache_cny": str(carry),
        "total_cap_cny": 5,
        "censored_trial_count": len(censored),
        "accounting_change": "Known returned cache hits use published peak cache rates. "
        "Unknown attempts retain full reserves. No original record overwritten.",
    }
    print(canonical({k: v for k, v in plan.items() if k != "unchanged_experiment"}), flush=True)
    if not args.execute:
        return
    root = Path(".cache/evals/formal-paired-20261028-v2-continuation")
    root.mkdir(exist_ok=False)
    (root / "plan.json").write_text(canonical(plan), encoding="utf-8")
    budget = EvaluationBudget(root / "budget.sqlite", len(pending) * 8, 5, carry)
    clients = {
        model: CompatibleJsonGateway(
            Settings(
                provider="deepseek",
                model=model,
                model_base_url="https://api.deepseek.com",
                allow_paid_model=True,
                model_timeout_seconds=30,
            )
        )
        for model in models
    }
    results = existing.copy()
    try:
        for model, case, trial in pending:
            if source_manifest(source) != previous["plan"]["source_sha256"]:
                raise RuntimeError("Frozen experimental source changed")
            folder = root / f"{model}-{case['id']}-{trial}"
            folder.mkdir()
            gateway = RecordedGateway(clients[model], budget, folder / "decisions.jsonl")
            try:
                result = await evaluate(
                    case,
                    folder,
                    PolicyIndex("data/policies/demo.json"),
                    gateway=gateway,
                    model_config={
                        "provider": "deepseek",
                        "model": model,
                        "model_limit": 8,
                        "model_timeout_seconds": 30,
                    },
                )
            except Exception as exc:
                result = {
                    "case_id": case["id"],
                    "passed": False,
                    "infrastructure_error": type(exc).__name__,
                }
            result.update(model=model, trial=trial, family=case["family"])
            results.append(result)
            (folder / "result.json").write_text(canonical(result), encoding="utf-8")
            print(model, case["id"], trial, result["passed"], flush=True)
            if result.get("final_state", {}).get("stop_reason") == "EVALUATION_BUDGET":
                break
    finally:
        for client in clients.values():
            await client.aclose()
        (root / "combined-results.json").write_text(
            canonical(
                {
                    "plan": plan,
                    "results": results,
                    "censored_original_trials": censored,
                    "summary": summarize(results),
                    "continuation_budget": budget.summary(),
                    "original_budget": previous["budget"],
                }
            ),
            encoding="utf-8",
        )
        print("Combined report:", root / "combined-results.json", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    asyncio.run(main(parser.parse_args()))
