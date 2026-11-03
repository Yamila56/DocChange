"""Export existing public evaluation evidence, without model calls."""

import argparse
import collections
import csv
import hashlib
import importlib.metadata
import json
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".cache/upstream/dogbench"))
from dogbench import judge  # noqa: E402
from dogbench.reporting import build_report  # noqa: E402

STAGES = (
    "dogbench-20261102",
    "dogbench-recovery-20261102",
    "dogbench-recovery2-20261102",
    "dogbench-final-20261102",
)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def csv_file(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def budget_summary(folder):
    with sqlite3.connect((folder / "budget.sqlite").as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute("SELECT reserve,settled,usage FROM attempts").fetchall()
    upper = Decimal(0)
    cache_upper = Decimal(0)
    prompt = output = hits = unknown = 0
    for reserve, settled, raw in rows:
        upper += Decimal(settled or reserve)
        if not raw:
            unknown += 1
            cache_upper += Decimal(reserve)
            continue
        usage = json.loads(raw)
        count = usage["prompt_tokens"]
        hit = usage.get("prompt_cache_hit_tokens", 0)
        miss = usage.get("prompt_cache_miss_tokens", count - hit)
        if hit + miss != count:
            hit, miss = 0, count
        prompt += count
        hits += hit
        output += usage["completion_tokens"]
        cache_upper += (
            Decimal(hit) * Decimal("0.04") + Decimal(miss) * 2 + usage["completion_tokens"] * 8
        ) / 1_000_000
    return {
        "requests": len(rows),
        "unknown_usage": unknown,
        "known_prompt_tokens": prompt,
        "known_completion_tokens": output,
        "known_cache_hit_tokens": hits,
        "no_cache_peak_upper_cny": str(upper),
        "cache_aware_peak_upper_cny": str(cache_upper),
    }


def main(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    predictions = records(args.predictions)
    outcomes = {
        r["instance_id"]: r
        for r in records(ROOT / ".cache/benchmarks/dogbench-data/outcomes.jsonl")
    }
    items = {
        r["instance_id"]: r for r in records(ROOT / ".cache/benchmarks/dogbench-data/items.jsonl")
    }
    raw = read(args.scores)
    scores = {r["instance_id"]: r for r in raw["scores"]}
    assert len(predictions) == len(scores) == len(items) == 175
    assert set(scores) == set(items) == {p["instance_id"] for p in predictions}
    predictions.sort(key=lambda p: p["instance_id"])
    for prediction in predictions:
        key = prediction["instance_id"]
        score = scores[key]
        patch = prediction.get("patch", "")
        assert score["patch_sha256"] == hashlib.sha256(patch.encode()).hexdigest()
        assert score["status"] == prediction["status"]
        assert score["decision"] == prediction.get("decision")
        if "judgment" in score:
            rubric = outcomes[key]["rubric_markdown"]
            assert score["rubric_sha256"] == hashlib.sha256(rubric.encode()).hexdigest()
            blocks = judge.criterion_blocks(rubric)
            judge.validate_score(score["judgment"], blocks, patch)
            numeric = judge.numeric_score(score["judgment"], blocks)
            assert all(score[k] == v for k, v in numeric.items())
    report = build_report([scores[p["instance_id"]] for p in predictions], expected_ids=list(items))
    assert report["summary"]["complete"]
    report["judge_configuration"] = raw["judge_configuration"]
    report["protocol"] = "DocChange API-only adaptation; not the unmodified Docker CLI leaderboard"
    dump(output / "scores.json", report)
    (output / "predictions.jsonl").write_text(
        "".join(json.dumps(p, ensure_ascii=False) + "\n" for p in predictions), encoding="utf-8"
    )
    dump(output / "generation-plan.json", read(ROOT / ".cache/evals/dogbench-20261102/plan.json"))
    patch_audit = read(ROOT / ".cache/evals/dogbench-patch-audit-20261102.json")
    dump(output / "patch-audit.json", patch_audit)
    audit_by_id = {r["instance_id"]: r for r in patch_audit["results"]}
    calls, task_calls, reservations = [], collections.Counter(), collections.Counter()
    budget = {}
    for name in STAGES:
        folder = ROOT / ".cache/evals" / name
        budget[name] = budget_summary(folder)
        for path in (folder / "tasks").glob("*/model-calls.jsonl"):
            key = path.parent.name
            for call in records(path):
                task_calls[key] += 1
                calls.append(
                    {
                        "instance_id": key,
                        "stage": name,
                        **{
                            k: call.get(k)
                            for k in (
                                "attempt_id",
                                "configured_model",
                                "provider_model",
                                "payload_sha256",
                                "usage",
                                "error_type",
                            )
                        },
                    }
                )
        for path in (folder / "tasks").glob("*/runs.sqlite"):
            try:
                with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
                    row = db.execute("SELECT model_calls FROM runs LIMIT 1").fetchone()
                if row:
                    reservations[path.parent.name] += row[0]
            except sqlite3.OperationalError:
                pass
    assert max(task_calls.values()) <= 16
    assert len(task_calls) == 175
    task_rows, patch_rows = [], []
    for prediction in predictions:
        key = prediction["instance_id"]
        row = scores[key]
        task_rows.append(
            {
                "instance_id": key,
                "source_url": items[key]["source_url"],
                "expected_outcome": row["expected_outcome"],
                "status": row["status"],
                "decision": row["decision"],
                "decision_correct": row["decision_correct"],
                "score": row["overall"],
                "blocking_p0": ";".join(row["blocking_p0"]),
                "api_requests": task_calls[key],
                "reserved_model_turns": reservations[key],
                "error": prediction.get("error", ""),
            }
        )
        if prediction.get("decision") == "patch":
            patch_path = output / "patches" / f"{key}.patch"
            patch_path.parent.mkdir(parents=True, exist_ok=True)
            # Keep the exact bytes bound to the judged prediction on Windows too.
            patch_path.write_bytes(prediction["patch"].encode("utf-8"))
            patch_rows.append(
                {
                    "instance_id": key,
                    "patch_file": f"patches/{key}.patch",
                    "change_was_needed": row["expected_outcome"] == "patch",
                    "score": row["overall"],
                    "no_blocking_p0": bool(row["mergeable"]) if row["decision_correct"] else False,
                    "apply_check_passed": audit_by_id[key]["apply_check_passed"],
                    "changed_paths": ";".join(audit_by_id[key]["changed_paths"]),
                    "judge_summary": row.get("judgment", {}).get(
                        "summary", "No change was needed; official decision score is zero"
                    ),
                }
            )
    csv_file(output / "tasks.csv", task_rows)
    csv_file(output / "patch-assessments.csv", patch_rows)
    (output / "requests.jsonl").write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in calls), encoding="utf-8"
    )
    judged_patches = [s for s in scores.values() if "judgment" in s]
    incorrect_patches = [p for p in patch_rows if not p["change_was_needed"]]
    totals = {
        "api_requests": sum(v["requests"] for v in budget.values()),
        "unknown_usage": sum(v["unknown_usage"] for v in budget.values()),
        "no_cache_peak_upper_cny": str(
            sum(Decimal(v["no_cache_peak_upper_cny"]) for v in budget.values())
        ),
        "cache_aware_peak_upper_cny": str(
            sum(Decimal(v["cache_aware_peak_upper_cny"]) for v in budget.values())
        ),
    }
    summary = {
        "date": "2026-11-02",
        "evaluated_source_commit": "7682fb9",
        "upstream_commit": "35bc80734093ffc3d0801476b02ceb2ab0c80bd2",
        "model": "deepseek-flash",
        "judge_model": "gpt-5.6-terra",
        "official_numeric_report": report["summary"],
        "generation": {
            "tasks": 175,
            "completed": 113,
            "patches": len(patch_rows),
            "abstentions": 40,
            "turn_budget_failures": 62,
            "all_tasks_have_api_requests": True,
            "maximum_actual_api_requests_per_task": max(task_calls.values()),
            "reserved_model_turns_total": sum(reservations.values()),
            "budget_preflight_reservations_without_api_request": sum(reservations.values())
            - sum(task_calls.values()),
        },
        "patch_quality": {
            "patches": len(patch_rows),
            "apply_check_passed": patch_audit["apply_check_passed"],
            "change_was_needed": len(judged_patches),
            "unnecessary_patches": len(incorrect_patches),
            "needed_patches_with_positive_score": sum(s["overall"] > 0 for s in judged_patches),
            "needed_patches_with_zero_score": sum(s["overall"] == 0 for s in judged_patches),
            "needed_patches_with_full_rubric_score": sum(
                s["overall"] == 100 for s in judged_patches
            ),
            "needed_patches_without_blocking_p0": sum(s["mergeable"] for s in judged_patches),
            "mergeable_means_no_p0_only": True,
        },
        "budget_stages": budget,
        "budget_totals": totals,
        "interface_pilot_separate": budget_summary(
            ROOT / ".cache/evals/dogbench-interface-20261102"
        ),
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("numpy", "rank-bm25", "pytrec-eval-terrier")
        },
        "metric_notes": [
            "Recovered cohort, not first-attempt success; prior decisions were not regenerated",
            "Logical turn cap includes preflight reservations rejected before an API request",
            "Official mergeable flag only means no blocking P0, not human acceptance",
            "One rubric-100 patch is a typo-only correction; inspect case analysis",
            "Cost figures are peak-price estimates with unknown-usage reservations, not invoices",
        ],
    }
    if args.prepared:
        preparation = []
        for key in sorted(items):
            row = read(args.prepared / f"{key}.json")
            attestation_path = Path(row["attestation"])
            attestation = read(args.prepared / "sealed/attestations" / attestation_path.name)
            preparation.append(
                {
                    "instance_id": key,
                    "logical_input_sha256": row["logical_input_sha256"],
                    "strict_context_eligible": row.get("strict_context_eligible", True),
                    "context_findings": row.get("context_findings", []),
                    "api_only_symlink_compatibility": row.get(
                        "api_only_symlink_compatibility", False
                    ),
                    "reference_symlinks": row.get("reference_symlinks", []),
                    "repository_alias_binding": [
                        c["repository_alias_binding"]
                        for c in attestation.get("source_attestation", {}).get("components", [])
                        if "repository_alias_binding" in c
                    ],
                }
            )
        dump(output / "preparation.json", preparation)
        clean = [p["instance_id"] for p in preparation if p["strict_context_eligible"]]
        flagged = [p["instance_id"] for p in preparation if not p["strict_context_eligible"]]
        summary["context_audit_sensitivity"] = {
            name: build_report([scores[key] for key in ids], expected_ids=ids)["summary"]
            for name, ids in (
                ("original_context_check_passed", clean),
                ("original_context_check_flagged", flagged),
            )
        }
        summary["preparation_compatibility"] = {
            "prepared": len(preparation),
            "original_context_check_passed": len(clean),
            "original_context_check_flagged": len(flagged),
            "symlink_compatibility_workspaces": sum(
                p["api_only_symlink_compatibility"] for p in preparation
            ),
            "repository_alias_bindings": sum(
                len(p["repository_alias_binding"]) for p in preparation
            ),
        }
    summary["generation"]["failed_tasks_with_fewer_than_16_actual_requests"] = sum(
        r["status"] != "completed" and r["api_requests"] < 16 for r in task_rows
    )
    dump(output / "summary.json", summary)
    ir_rows = []
    for name in ("scifact", "scidocs"):
        result = read(ROOT / f"docs/evaluation/public-ir-2026-11-02/{name}.json")
        for metric, value in result["metrics"].items():
            ir_rows.append(
                {"dataset": name, "metric": metric, "value": value, "percentage": value * 100}
            )
    csv_file(output / "ir-metrics.csv", ir_rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--predictions",
        type=Path,
        default=ROOT / ".cache/evals/dogbench-final-20261102/predictions.jsonl",
    )
    parser.add_argument("--scores", type=Path, default=ROOT / ".cache/judge-dogbench/report.json")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/evaluation/dogbench-2026-11-02")
    parser.add_argument("--prepared", type=Path)
    main(parser.parse_args())
