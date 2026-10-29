"""Three-case integration smoke, not a held-out benchmark. Default is a free dry run.

Use a unique ignored SQLite ledger per paid batch. Attempt reservations persist even
if a response is lost. This is a conservative estimate, NOT a provider billing cap.
"""

import argparse
import asyncio
import hashlib
import json
import sqlite3
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from shopresolve.agent.gateway import CompatibleJsonGateway
from shopresolve.config import Settings
from shopresolve.errors import DomainError
from shopresolve.retrieval.index import PolicyIndex

if __package__:
    from scripts.evaluate_workflows import evaluate
else:
    from evaluate_workflows import evaluate


class PilotBudget:
    def __init__(self, path, max_calls, max_cny, reserve_cny):
        self.path = path
        self.max_calls = max_calls
        self.max_cny = Decimal(str(max_cny))
        self.reserve_cny = Decimal(str(reserve_cny))
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS attempts (id INTEGER PRIMARY KEY, reserve TEXT)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS settlements (attempt_id INTEGER PRIMARY KEY, "
                "cost TEXT NOT NULL, usage TEXT NOT NULL)"
            )

    def reserve(self):
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT COALESCE(s.cost, a.reserve) FROM attempts a "
                "LEFT JOIN settlements s ON s.attempt_id=a.id"
            ).fetchall()
            total = sum((Decimal(row[0]) for row in rows), Decimal(0))
            if len(rows) >= self.max_calls or total + self.reserve_cny > self.max_cny:
                raise DomainError("PILOT_BUDGET", "Pilot attempt/estimated cost budget exhausted")
            cursor = db.execute(
                "INSERT INTO attempts(reserve) VALUES (?)", (str(self.reserve_cny),)
            )
            return cursor.lastrowid

    def settle(self, attempt_id, usage):
        if not isinstance(usage, dict):
            return
        counts = [usage.get("prompt_tokens"), usage.get("completion_tokens")]
        if not all(type(n) is int and n >= 0 for n in counts):
            return  # Unknown usage retains the entire reservation.
        # Conservative peak Flash tariff; deliberately ignore all cache discounts.
        cost = (Decimal(counts[0]) * 2 + Decimal(counts[1]) * 8) / 1_000_000
        serialized = json.dumps(usage, sort_keys=True)
        with sqlite3.connect(self.path) as db:
            if db.execute("SELECT 1 FROM attempts WHERE id=?", (attempt_id,)).fetchone() is None:
                raise DomainError("PILOT_BUDGET", "Unknown attempt settlement")
            previous = db.execute(
                "SELECT cost,usage FROM settlements WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            if previous:
                if previous != (str(cost), serialized):
                    raise DomainError("PILOT_BUDGET", "Attempt usage cannot be replaced")
                return
            db.execute(
                "INSERT INTO settlements VALUES (?,?,?)", (attempt_id, str(cost), serialized)
            )


class BoundedGateway:
    def __init__(self, inner, budget):
        self.inner, self.budget = inner, budget
        self.usage = []
        self.decisions = []

    async def request(self, phase, context):
        attempt_id = self.budget.reserve()  # BEFORE network; unknown responses remain reserved.
        reply = await self.inner.request(phase, context)
        self.usage.append(reply.usage)
        self.decisions.append({"phase": phase, "output": reply.output})
        self.budget.settle(attempt_id, reply.usage)
        return reply


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute", action="store_true", help="Enable paid calls (also requires .env opt-in)"
    )
    parser.add_argument("--max-calls", type=int, default=12)
    parser.add_argument("--max-cost-cny", type=Decimal, default=Decimal("2"))
    parser.add_argument("--batch", default=datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f"))
    parser.add_argument("--dataset", default="evals/data/deepseek-pilot-v1.json")
    parser.add_argument("--ledger-batch", help="Reuse an existing batch's cumulative budget ledger")
    return parser.parse_args()


async def main(args):
    if not 1 <= args.max_calls <= 60 or not Decimal("0") < args.max_cost_cny <= Decimal("10"):
        raise SystemExit("Pilot accepts 1..60 calls and an estimated budget >0 and <=10 CNY")
    for identifier in (args.batch, args.ledger_batch):
        if identifier is not None and (
            not identifier
            or any(
                c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
                for c in identifier
            )
        ):
            raise SystemExit("Batch must be a simple alphanumeric identifier")
    source = Path(args.dataset).resolve()
    if not source.is_relative_to(Path("evals/data").resolve()):
        raise SystemExit("Dataset must be inside this repository's evals/data")
    dataset = json.loads(source.read_text(encoding="utf-8"))
    cases = dataset["cases"]
    settings = Settings()
    # Peak, cache-MISS tariff verified 2026-10-27. Re-check before later runs.
    # UTF-8 bytes + margin are an estimate, not the provider's exact tokenizer.
    reserve = (
        Decimal(settings.model_max_input_bytes + 8192) * 2
        + Decimal(settings.model_max_output_tokens) * 8
    ) / 1_000_000
    plan = {
        "mode": "paid" if args.execute else "dry-run: NO API requests",
        "cases": [case["id"] for case in cases],
        "max_calls": args.max_calls,
        "estimated_cost_budget_cny": str(args.max_cost_cny),
        "conservative_reservation_per_attempt_cny": str(reserve),
        "tariff_cny_per_million": {"input_cache_miss_peak": 2, "output_peak": 8},
        "price_verified": "2026-10-27; re-check official pricing before executing",
        "limitations": "Local estimate, not a provider-side charge cap; excludes unrelated calls",
    }
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if not args.execute:
        return
    if settings.provider != "deepseek" or settings.model != "deepseek-flash":
        raise SystemExit(
            "This tariff/adapter pilot only supports provider=deepseek, model=deepseek-flash"
        )
    root = Path(".cache/evals").resolve()
    root.mkdir(parents=True, exist_ok=True)
    output = root / f"deepseek-{args.batch}.json"
    ledger = root / f"deepseek-{args.ledger_batch or args.batch}.sqlite"
    if output.exists() or (not args.ledger_batch and ledger.exists()):
        raise SystemExit(
            "Use a NEW batch ID. Existing evidence/attempt reservations are immutable."
        )
    if args.ledger_batch and not ledger.exists():
        raise SystemExit("Shared budget ledger must already exist")
    inner = CompatibleJsonGateway(settings)
    bounded = BoundedGateway(inner, PilotBudget(ledger, args.max_calls, args.max_cost_cny, reserve))
    results = []
    try:
        for case in cases:
            with tempfile.TemporaryDirectory(prefix="live-pilot-", dir=root) as directory:
                try:
                    result = await evaluate(
                        case,
                        Path(directory),
                        PolicyIndex(settings.policy_corpus),
                        gateway=bounded,
                        model_config={
                            "provider": settings.provider,
                            "model": settings.model,
                            "model_limit": 8,
                            "model_timeout_seconds": settings.model_timeout_seconds,
                        },
                    )
                except Exception as exc:
                    result = {
                        "case_id": case["id"],
                        "passed": False,
                        "infrastructure_error": type(exc).__name__,
                    }
                results.append(result)
    finally:
        await inner.aclose()
        report = {
            "plan": plan,
            "model": settings.model,
            "thinking": "disabled",
            "dataset_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "scope": "Synthetic development/integration cases, SQLite; "
            "not held-out LLM benchmark or MySQL load test",
            "cases": results,
            "usage": bounded.usage,
            "decisions": bounded.decisions,
            "ledger_batch": args.ledger_batch or args.batch,
            "unknown_usage": "Attempts without returned usage remain reserved in the SQLite ledger",
        }
        # Ignored local artifact; do not publish raw model/user traces automatically.
        with output.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print(f"Local report: {output}; completed={len(results)}")


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
