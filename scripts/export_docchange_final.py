"""Export public benchmark evidence, never local keys, databases or source workspaces."""

import argparse
import csv
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main(args):
    batch, out = args.batch, args.output
    out.mkdir(parents=True, exist_ok=True)
    plan = read(batch / "plan.json")
    generation = read(batch / "summary.json")
    predictions = [
        json.loads(s)
        for s in (batch / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
        if s
    ]
    empty_errors = []
    for prediction in predictions:
        if prediction["status"] in {"error", "timeout", "invalid"} and not prediction.get("error"):
            empty_errors.append(prediction["instance_id"])
            # Match score_dogbench's schema-only normalization; no decision/score changes.
            prediction["error"] = (
                "Transport failure with an empty exception message; see original trace"
            )
    ids = [r["instance_id"] for r in predictions]
    assert len(ids) == len(set(ids)) and set(ids) <= set(plan["items"])
    scores = (
        read(args.judgments / "report.json") if (args.judgments / "report.json").exists() else None
    )
    by_id = {r["instance_id"]: r for r in scores["scores"]} if scores else {}
    patch_dir = out / "patches"
    patch_dir.mkdir(exist_ok=True)
    draft_dir = out / "unreviewed-drafts"
    draft_dir.mkdir(exist_ok=True)
    rejected_dir = out / "rejected-drafts"
    rejected_dir.mkdir(exist_ok=True)
    rows, policy, calls = [], [], []
    for prediction in predictions:
        key = prediction["instance_id"]
        folder = batch / "tasks" / key
        result = read(folder / "result.json")
        state, used = {}, None
        if (folder / "runs.sqlite").exists():
            with sqlite3.connect(folder / "runs.sqlite") as db:
                record = db.execute("SELECT state,model_calls FROM runs").fetchone()
                if record:
                    state, used = json.loads(record[0]), record[1]
        if prediction.get("patch"):
            data = prediction["patch"].encode("utf-8")
            (patch_dir / (key + ".patch")).write_bytes(data)
            artifact = (folder / "candidate.patch").read_bytes()
            # Native write_text may expand LF; scoring uses the canonical JSON patch bytes.
            assert artifact in (data, data.replace(b"\n", b"\r\n"))
            if scored_hash := by_id.get(key, {}).get("patch_sha256"):
                assert hashlib.sha256(data).hexdigest() == scored_hash
        maintenance = state.get("maintenance", {})
        candidate = maintenance.get("candidate") or state.get("repository_candidate", {})
        if maintenance:
            # Public diagnostics retain provenance, not full source/task text.
            graph = json.loads(json.dumps(maintenance.get("graph", {})))
            for node in graph.get("nodes", []):
                if node.get("type") == "evidence" and "content" in node:
                    content = node.pop("content")
                    node["exported_content_sha256"] = hashlib.sha256(
                        json.dumps(content, ensure_ascii=False, sort_keys=True).encode()
                    ).hexdigest()
                    node["source_content_omitted_from_public_export"] = True
            diagnostic = {
                k: maintenance.get(k)
                for k in (
                    "protocol_version",
                    "phase",
                    "tasks",
                    "impact",
                    "discovery",
                    "research_history",
                    "review",
                    "repairs",
                    "snapshot",
                    "protocol_retries",
                    "protocol_feedback",
                    "surface_ledger",
                    "normalized_steps",
                    "decision_history",
                    "overturned_patch_hash",
                    "review_fallback",
                    "counterevidence_ids",
                    "review_revision_signatures",
                    "recovery",
                    "discovery_elapsed_ms",
                    "search_wait_ms",
                    "index_slot_wait_ms",
                    "tool_metrics",
                )
            }
            diagnostic["coverage"] = candidate.get("coverage", [])
            diagnostic["graph"] = graph
            diagnostic["candidate_patch_sha256"] = (
                hashlib.sha256(candidate["patch"].encode()).hexdigest()
                if candidate.get("patch")
                else None
            )
            directory = out / "maintenance-states"
            directory.mkdir(exist_ok=True)
            dump(directory / (key + ".json"), diagnostic)
        if candidate.get("patch") and not prediction.get("patch"):
            (draft_dir / (key + ".patch")).write_bytes(candidate["patch"].encode("utf-8"))
        backup = folder / "runs.before-reviewer-fix.sqlite"
        if backup.exists():
            with sqlite3.connect(backup) as db:
                saved = db.execute("SELECT state FROM runs").fetchone()
            prior = json.loads(saved[0]).get("repository_candidate", {}) if saved else {}
            if prior.get("patch") and prior["patch"] != prediction.get("patch"):
                (rejected_dir / (key + ".patch")).write_bytes(prior["patch"].encode("utf-8"))
        if (folder / "model-calls.jsonl").exists():
            calls.extend(
                json.loads(s)
                for s in (folder / "model-calls.jsonl").read_text(encoding="utf-8").splitlines()
                if s
            )
        scored = by_id.get(key, {})
        rows.append(
            {
                "instance_id": key,
                "status": prediction["status"],
                "decision": prediction.get("decision"),
                "runtime_status": result["status"],
                "runtime_decision": result.get("decision"),
                "terminal_reason_code": result.get("reason_code"),
                "model_turns": used,
                "error_type": result.get("error_type"),
                "expected_outcome": scored.get("expected_outcome"),
                "decision_correct": scored.get("decision_correct"),
                "score": scored.get("overall"),
                "blocking_p0": ";".join(scored.get("blocking_p0", [])),
                "latency_ms": result.get("latency_ms"),
                "error": prediction.get("error"),
            }
        )
        policy.append(
            {
                "instance_id": key,
                "generation_status": prediction["status"],
                "phase": state.get("repository_policy_phase"),
                "requirements": state.get("repository_requirements", []),
                "review": state.get("repository_review"),
                "repairs": state.get("repository_repairs", 0),
                "last_protocol_error": state.get("repository_policy_error"),
                "candidate_exists": bool(state.get("repository_candidate")),
                "maintenance_phase": maintenance.get("phase"),
                "maintenance_diagnostic": (
                    "maintenance-states/" + key + ".json" if maintenance else None
                ),
            }
        )
    # Include calls from incomplete tasks too; never hide their spend.
    finished = set(ids)
    for folder in (batch / "tasks").iterdir():
        f = folder / "model-calls.jsonl"
        if folder.name not in finished and f.exists():
            calls.extend(json.loads(s) for s in f.read_text(encoding="utf-8").splitlines() if s)
        database = folder / "runs.sqlite"
        if folder.name not in finished and database.exists():
            with sqlite3.connect(database) as db:
                saved = db.execute("SELECT state,model_calls FROM runs").fetchone()
            if saved:
                state = json.loads(saved[0])
                candidate = state.get("repository_candidate", {})
                if candidate.get("patch"):
                    (draft_dir / (folder.name + ".patch")).write_bytes(
                        candidate["patch"].encode("utf-8")
                    )
                policy.append(
                    {
                        "instance_id": folder.name,
                        "generation_status": (
                            "unfinished_at_user_scope_stop"
                            if (batch / "user-scope-stop.json").exists()
                            else "deferred"
                        ),
                        "model_turns": saved[1],
                        "phase": state.get("repository_policy_phase"),
                        "requirements": state.get("repository_requirements", []),
                        "review": state.get("repository_review"),
                        "candidate_exists": bool(candidate),
                    }
                )
    budget = generation["budget"]
    summary = {
        "expected_tasks": len(plan["items"]),
        "generated_tasks": len(predictions),
        "generation_complete": len(predictions) == len(plan["items"]),
        "missing_generation_ids": sorted(set(plan["items"]) - finished),
        "outcomes": dict(Counter(str((r["status"], r.get("decision"))) for r in predictions)),
        "runtime_outcomes": dict(
            Counter(str((r["runtime_status"], r.get("runtime_decision"))) for r in rows)
        ),
        "scoring": scores["summary"] if scores else None,
        "budget": budget,
        "actual_api_log_records": len(calls),
        "returned_models": dict(Counter(c.get("provider_model", "unknown") for c in calls)),
        "phase_calls": dict(Counter(c["phase"] for c in calls)),
        "unreviewed_drafts": len(list(draft_dir.glob("*.patch"))),
        "historical_rejected_drafts": len(list(rejected_dir.glob("*.patch"))),
        "empty_error_messages_normalized": empty_errors,
        "notes": [
            "API-only adapter; not the upstream native Docker/CLI leaderboard lane",
            "Public development cohort; not independent held-out generalization",
            "Deferred tasks are missing, not synthetic zero-score predictions",
            "All already-finished decisions and failures retained; no best-of reruns",
            "Reported cost estimates include unknown-usage reserves; not an invoice",
            (
                "Historical paired cohort; orchestration/protocol/harness changed. "
                "Same requested model/retrieval settings do not pin server-side weights."
                if plan.get("repository_policy") in {"taskgraph-v2", "batched-react-v1"}
                else "Model, tools and policy changed jointly; no isolated causal ablation claim"
            ),
        ],
    }
    dump(out / "generation-plan.json", plan)
    dump(out / "summary.json", summary)
    dump(out / "coverage-states.json", policy)
    if scores:
        dump(out / "scores.json", scores)
    if (batch / "authorized-budget-amendment.json").exists():
        dump(
            out / "authorized-budget-amendment.json",
            read(batch / "authorized-budget-amendment.json"),
        )
    if (batch / "source-repair-amendment.json").exists():
        dump(out / "source-repair-amendment.json", read(batch / "source-repair-amendment.json"))
    for name in (
        "engineering-recovery.json",
        "workspace-repair-audit.json",
        "billing-correction.json",
        "user-scope-stop.json",
        "ab-protocol.json",
        "controller-recovery.json",
    ):
        if (batch / name).exists():
            dump(out / name, read(batch / name))
    if (batch / "generation-stop.json").exists():
        stop = read(batch / "generation-stop.json")
        if (batch / "billing-correction.json").exists():
            stop = {
                "scope": "Historical pause before corrected continuation; not final batch status",
                "superseded_by": "billing-correction.json",
                "record": stop,
            }
        dump(out / "generation-stop.json", stop)
    (out / "predictions.jsonl").write_text(
        "".join(json.dumps(p, ensure_ascii=False) + "\n" for p in predictions), encoding="utf-8"
    )
    with (out / "tasks.csv").open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]) if rows else ["instance_id"])
        writer.writeheader()
        writer.writerows(rows)
    print(
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "expected_tasks",
                    "generated_tasks",
                    "generation_complete",
                    "actual_api_log_records",
                )
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--judgments", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args())
