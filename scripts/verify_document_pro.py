"""Read-only account preflight; --execute adds one bounded Pro schema contract call."""

import argparse
import asyncio
import json
from decimal import Decimal
from pathlib import Path

import httpx

from scripts.evaluate_models import EvaluationBudget, RecordedGateway
from shopresolve.documents.model_profile import PRO_MODEL, pro_settings
from shopresolve.documents.repository import RepositoryDecision, RepositoryGateway


async def main(execute):
    settings = pro_settings()
    if not settings.model_api_key:
        raise RuntimeError("Local model key is missing; never send keys in chat")
    async with httpx.AsyncClient(
        base_url=settings.model_base_url,
        trust_env=False,
        timeout=20,
        headers={"Authorization": f"Bearer {settings.model_api_key}"},
    ) as client:
        models = await client.get("/models")
        models.raise_for_status()
        balance = await client.get("/user/balance")
        balance.raise_for_status()
        account = balance.json()
        available = any(
            r.get("currency") == "CNY" and Decimal(r["total_balance"]) >= Decimal("0.75")
            for r in account.get("balance_infos", [])
        )
        report = {
            "configured_model": PRO_MODEL,
            "thinking": "enabled/high",
            "model_listed": PRO_MODEL in {r["id"] for r in models.json().get("data", [])},
            "account_available": account.get("is_available") is True,
            "can_cover_contract_reserve": available,
            "maximum_requests": 1,
            "estimated_cap_cny": "0.75",
            "scope": "API/schema compatibility only; not a task-quality benchmark",
        }
    if execute:
        if not all(
            report[k] for k in ("model_listed", "account_available", "can_cover_contract_reserve")
        ):
            raise RuntimeError("Pro account/model preflight did not pass; no completion sent")
        folder = Path(".cache/evals/pro-document-contract-20261103")
        folder.mkdir(parents=True, exist_ok=True)
        budget = EvaluationBudget(folder / "budget.sqlite", 1, 0.75, 0, cache_hit_rate=0.30)
        # Only released task context, never human paths, outcomes or rubric.
        item = json.loads(
            Path(".cache/benchmarks/dogbench-data/items.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )
        context = {
            "task_prompt": item["context"],
            "observations": [],
            "read_paths": [],
            "remaining_decisions": 16,
        }
        gateway = RepositoryGateway(settings)
        try:
            reply = await RecordedGateway(gateway, budget, folder / "calls.jsonl").request(
                "DOCUMENT_REPOSITORY", context
            )
            decision = RepositoryDecision.model_validate(reply.output)
            report.update(
                schema_valid=True,
                returned_model=reply.provider_model,
                decision_kind=decision.kind,
                usage=reply.usage,
                budget=budget.summary(),
            )
            if reply.provider_model != PRO_MODEL:
                raise RuntimeError("Provider returned an unexpected model; no fallback accepted")
        finally:
            await gateway.aclose()
        Path("docs/evaluation/pro-contract-2026-11-03.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    asyncio.run(main(parser.parse_args().execute))
