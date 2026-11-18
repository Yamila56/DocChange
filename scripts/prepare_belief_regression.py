"""Predeclare development regression IDs using completed historical results. No model calls."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.atomic_report import atomic, atomic_text


def prepare(baseline, population):
    score_path = baseline / "scores.json"
    scores = json.loads(score_path.read_text(encoding="utf-8"))["scores"]
    full = [
        json.loads(line)["instance_id"]
        for line in population.read_text(encoding="utf-8").splitlines()
        if line
    ]
    ids = {row["instance_id"] for row in scores}
    if len(ids) != 49 or len(scores) != 49 or len(full) != 175 or len(set(full)) != 175:
        raise ValueError("Expected unique historical development49 and population175")
    if not ids <= set(full):
        raise ValueError("Development IDs must belong to the same public population")
    failures = sorted(row["instance_id"] for row in scores if not row["decision_correct"])
    quality = sorted(
        (
            row
            for row in scores
            if row["decision_correct"] and row["decision"] == "patch" and row["overall"] <= 25
        ),
        key=lambda row: (row["overall"], row["instance_id"]),
    )[:6]
    if len(failures) != 24 or len(quality) != 6:
        raise ValueError(
            "Historical cohort must have 24 direction failures and six low-quality correct patches"
        )
    quality_ids = [row["instance_id"] for row in quality]
    return {
        "version": "belief-regression-v1",
        "source_scores_sha256": hashlib.sha256(score_path.read_bytes()).hexdigest(),
        "population_sha256": hashlib.sha256(population.read_bytes()).hexdigest(),
        "selection": (
            "All24 direction failures plus lowest six correct-patch scores <=25, "
            "ties by instance_id"
        ),
        "failure_ids": failures,
        "quality_ids": quality_ids,
        "quality_baseline_scores": {row["instance_id"]: row["overall"] for row in quality},
        "regression_ids": failures + quality_ids,
        "development_ids": sorted(ids),
        "extension_ids": sorted(set(full) - ids),
        "extension_prior_exposure": (
            "All175 already participated in historical evaluation; "
            "remaining126 are an extension cohort"
        ),
        "mixed_version_49_score_permitted": False,
        "engineering_gate": {
            "failure_group_normal_terminal_minimum": 20,
            "terminal_protocol_failures": 0,
            "terminal_transport_failures": 0,
            "terminal_exact_edit_failures": 0,
        },
        "quality_group": (
            "Compare all six original and new scores, direction regressions "
            "and actual review/research/revision traces"
        ),
        "forced_review_research": False,
        "gate_failure_action": (
            "Report failure and stop before126; "
            "any later change creates a new protocol and needs explicit direction"
        ),
    }


def main(args):
    plan = prepare(args.baseline, args.population)
    if args.output.exists():
        existing = json.loads(args.output.read_text(encoding="utf-8"))
        if existing != plan:
            raise ValueError(
                "Regression selection is frozen; use a new version for a changed selection"
            )
    else:
        atomic(args.output, plan)
    for label, ids in (
        ("regression", plan["regression_ids"]),
        ("extension", plan["extension_ids"]),
    ):
        path = args.output.with_name(args.output.stem + "-" + label + ".jsonl")
        atomic_text(path, "".join(json.dumps({"instance_id": key}) + "\n" for key in ids))
    print(
        json.dumps(
            {"regression": 30, "failure": 24, "quality": 6, "extension": 126, "model_calls": 0}
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline", type=Path, default=Path("docs/evaluation/dogbench-batched-review-2026-11-18")
    )
    parser.add_argument(
        "--population",
        type=Path,
        default=Path("docs/evaluation/dogbench-2026-11-02/predictions.jsonl"),
    )
    parser.add_argument("--output", type=Path, default=Path("evals/data/belief-regression-v1.json"))
    main(parser.parse_args())
