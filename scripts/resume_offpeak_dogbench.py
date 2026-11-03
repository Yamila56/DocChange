"""Continue the frozen candidate using corrected off-peak billing and prepaid funds.

The old peak-price ledger stays immutable. Its exact records are copied, not reset.
The copied ledger retains peak-equivalent units internally; displayed CNY is half.
Requests are allowed only when their complete timeout window is outside peak hours.
"""

import argparse
import asyncio
import hashlib
import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx

from scripts.evaluate_models import EvaluationBudget, canonical
from shopresolve.documents.model_profile import pro_settings
from shopresolve.documents.repository import RepositoryGateway
from shopresolve.errors import DomainError

BEIJING = timezone(timedelta(hours=8))


def is_offpeak(timestamp):
    local = datetime.fromtimestamp(timestamp, BEIJING)
    # Treat weekday holidays as peak during office hours conservatively; nights
    # and weekends require no external holiday calendar and are always off-peak.
    return local.weekday() >= 5 or not (9 <= local.hour < 12 or 14 <= local.hour < 18)


class OffpeakBudget(EvaluationBudget):
    def reserve(self, model, input_bytes, output_tokens):
        now = time.time()
        if not all(is_offpeak(now + offset) for offset in (0, 60, 120)):
            raise DomainError("EVALUATION_BUDGET", "Off-peak-only billing window has ended")
        return super().reserve(model, input_bytes, output_tokens)

    def summary(self):
        summary = super().summary()
        for row in summary["by_model"].values():
            row["estimated_cny"] = str(Decimal(row["estimated_cny"]) / 2)
        for key in ("carry_cny", "estimated_total_with_carry_cny", "cap_cny"):
            summary[key] = str(Decimal(summary[key]) / 2)
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT reserve,settled FROM attempts").fetchall()
        summary["known_usage_cost_cny"] = str(
            sum((Decimal(s) / 2 for _, s in rows if s is not None), Decimal(0))
        )
        summary["unknown_usage_reserve_cny"] = str(
            sum((Decimal(r) / 2 for r, s in rows if s is None), Decimal(0))
        )
        summary["pricing"] = "Official off-peak half-price; not an invoice"
        summary["ledger_unit"] = "Internal peak-equivalent CNY; displayed CNY divided by two"
        return summary


class UsageCapturingGateway(RepositoryGateway):
    """Capture usage even when the unchanged JSON/finish validator rejects a reply."""

    def __init__(self, settings, **kwargs):
        super().__init__(settings, **kwargs)
        self.billing_usage = None

        async def capture(response):
            await response.aread()
            try:
                body = response.json()
            except ValueError:
                return
            if isinstance(body, dict) and isinstance(body.get("usage"), dict):
                self.billing_usage = body["usage"]

        self.client.event_hooks["response"].append(capture)

    async def request(self, phase, context):
        self.billing_usage = None
        try:
            return await super().request(phase, context)
        except Exception as exc:
            if self.billing_usage is not None:
                exc.usage = self.billing_usage
            raise


async def account_preflight():
    settings = pro_settings()
    async with httpx.AsyncClient(trust_env=False, timeout=20) as client:
        response = await client.get(
            settings.model_base_url + "/user/balance",
            headers={"Authorization": "Bearer " + settings.model_api_key},
        )
        response.raise_for_status()
        account = response.json()
    amount = sum(
        (
            Decimal(r["total_balance"])
            for r in account.get("balance_infos", [])
            if r["currency"] == "CNY"
        ),
        Decimal(0),
    )
    if not account.get("is_available") or amount < 1:
        raise RuntimeError("Provider prepaid funds are unavailable; no completion sent")
    return amount


def main(args):
    from scripts import evaluate_dogbench as evaluation

    batch = args.batch.resolve()
    plan = json.loads((batch / "plan.json").read_text(encoding="utf-8"))
    repair = json.loads((batch / "source-repair-amendment.json").read_text(encoding="utf-8"))
    for name, before in plan["source_sha256"].items():
        actual = hashlib.sha256((evaluation.ROOT / name).read_bytes()).hexdigest()
        allowed = repair["files"].get(name, {})
        if actual != before and not (
            allowed.get("before_sha256") == before and allowed.get("after_sha256") == actual
        ):
            raise ValueError("Frozen candidate/config source changed: " + name)
    if not is_offpeak(time.time()):
        raise RuntimeError("This corrected continuation only runs in official off-peak hours")
    source, target = batch / "budget.sqlite", batch / "budget-offpeak.sqlite"
    correction = batch / "billing-correction.json"
    if not target.exists():
        # All original attempts lie in this verified night-time envelope.
        starts = []
        for database in (batch / "tasks").glob("*/runs.sqlite"):
            with sqlite3.connect(database) as db:
                starts.extend(t for (t,) in db.execute("SELECT created_at FROM runs"))
        logs = list((batch / "tasks").glob("*/model-calls.jsonl"))
        first, last = min(starts), max(f.stat().st_mtime for f in logs)
        if not all(is_offpeak(t) for t in range(int(first), int(last) + 1, 60)):
            raise ValueError("Legacy request envelope is not entirely off-peak")
        balance = asyncio.run(account_preflight())
        funded_cap = (balance - Decimal("0.5")).to_integral_value(rounding="ROUND_FLOOR")
        before_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        with sqlite3.connect(source) as old, sqlite3.connect(target) as new:
            old.backup(new)
            rows = old.execute("SELECT reserve,settled FROM attempts").fetchall()
            old_peak = sum((Decimal(s or r) for r, s in rows), Decimal(0))
            total_peak_cap = old_peak + funded_cap * 2
            configuration = json.loads(new.execute("SELECT value FROM config").fetchone()[0])
            configuration["cap"] = str(total_peak_cap)
            new.execute("UPDATE config SET value=?", (canonical(configuration),))
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before_sha
        evaluation.atomic(
            correction,
            {
                "source_ledger_sha256": before_sha,
                "legacy_attempts_retained": len(rows),
                "legacy_request_window_utc": [
                    datetime.fromtimestamp(t, UTC).isoformat() for t in (first, last)
                ],
                "official_pricing": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
                "legacy_peak_estimate_cny": str(old_peak),
                "legacy_corrected_upper_cny": str(old_peak / 2),
                "additional_prepaid_funded_cap_cny": str(funded_cap),
                "effective_total_upper_cap_cny": str(total_peak_cap / 2),
                "authorization": "User approved prepaid balance use until alert; no recharge",
                "candidate_payload_and_turn_limits_unchanged": True,
                "historical_settled_records_rewritten": False,
            },
        )
    info = json.loads(correction.read_text(encoding="utf-8"))
    peak_cap = Decimal(info["effective_total_upper_cap_cny"]) * 2

    class RedirectedBudget(OffpeakBudget):
        def __init__(self, path, max_calls, cap_cny, carry_cny, cache_hit_rate=None):
            if path.resolve() != source:
                raise ValueError("Unexpected budget path")
            super().__init__(target, max_calls, peak_cap, carry_cny, cache_hit_rate)

    evaluation.EvaluationBudget = RedirectedBudget
    evaluation.RepositoryGateway = UsageCapturingGateway
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
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--cuda-dll-dir", type=Path)
    main(parser.parse_args())
