"""Frozen, paired Flash/Pro evaluation. Dry-run by default; no production DB access."""

import argparse
import asyncio
import hashlib
import json
import math
import os
import sqlite3
import statistics
import subprocess
from collections import defaultdict
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

TARIFF = {"deepseek-flash": (2, 8), "deepseek-v4-pro": (9, 27)}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class EvaluationBudget:
    def __init__(self, path, max_calls, cap_cny, carry_cny, cache_hit_rate=None):
        self.path = path
        self.cache_hit_rate = cache_hit_rate
        self.max_calls, self.cap, self.carry = (
            max_calls,
            Decimal(str(cap_cny)),
            Decimal(str(carry_cny)),
        )
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS attempts "
                "(id INTEGER PRIMARY KEY, model TEXT, reserve TEXT, settled TEXT, usage TEXT)"
            )
            db.execute("CREATE TABLE IF NOT EXISTS config (value TEXT NOT NULL)")
            configuration = {
                "max_calls": max_calls,
                "cap": str(self.cap),
                "carry": str(self.carry),
                "tariff": TARIFF,
            }
            if cache_hit_rate is not None:
                configuration["cache_hit_rate"] = str(cache_hit_rate)
            config = canonical(configuration)
            existing = db.execute("SELECT value FROM config").fetchone()
            if existing and existing[0] != config:
                raise ValueError("Existing evaluation budget configuration is immutable")
            if not existing:
                db.execute("INSERT INTO config VALUES (?)", (config,))

    def reserve(self, model, input_bytes, output_tokens):
        rates = TARIFF[model]
        reserve = (
            Decimal(input_bytes + 8192) * rates[0] + Decimal(output_tokens) * rates[1]
        ) / 1_000_000
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT COALESCE(settled,reserve) FROM attempts").fetchall()
            used = self.carry + sum((Decimal(r[0]) for r in rows), Decimal(0))
            if len(rows) >= self.max_calls or used + reserve > self.cap:
                raise DomainError("EVALUATION_BUDGET", "Authorized evaluation budget exhausted")
            cursor = db.execute(
                "INSERT INTO attempts(model,reserve) VALUES (?,?)", (model, str(reserve))
            )
            return cursor.lastrowid

    def settle(self, identifier, usage):
        if not isinstance(usage, dict):
            return
        counts = [usage.get("prompt_tokens"), usage.get("completion_tokens")]
        if not all(type(n) is int and n >= 0 for n in counts):
            return
        with sqlite3.connect(self.path) as db:
            model, old_cost, old_usage = db.execute(
                "SELECT model,settled,usage FROM attempts WHERE id=?", (identifier,)
            ).fetchone()
            rates = TARIFF[model]
            hit = usage.get("prompt_cache_hit_tokens", 0)
            hit = hit if type(hit) is int and 0 <= hit <= counts[0] else 0
            discount = (
                (Decimal(hit) * (Decimal(rates[0]) - Decimal(str(self.cache_hit_rate))))
                if self.cache_hit_rate is not None
                else 0
            )
            cost = str(
                (Decimal(counts[0]) * rates[0] + Decimal(counts[1]) * rates[1] - discount)
                / 1_000_000
            )
            serialized = canonical(usage)
            if old_cost is not None and (old_cost, old_usage) != (cost, serialized):
                raise ValueError("Cannot replace settled usage")
            db.execute(
                "UPDATE attempts SET settled=?,usage=? WHERE id=?", (cost, serialized, identifier)
            )

    def summary(self):
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT model,reserve,settled,usage FROM attempts").fetchall()
        by_model = defaultdict(
            lambda: {
                "calls": 0,
                "unknown_usage": 0,
                "estimated_cny": Decimal(0),
                "input_tokens": 0,
                "output_tokens": 0,
            }
        )
        for model, reserve, settled, usage in rows:
            item = by_model[model]
            item["calls"] += 1
            item["estimated_cny"] += Decimal(settled or reserve)
            item["unknown_usage"] += settled is None
            if usage:
                u = json.loads(usage)
                item["input_tokens"] += u["prompt_tokens"]
                item["output_tokens"] += u["completion_tokens"]
        total = self.carry + sum((v["estimated_cny"] for v in by_model.values()), Decimal(0))
        for item in by_model.values():
            item["estimated_cny"] = str(item["estimated_cny"])
        return {
            "by_model": dict(by_model),
            "carry_cny": str(self.carry),
            "estimated_total_with_carry_cny": str(total),
            "cap_cny": str(self.cap),
        }


class RecordedGateway:
    def __init__(self, inner, budget, path):
        self.inner, self.budget, self.path = inner, budget, path
        self.prepared = None

    def preflight(self, phase, context):
        payload = self.inner.build_payload(phase, context)
        encoded = canonical(payload).encode("utf-8")
        identifier = self.budget.reserve(
            self.inner.settings.model, len(encoded), payload["max_tokens"]
        )
        self.prepared = (hashlib.sha256(encoded).hexdigest(), identifier)

    async def request(self, phase, context):
        payload = self.inner.build_payload(phase, context)
        encoded = canonical(payload).encode("utf-8")
        fingerprint = hashlib.sha256(encoded).hexdigest()
        if self.prepared and self.prepared[0] == fingerprint:
            identifier = self.prepared[1]
            self.prepared = None
        else:
            identifier = self.budget.reserve(
                self.inner.settings.model, len(encoded), payload["max_tokens"]
            )
        record = {
            "attempt_id": identifier,
            "phase": phase,
            "configured_model": self.inner.settings.model,
            "payload_sha256": hashlib.sha256(encoded).hexdigest(),
        }
        try:
            reply = await self.inner.request(phase, context)
            record.update(
                output=reply.output,
                usage=reply.usage,
                provider_model=reply.provider_model,
                request_id=reply.request_id,
            )
            if hasattr(self.inner, "last_raw_output"):
                record["raw_output"] = self.inner.last_raw_output[:16000]
            self.budget.settle(identifier, reply.usage)
            return reply
        except Exception as exc:
            record["error_type"] = type(exc).__name__
            if isinstance(exc, json.JSONDecodeError):
                record["invalid_json"] = exc.doc[:16000]
                record["parse_error"] = exc.msg
            if getattr(exc, "usage", None):
                record["usage"] = exc.usage
                self.budget.settle(identifier, exc.usage)
            raise
        finally:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(canonical(record) + "\n")
                f.flush()
                os.fsync(f.fileno())


def wilson(successes, total):
    if not total:
        return None
    z = 1.959963984540054
    p = successes / total
    center = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return [center - half, center + half]


def summarize(results):
    groups = defaultdict(list)
    for r in results:
        groups[r["model"]].append(r)
    output = {}
    for model, rows in groups.items():
        passed = sum(r["passed"] for r in rows)
        cases = defaultdict(list)
        for r in rows:
            cases[r["case_id"]].append(r["passed"])
        paired = [values for values in cases.values() if len(values) == 2]
        latency = sorted(r["latency_ms"] for r in rows if "latency_ms" in r)
        output[model] = {
            "trials": len(rows),
            "passed": passed,
            "success_rate": passed / len(rows),
            "trial_wilson95_descriptive": wilson(passed, len(rows)),
            "paired_tasks": len(paired),
            "both_trials_success": sum(all(v) for v in paired),
            "empirical_two_trial_reliability": sum(all(v) for v in paired) / len(paired)
            if paired
            else None,
            "median_latency_ms": statistics.median(latency) if latency else None,
            "p95_latency_ms": latency[math.ceil(len(latency) * 0.95) - 1] if latency else None,
            "failed_cases": [
                {
                    "id": r["case_id"],
                    "trial": r["trial"],
                    "stop_reason": r.get("final_state", {}).get("stop_reason"),
                    "failed_checks": [k for k, v in r.get("checks", {}).items() if not v],
                    "infrastructure_error": r.get("infrastructure_error"),
                }
                for r in rows
                if not r["passed"]
            ],
        }
    return output


def source_manifest(dataset):
    files = sorted(
        set(Path("src/shopresolve").rglob("*.py"))
        | {
            Path("scripts/evaluate_models.py"),
            Path("scripts/evaluate_workflows.py"),
            Path("data/policies/demo.json"),
            dataset,
        }
    )
    return {str(p).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


async def main(args):
    source = Path("evals/data/commerce-evaluation-v2.json")
    dataset = json.loads(source.read_text(encoding="utf-8"))
    plan = {
        "dataset": dataset["dataset_version"],
        "cases": len(dataset["cases"]),
        "trials_per_case": 2,
        "models": list(TARIFF),
        "thinking": "disabled",
        "model_limit": 8,
        "active_budget_ms": 120000,
        "request_timeout_seconds": 30,
        "retrieval": "bm25",
        "max_total_cny_including_prior": 5,
        "carry_cny": "0.567894",
        "source_sha256": source_manifest(source),
        "protocol": "same model-visible inputs/tools/scoring; interleaved model order; "
        "independent SQLite per trial",
    }
    print(canonical({k: v for k, v in plan.items() if k != "source_sha256"}), flush=True)
    if not args.execute and not args.demo:
        return
    root = Path(".cache/evals") / args.batch
    root.mkdir(parents=True, exist_ok=False)
    (root / "plan.json").write_text(canonical(plan), encoding="utf-8")
    budget = EvaluationBudget(root / "budget.sqlite", 14 * 2 * 2 * 8, 5, plan["carry_cny"])
    results = []
    models = ["demo"] if args.demo else list(TARIFF)
    clients = {}
    if not args.demo:
        for model in models:
            settings = Settings(
                provider="deepseek",
                model=model,
                model_base_url="https://api.deepseek.com",
                allow_paid_model=True,
                model_timeout_seconds=30,
            )
            clients[model] = CompatibleJsonGateway(settings)
    try:
        for trial in range(2):
            for index, case in enumerate(dataset["cases"]):
                ordered = models if (index + trial) % 2 == 0 else list(reversed(models))
                for model in ordered:
                    if source_manifest(source) != plan["source_sha256"]:
                        raise RuntimeError("Frozen source changed during evaluation")
                    folder = root / f"{model}-{case['id']}-{trial}"
                    folder.mkdir()
                    gateway = (
                        RecordedGateway(clients[model], budget, folder / "decisions.jsonl")
                        if model != "demo"
                        else None
                    )
                    try:
                        result = await evaluate(
                            case,
                            folder,
                            PolicyIndex("data/policies/demo.json"),
                            gateway=gateway,
                            model_config={
                                "provider": "demo" if model == "demo" else "deepseek",
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
                    (root / "results.json").write_text(
                        canonical(
                            {
                                "plan": plan,
                                "results": results,
                                "summary": summarize(results),
                                "budget": budget.summary(),
                            }
                        ),
                        encoding="utf-8",
                    )
                    if result.get("final_state", {}).get("stop_reason") == "EVALUATION_BUDGET":
                        return
    finally:
        for client in clients.values():
            await client.aclose()
        (root / "results.json").write_text(
            canonical(
                {
                    "plan": plan,
                    "results": results,
                    "summary": summarize(results),
                    "budget": budget.summary(),
                    "git_base": subprocess.check_output(
                        ["git", "rev-parse", "HEAD"], text=True
                    ).strip(),
                    "generated_at": datetime.now(UTC).isoformat(),
                }
            ),
            encoding="utf-8",
        )
        print("Local report:", root / "results.json", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--batch", default="formal-20261028-v2")
    options = parser.parse_args()
    if not options.batch or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        for c in options.batch
    ):
        raise SystemExit("Invalid batch identifier")
    if options.demo and options.execute:
        raise SystemExit("Choose demo OR paid execution")
    asyncio.run(main(options))
