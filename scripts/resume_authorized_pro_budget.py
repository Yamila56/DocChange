"""Explicit budget amendment, retaining the frozen policy, attempts and completed outputs.

Use only after the operator authorizes the new TOTAL cap and the previous process exits.
This controller changes the spend ceiling, not candidate code or per-task turn budgets.
"""

import argparse
import asyncio
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from scripts import evaluate_dogbench as evaluation
from scripts.evaluate_models import EvaluationBudget, canonical


def main(args):
    batch = args.batch.resolve()
    plan_path = batch / "plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan["model"] != "deepseek-v4-pro" or len(plan["items"]) != 175:
        raise ValueError("Expected the complete frozen Pro cohort")
    for name, expected in plan["source_sha256"].items():
        if hashlib.sha256((evaluation.ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Frozen evaluated source changed: " + name)
    total = Decimal(str(args.authorized_total_cny))
    if not Decimal(str(plan["cap_cny"])) <= total <= Decimal("60"):
        raise ValueError("This release is authorized for at most 60 CNY total")
    ledger = batch / "budget.sqlite"
    with sqlite3.connect(ledger) as db:
        db.execute("BEGIN IMMEDIATE")
        raw = db.execute("SELECT value FROM config").fetchone()[0]
        config = json.loads(raw)
        if Decimal(config["cap"]) not in {Decimal(str(plan["cap_cny"])), total}:
            raise ValueError("Unexpected prior budget configuration")
        amendment = batch / "authorized-budget-amendment.json"
        if not amendment.exists():
            evaluation.atomic(
                amendment,
                {
                    "authorized_total_cny": str(total),
                    "initial_cap_cny": config["cap"],
                    "timestamp": datetime.now(UTC).isoformat(),
                    "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
                    "prior_config_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                    "retained_attempts": db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0],
                    "candidate_source_unchanged": True,
                    "completed_results_regenerated": False,
                    "authorization": "User approved 60 CNY total; no automatic recharge",
                },
            )
        elif Decimal(json.loads(amendment.read_text())["authorized_total_cny"]) != total:
            raise ValueError("Existing authorization record differs")
        config["cap"] = str(total)
        db.execute("UPDATE config SET value=?", (canonical(config),))

    class AmendedBudget(EvaluationBudget):
        def __init__(self, path, max_calls, cap_cny, carry_cny, cache_hit_rate=None):
            if path.resolve() != ledger:
                raise ValueError("Budget amendment may only use its existing ledger")
            super().__init__(path, max_calls, total, carry_cny, cache_hit_rate)

    evaluation.EvaluationBudget = AmendedBudget
    # Preserve the original plan byte-for-byte; publish the authorization alongside it.
    asyncio.run(
        evaluation.main(
            SimpleNamespace(
                workers=args.workers,
                model_limit=plan["model_limit"],
                api_only_symlinks=True,
                limit=0,
                output=batch,
                subscription=False,
                model=plan["model"],
                retrieval_mode=plan["retrieval_mode"],
                retrieval_provider=plan["retrieval_provider"],
                cuda_dll_dir=args.cuda_dll_dir,
                cap_cny=plan["cap_cny"],
                cache_aware=True,
                prepared=args.prepared,
                prune_candidates=False,
                wait_preparation=False,
            )
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--authorized-total-cny", type=Decimal, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--cuda-dll-dir", type=Path)
    main(parser.parse_args())
