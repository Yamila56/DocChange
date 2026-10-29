"""Post-evaluation targeted regression, explicitly NOT a fresh held-out score."""

import argparse
import asyncio
import json
from pathlib import Path

from scripts.evaluate_models import EvaluationBudget, RecordedGateway, canonical, source_manifest
from scripts.evaluate_workflows import evaluate
from scripts.resume_model_evaluation import peak_cached_carry
from shopresolve.agent.gateway import CompatibleJsonGateway
from shopresolve.config import Settings
from shopresolve.retrieval.index import PolicyIndex


async def main(args):
    source = Path("evals/data/commerce-evaluation-v2.json")
    data = json.loads(source.read_text(encoding="utf-8"))
    cases = [c for c in data["cases"] if c["id"] in {"f03", "f08", "f09", "f11"}]
    carry = peak_cached_carry(
        Path(".cache/evals/formal-paired-20261028-v2/budget.sqlite"), "0.567894"
    )
    carry = peak_cached_carry(
        Path(".cache/evals/formal-paired-20261028-v2-continuation/budget.sqlite"), carry
    )
    plan = {
        "type": "post-feedback targeted regression, not held-out model comparison",
        "cases": [c["id"] for c in cases],
        "models": ["deepseek-flash", "deepseek-v4-pro"],
        "carry_cny": str(carry),
        "total_cap_cny": 5,
        "source_sha256": source_manifest(source),
    }
    print(canonical({k: v for k, v in plan.items() if k != "source_sha256"}), flush=True)
    if not args.execute:
        return
    root = Path(".cache/evals/evaluation-fixes-20261028-v3")
    root.mkdir(exist_ok=False)
    (root / "plan.json").write_text(canonical(plan), encoding="utf-8")
    budget = EvaluationBudget(root / "budget.sqlite", 64, 5, carry)
    results = []
    try:
        for case in cases:
            for model in plan["models"]:
                if source_manifest(source) != plan["source_sha256"]:
                    raise RuntimeError("Candidate source changed")
                folder = root / f"{model}-{case['id']}"
                folder.mkdir()
                client = CompatibleJsonGateway(
                    Settings(
                        provider="deepseek",
                        model=model,
                        model_base_url="https://api.deepseek.com",
                        allow_paid_model=True,
                        model_timeout_seconds=30,
                    )
                )
                try:
                    result = await evaluate(
                        case,
                        folder,
                        PolicyIndex("data/policies/demo.json"),
                        gateway=RecordedGateway(client, budget, folder / "decisions.jsonl"),
                        model_config={
                            "provider": "deepseek",
                            "model": model,
                            "model_limit": 8,
                            "model_timeout_seconds": 30,
                        },
                    )
                finally:
                    await client.aclose()
                result["model"] = model
                results.append(result)
                (folder / "result.json").write_text(canonical(result), encoding="utf-8")
                print(model, case["id"], result["passed"], flush=True)
                if result.get("final_state", {}).get("stop_reason") == "EVALUATION_BUDGET":
                    return
    finally:
        (root / "results.json").write_text(
            canonical({"plan": plan, "results": results, "budget": budget.summary()}),
            encoding="utf-8",
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    asyncio.run(main(parser.parse_args()))
