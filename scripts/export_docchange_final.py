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
        candidate = state.get("repository_candidate", {})
        if candidate.get("patch") and not prediction.get("patch"):
            (draft_dir / (key + ".patch")).write_bytes(candidate["patch"].encode("utf-8"))
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
                "model_turns": used,
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
                "phase": state.get("repository_policy_phase"),
                "requirements": state.get("repository_requirements", []),
                "review": state.get("repository_review"),
                "repairs": state.get("repository_repairs", 0),
                "last_protocol_error": state.get("repository_policy_error"),
                "candidate_exists": bool(state.get("repository_candidate")),
            }
        )
    # Include calls from incomplete tasks too; never hide their spend.
    finished = set(ids)
    for folder in (batch / "tasks").iterdir():
        f = folder / "model-calls.jsonl"
        if folder.name not in finished and f.exists():
            calls.extend(json.loads(s) for s in f.read_text(encoding="utf-8").splitlines() if s)
    budget = generation["budget"]
    summary = {
        "expected_tasks": len(plan["items"]),
        "generated_tasks": len(predictions),
        "generation_complete": len(predictions) == len(plan["items"]),
        "missing_generation_ids": sorted(set(plan["items"]) - finished),
        "outcomes": dict(Counter(str((r["status"], r.get("decision"))) for r in predictions)),
        "scoring": scores["summary"] if scores else None,
        "budget": budget,
        "actual_api_log_records": len(calls),
        "returned_models": dict(Counter(c.get("provider_model", "unknown") for c in calls)),
        "phase_calls": dict(Counter(c["phase"] for c in calls)),
        "unreviewed_drafts": len(list(draft_dir.glob("*.patch"))),
        "notes": [
            "API-only adapter; not the upstream native Docker/CLI leaderboard lane",
            "Public development cohort; not independent held-out generalization",
            "Deferred tasks are missing, not synthetic zero-score predictions",
            "All already-finished decisions and failures retained; no best-of reruns",
            "Peak-price estimated costs include unknown-usage reserves; not an invoice",
            "Model, tools and policy changed jointly; no isolated causal ablation claim",
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
