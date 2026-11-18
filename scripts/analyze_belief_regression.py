"""Check the predeclared30 gate without mixing historical successes into new scores."""

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from scripts.atomic_report import atomic
from shopresolve.documents.maintenance_belief_protocol import BELIEF_POLICY
from shopresolve.documents.maintenance_ledger_protocol import LEDGER_POLICY
from shopresolve.documents.maintenance_proposal_protocol import PROPOSAL_POLICY


def maintenance_state(folder):
    execution_path = folder / "execution.json"
    if not execution_path.exists():
        return {}
    run_id = json.loads(execution_path.read_text(encoding="utf-8"))["run_id"]
    database = (folder / "runs.sqlite").resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(database, uri=True)) as db:
        row = db.execute("select state from runs where id = ?", (run_id,)).fetchone()
    return json.loads(row[0]).get("maintenance", {})


def main(args):
    selection = json.loads(args.selection.read_text(encoding="utf-8"))
    plan = json.loads((args.batch / "plan.json").read_text(encoding="utf-8"))
    if (
        set(plan["items"]) != set(selection["regression_ids"])
        or plan["repository_policy"] != args.policy
    ):
        raise ValueError("Require the predeclared 30 tasks and matching repository policy")
    results = json.loads((args.batch / "results.json").read_text(encoding="utf-8"))["results"]
    by_id = {row["instance_id"]: row for row in results}
    if len(results) != len(by_id) or set(by_id) != set(plan["items"]):
        raise ValueError("All30 unique generation outcomes are required")
    report = json.loads((args.judgments / "report.json").read_text(encoding="utf-8"))
    scored = {row["instance_id"]: row for row in report["scores"]}
    if len(report["scores"]) != len(scored) or set(scored) != set(plan["items"]):
        raise ValueError("All30 official judgments are required")
    rows = []
    for key in plan["items"]:
        result = by_id[key]
        state = maintenance_state(args.batch / "tasks" / key)
        code, error = result.get("reason_code", ""), result.get("error_type", "")
        rows.append(
            {
                "instance_id": key,
                "group": "failure" if key in selection["failure_ids"] else "quality",
                "status": result["status"],
                "decision": result.get("decision"),
                "normal_terminal": result["status"] == "completed"
                and result.get("decision") in {"patch", "abstain", "unresolved"},
                "terminal_protocol": code == "MODEL_PROTOCOL"
                or error == "ValidationError"
                or (
                    args.policy == PROPOSAL_POLICY
                    and code
                    in {"UNKNOWN_EVIDENCE", "INVALID_PATH", "REPAIR_SCOPE", "INVALID_REVIEW"}
                ),
                "terminal_transport": code == "TRANSPORT_EXHAUSTED"
                or error in {"ReadError", "ReadTimeout", "ConnectError", "ConnectTimeout"},
                "terminal_exact_edit": code in {"PATCH_MISMATCH", "UNOBSERVED_EDIT"}
                or "Exact occurrence count" in result.get("error", ""),
                "terminal_lease": code in {"LEASE_LOST", "LEASE_HEARTBEAT", "LEASE_UNAVAILABLE"}
                or "Lease heartbeat" in result.get("error", "")
                or result.get("error") == "Worker no longer owns this run",
                "decision_correct": scored[key]["decision_correct"],
                "score": scored[key]["overall"],
                "p0_clean": scored[key]["expected_outcome"] == "patch"
                and scored[key]["decision_correct"]
                and scored[key]["mergeable"],
                "review_research_rounds": sum(
                    r["budget"] == "review" for r in state.get("research_history", [])
                ),
                "candidate_revisions": len(state.get("review_revision_signatures", [])),
                "overturned_draft": bool(state.get("overturned_patch_hash")),
                "normalized_steps": len(state.get("normalized_steps", {})),
                "review_status": result.get("review_status"),
                "review_reason_code": result.get("review_reason_code"),
            }
        )
    failures = [row for row in rows if row["group"] == "failure"]
    counts = {
        key: sum(row[key] for row in failures)
        for key in (
            "normal_terminal",
            "terminal_protocol",
            "terminal_transport",
            "terminal_exact_edit",
        )
    }
    gate = counts["normal_terminal"] >= 20 and not any(
        counts[key]
        for key in (
            "terminal_protocol",
            "terminal_transport",
            "terminal_exact_edit",
        )
    )
    protocol_failures = sum(row["terminal_protocol"] for row in rows)
    lease_failures = sum(row["terminal_lease"] for row in rows)
    if args.policy == LEDGER_POLICY:
        gate = gate and protocol_failures / len(rows) < 0.02 and lease_failures == 0
    recovered = sum(row["decision_correct"] for row in failures)
    retained = sum(row["decision_correct"] for row in rows if row["group"] == "quality")
    direction_gate = recovered >= 10 and retained >= 5
    if args.policy == PROPOSAL_POLICY:
        gate = gate and protocol_failures == 0 and lease_failures == 0
    permit_expansion = gate and (args.policy != PROPOSAL_POLICY or direction_gate)
    atomic(
        args.output,
        {
            "scope": "Predeclared30 development regression",
            "repository_policy": args.policy,
            "engineering_gate_passed": gate,
            "terminal_protocol_all30": protocol_failures,
            "terminal_lease_all30": lease_failures,
            "failure24": counts,
            "newly_direction_correct": recovered,
            "retained_direction_correct": retained,
            "direction_gate_passed": direction_gate if args.policy == PROPOSAL_POLICY else None,
            "p0_clean_deliveries": sum(row["p0_clean"] for row in rows),
            "retained_with_degraded_review": sum(
                row["review_status"] in {"degraded", "unavailable"} for row in rows
            ),
            "retained_after_review_protocol_failure": sum(
                row["review_reason_code"] == "MODEL_PROTOCOL" for row in rows
            ),
            "quality6": [
                {**r, "previous_score": selection["quality_baseline_scores"][r["instance_id"]]}
                for r in rows
                if r["group"] == "quality"
            ],
            "all30": rows,
            "equivalent_49_score": None,
            "review_research_observed": any(r["review_research_rounds"] for r in rows),
            (
                "full175_permitted_by_gate"
                if args.policy in {LEDGER_POLICY, PROPOSAL_POLICY}
                else "extension126_permitted_by_gate"
            ): permit_expansion,
        },
    )
    print(json.dumps({"engineering_gate_passed": gate, "failure24": counts}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy", choices=(BELIEF_POLICY, LEDGER_POLICY, PROPOSAL_POLICY), default=PROPOSAL_POLICY
    )
    parser.add_argument(
        "--selection", type=Path, default=Path("evals/data/belief-regression-v1.json")
    )
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--judgments", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args())
