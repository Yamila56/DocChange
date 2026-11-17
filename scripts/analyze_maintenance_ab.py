"""Offline paired analysis of frozen maintenance runs. Never calls a candidate model.

Failure stages are observable; semantic root causes often remain uncertain.
Diagnostic draft judgments are separate from final official predictions/scores.
"""

import argparse
import csv
import json
import random
import sqlite3
from collections import Counter
from pathlib import Path
from statistics import fmean, median

OPERATIONAL_TOOLS = {"list", "search", "read", "read_many", "propose"}
TAXONOMY = {
    "A": "Planning",
    "B": "Discovery",
    "C": "Coverage",
    "D": "Editing",
    "E": "Verification",
    "F": "Repair",
    "G": "Protocol/Runtime",
}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )


def paired_counts(first, second):
    if first.keys() != second.keys():
        raise ValueError("Paired cohorts must have exactly matching task IDs")
    counts = Counter(
        (bool(first[k]["decision_correct"]), bool(second[k]["decision_correct"])) for k in first
    )
    return {
        "both_correct": counts[True, True],
        "old_only_correct": counts[True, False],
        "new_only_correct": counts[False, True],
        "both_wrong": counts[False, False],
    }


def task_measurements(batch, key, score):
    folder = batch / "tasks" / key
    result = read(folder / "result.json")
    state, model_calls, tools, observed, read_paths = {}, 0, Counter(), set(), set()
    last_output = {}
    database = folder / "runs.sqlite"
    if database.exists():
        # Read-only connection; analysis must never amend an experiment ledger/checkpoint.
        with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
            rows = db.execute("SELECT state,model_calls FROM runs").fetchall()
            if len(rows) > 1:
                raise ValueError("Multiple runs require an explicit attempt binding")
            if rows:
                state, model_calls = json.loads(rows[0][0]), rows[0][1]
            if db.execute("SELECT name FROM sqlite_master WHERE name='model_steps'").fetchone():
                saved = db.execute(
                    "SELECT response FROM model_steps WHERE response IS NOT NULL "
                    "ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                last_output = json.loads(saved[0]) if saved else {}
            for name, encoded in db.execute("SELECT name,result FROM tool_invocations"):
                if name in OPERATIONAL_TOOLS:
                    tools[name] += 1
                if not encoded:
                    continue
                reply = json.loads(encoded)
                items = (
                    reply.get("files", [])
                    if name == "read_many"
                    else (reply.get("results", []) if name == "search" else [reply])
                )
                for item in items:
                    if not isinstance(item, dict) or not item.get("evidence_id"):
                        continue
                    if item.get("root") == "docs":
                        observed.add(item["path"])
                        if name in {"read", "read_many"}:
                            read_paths.add(item["path"])
    receipts = folder / "model-calls.jsonl"
    calls = (
        [json.loads(line) for line in receipts.read_text(encoding="utf-8").splitlines() if line]
        if receipts.exists()
        else []
    )
    data = state.get("maintenance", {})
    candidate = data.get("candidate") or state.get("repository_candidate", {})
    review = data.get("review") or state.get("repository_review", {})
    return {
        **score,
        "model_decisions": model_calls,
        "api_receipt_records": len(calls),
        "operational_tool_calls": sum(tools.values()),
        "tool_counts": dict(tools),
        "observed_doc_paths": sorted(observed),
        "read_doc_paths": sorted(read_paths),
        "execution_error_type": result.get("error_type"),
        "execution_error": result.get("error", ""),
        "phase": data.get("phase", state.get("repository_policy_phase")),
        "repair_rounds": data.get("repairs", state.get("repository_repairs", 0)),
        "plan": data.get("tasks", state.get("repository_requirements", [])),
        "impact": data.get("impact"),
        "discovery": data.get("discovery", {}),
        "candidate": candidate,
        "review": review,
        "protocol_retries": data.get("protocol_retries", {}),
        "discovery_elapsed_ms": data.get("discovery_elapsed_ms"),
        "tool_metrics": data.get("tool_metrics", {}),
        "last_model_output": last_output,
        "latency_ms": result.get("latency_ms"),
    }


def metrics(rows):
    if not rows:
        raise ValueError("Cannot summarize an empty cohort")
    patches = [r for r in rows if r["expected_outcome"] == "patch"]
    no_changes = [r for r in rows if r["expected_outcome"] == "abstain"]
    n, calls = len(rows), sum(r["model_decisions"] for r in rows)
    latencies = [r["latency_ms"] for r in rows if r.get("latency_ms") is not None]
    return {
        "tasks": n,
        "patch_needed": len(patches),
        "no_change": len(no_changes),
        "completion_rate": sum(r["status"] == "completed" for r in rows) / n,
        "execution_failures": sum(r["status"] != "completed" for r in rows),
        "decision_accuracy": sum(r["decision_correct"] for r in rows) / n,
        "patch_recall": fmean(r["decision_correct"] for r in patches) if patches else None,
        "no_change_recall": fmean(r["decision_correct"] for r in no_changes)
        if no_changes
        else None,
        "documentation_quality": fmean(r["overall"] for r in patches) if patches else None,
        "p0_clean_delivery_rate": fmean(r["decision_correct"] and r["mergeable"] for r in patches)
        if patches
        else None,
        "mean_model_decisions": fmean(r["model_decisions"] for r in rows),
        "median_model_decisions": median(r["model_decisions"] for r in rows),
        "mean_operational_tool_calls": fmean(r["operational_tool_calls"] for r in rows),
        "total_model_decisions": calls,
        "mean_task_wall_ms": fmean(latencies) if latencies else None,
        "median_task_wall_ms": median(latencies) if latencies else None,
        "total_api_receipt_records": sum(r["api_receipt_records"] for r in rows),
        "total_protocol_corrections": sum(
            sum(r.get("protocol_retries", {}).values()) for r in rows
        ),
        "correct_decisions_per_100_model_decisions": 100
        * sum(r["decision_correct"] for r in rows)
        / calls
        if calls
        else None,
        "completed_decisions_per_100_model_decisions": 100
        * sum(r["status"] == "completed" for r in rows)
        / calls
        if calls
        else None,
    }


def failure_classification(row, diagnostic=None):
    """Classify visible failure location; do not invent semantic causal certainty."""
    error = row["execution_error"]
    status, phase = row["status"], row["phase"]
    primary, secondary, reason = None, [], "Direction correct; no blocking P0"
    if status != "completed":
        primary, reason = (
            "G",
            "Runtime/protocol/budget failure: "
            + (error or row["execution_error_type"] or "unknown"),
        )
        if (
            "Structured review remains unresolved" in error
            or "Coverage review failed after repair" in error
        ):
            primary, reason = "F", "Review remained unresolved after the allowed repair"
        elif "Repair may only" in error:
            primary, reason = "F", "Repair exceeded the permitted requirement/path scope"
        elif "INVALID_PLAN" in error or (phase == "PLAN" and "dependency" in error.lower()):
            primary, reason = (
                "A",
                "Executable plan rejected; semantic planning quality not established",
            )
        elif phase == "EDIT" and any(
            s in error
            for s in (
                "Insufficient evidence",
                "Missing evidence is unresolved",
                "requires repository evidence",
                "Coverage cites unknown",
                "Assess every planned",
                "CoverageCell",
                "known source references",
                "observed path",
                "needs a coverage cell",
                "No-change needs definite impact",
            )
        ):
            primary, reason = "C", "Editor could not produce a source-backed coverage decision"
            if any(v.get("status") == "unresolved" for v in row["discovery"].values()):
                secondary.append("B")
    elif not row["decision_correct"]:
        primary, reason = "C", "Final edit/no-change direction is wrong under the official outcome"
    elif row["expected_outcome"] == "patch" and row["overall"] < 100:
        primary, reason = (
            "D",
            "Delivered patch has documented quality gaps under the official rubric",
        )
    if row["blocking_p0"] and row["review"].get("ready"):
        secondary.append("E")
    if (
        diagnostic
        and not row["review"].get("ready")
        and diagnostic.get("mergeable")
        and diagnostic.get("overall") == 100
    ):
        secondary.append("E")
    return {
        "primary": primary,
        "secondary": sorted(set(secondary)),
        "observed_reason": reason[:2000],
        "interpretation": "Observable stage/quality gap; semantic root cause may be ambiguous",
        "false_rejection_proven": False,
        "diagnostic_candidate_quality": diagnostic.get("overall") if diagnostic else None,
        "diagnostic_candidate_p0_clean": diagnostic.get("mergeable") if diagnostic else None,
    }


def paired_bootstrap(first, second, seed=20261117, samples=5000):
    keys = sorted(first)
    if set(keys) != set(second):
        raise ValueError("Bootstrap requires identical tasks")
    rng = random.Random(seed)
    diffs = [
        float(second[k]["decision_correct"]) - float(first[k]["decision_correct"]) for k in keys
    ]
    draws = sorted(fmean(rng.choices(diffs, k=len(diffs))) for _ in range(samples))
    return {
        "metric": "decision_accuracy_delta",
        "mean": fmean(diffs),
        "percentile_95_ci": [draws[int(samples * 0.025)], draws[int(samples * 0.975)]],
        "seed": seed,
        "resamples": samples,
        "unit": "paired task",
        "causal_interpretation": False,
    }


def load_cohort(batch, judgments):
    plan, report = read(batch / "plan.json"), read(judgments / "report.json")
    by_id = {row["instance_id"]: row for row in report["scores"]}
    if len(by_id) != len(report["scores"]) or set(by_id) != set(plan["items"]):
        raise ValueError("All frozen cohort items require unique final judgments")
    if len(plan["items"]) != 49:
        raise ValueError("This experiment is fixed to the original 49 tasks")
    return {key: task_measurements(batch, key, by_id[key]) for key in plan["items"]}


def prepare_drafts(batch, output):
    """Export non-final failed candidates only for diagnostic independent scoring."""
    predictions = []
    for key in read(batch / "plan.json")["items"]:
        folder = batch / "tasks" / key
        if not (folder / "result.json").exists():
            continue
        result = read(folder / "result.json")
        if result["status"] == "completed":
            continue
        database = folder / "runs.sqlite"
        if not database.exists():
            continue
        with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
            rows = db.execute("SELECT state FROM runs").fetchall()
        if len(rows) != 1:
            continue
        state = json.loads(rows[0][0])
        candidate = state.get("maintenance", {}).get("candidate", {})
        if candidate.get("patch"):
            predictions.append(
                {
                    "instance_id": key,
                    "status": "completed",
                    "decision": "patch",
                    "patch": candidate["patch"],
                }
            )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(p, ensure_ascii=False) + "\n" for p in predictions), encoding="utf-8"
    )
    print(
        json.dumps({"diagnostic_nonfinal_candidates": len(predictions), "promoted_to_final": False})
    )


def main(args):
    if args.prepare_drafts:
        prepare_drafts(args.new_batch, args.output / "diagnostic-drafts.jsonl")
        return
    first = load_cohort(args.old_batch, args.old_judgments)
    second = load_cohort(args.new_batch, args.new_judgments)
    pairs = paired_counts(first, second)
    diagnostics = {}
    if args.diagnostic_judgments and (args.diagnostic_judgments / "report.json").exists():
        diagnostics = {
            r["instance_id"]: r for r in read(args.diagnostic_judgments / "report.json")["scores"]
        }
    classified = {
        key: failure_classification(row, diagnostics.get(key)) for key, row in second.items()
    }
    # File-path labels are used only in this offline analyzer, never candidate context.
    reference = {}
    if args.reference_items:
        reference = {
            r["instance_id"]: r.get("docs", {}).get("paths", [])
            for r in (
                json.loads(line)
                for line in args.reference_items.read_text(encoding="utf-8").splitlines()
                if line
            )
        }
    for key, assessment in classified.items():
        paths = reference.get(key, [])
        if isinstance(paths, list) and all(isinstance(p, str) for p in paths):
            assessment["human_changed_paths_not_observed"] = sorted(
                set(paths) - set(second[key]["observed_doc_paths"])
            )
            assessment["path_diagnostic_note"] = (
                "Proxy only: new paths cannot be read; a path miss cannot distinguish A from B"
            )
    result = {
        "cohort": 49,
        "old": metrics(list(first.values())),
        "new": metrics(list(second.values())),
        "paired": pairs,
        "paired_by_outcome": {
            outcome: paired_counts(
                {k: r for k, r in first.items() if r["expected_outcome"] == outcome},
                {k: r for k, r in second.items() if r["expected_outcome"] == outcome},
            )
            for outcome in ("patch", "abstain")
        },
        "decision_accuracy_ci": paired_bootstrap(first, second),
        "taxonomy": TAXONOMY,
        "primary_failure_counts": dict(
            Counter(r["primary"] for r in classified.values() if r["primary"])
        ),
        "secondary_failure_counts": dict(
            Counter(c for r in classified.values() for c in r["secondary"])
        ),
        "definitions": {
            "completion": "Final output returned, including wrong decisions; not task correctness",
            "documentation_quality": (
                "Official patch-needed quality including execution/wrong-direction zeros"
            ),
            "model_decisions": (
                "Persisted Run.model_calls, includes interrupted charged reservations; "
                "API receipts reported separately"
            ),
            "operational_tool_calls": (
                "Unique persisted list/search/read/read_many/propose invocations; "
                "excludes plan/review/abstain role actions"
            ),
            "correct_decisions": (
                "Correct edit/no-change direction, not full patch semantic correctness"
            ),
            "taxonomy": (
                "Primary observable failure stage plus secondary checks; "
                "A/B semantic attribution may remain unresolved"
            ),
        },
        "historical_baseline": True,
        "new_official_175_composite": None,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    dump(args.output / "paired-analysis.json", result)
    dump(args.output / "failure-taxonomy.json", classified)
    records = []
    for key in first:
        a, b = first[key], second[key]
        records.append(
            {
                "instance_id": key,
                "expected_outcome": b["expected_outcome"],
                **{
                    prefix + field: row.get(field)
                    for prefix, row in (("old_", a), ("new_", b))
                    for field in (
                        "status",
                        "decision",
                        "decision_correct",
                        "overall",
                        "mergeable",
                        "model_decisions",
                        "api_receipt_records",
                        "operational_tool_calls",
                        "phase",
                    )
                },
                "primary_failure": classified[key]["primary"],
                "secondary_failure": ";".join(classified[key]["secondary"]),
            }
        )
    with (args.output / "paired-tasks.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    print(
        json.dumps(
            {
                "paired_tasks": len(records),
                "old": result["old"],
                "new": result["new"],
                "primary_failure_counts": result["primary_failure_counts"],
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--old-batch", type=Path, default=Path(".cache/evals/dogbench-flash-long-20261104")
    )
    parser.add_argument(
        "--old-judgments",
        type=Path,
        default=Path(".cache/evals/dogbench-flash-long-judgments-20261104"),
    )
    parser.add_argument("--new-batch", type=Path, required=True)
    parser.add_argument("--new-judgments", type=Path)
    parser.add_argument("--diagnostic-judgments", type=Path)
    parser.add_argument("--reference-items", type=Path)
    parser.add_argument("--prepare-drafts", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args())
