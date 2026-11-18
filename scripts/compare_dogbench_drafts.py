"""Score the saved first draft against the delivered patch on the same task."""

import argparse
import json
from pathlib import Path
from statistics import mean

from scripts.analyze_belief_regression import maintenance_state
from scripts.atomic_report import atomic, atomic_text


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def draft_pairs(batch):
    for row in load(batch / "results.json")["results"]:
        if row.get("status") != "completed" or row.get("decision") != "patch":
            continue
        key = row["instance_id"]
        state = maintenance_state(batch / "tasks" / key)
        if draft := state.get("first_draft"):
            yield key, draft["patch"], row["patch"]


def export(batch, output):
    predictions = [
        {"instance_id": key, "status": "completed", "decision": "patch", "patch": first}
        for key, first, final in draft_pairs(batch)
        if first != final
    ]
    atomic_text(output, "".join(json.dumps(row) + "\n" for row in predictions))


def compare(batch, output, final_report, draft_report):
    final_scores = {row["instance_id"]: row for row in load(final_report)["scores"]}
    draft_scores = {}
    if draft_report.exists():
        draft_scores = {row["instance_id"]: row for row in load(draft_report)["scores"]}
    rows = []
    for key, first, final in draft_pairs(batch):
        after = final_scores[key]
        before = after if first == final else draft_scores[key]
        if after["expected_outcome"] != "patch":
            continue
        old = set(before["blocking_p0"])
        new = set(after["blocking_p0"])
        rows.append(
            {
                "instance_id": key,
                "patch_changed": first != final,
                "draft_score": before["overall"],
                "final_score": after["overall"],
                "draft_p0_clean": before["mergeable"],
                "final_p0_clean": after["mergeable"],
                "p0_closed": sorted(old - new),
                "p0_introduced": sorted(new - old),
            }
        )
    changed = [row for row in rows if row["patch_changed"]]
    atomic(
        output,
        {
            "scope": "Tasks requiring edits with both a first draft and delivered patch",
            "paired": len(rows),
            "changed": len(changed),
            "draft_p0_clean": sum(row["draft_p0_clean"] for row in rows),
            "final_p0_clean": sum(row["final_p0_clean"] for row in rows),
            "mean_score_delta": round(mean(r["final_score"] - r["draft_score"] for r in rows), 2)
            if rows
            else None,
            "mean_changed_score_delta": round(
                mean(r["final_score"] - r["draft_score"] for r in changed), 2
            )
            if changed
            else None,
            "rows": rows,
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("export", "compare"):
        command = commands.add_parser(name)
        command.add_argument("--batch", required=True, type=Path)
        command.add_argument("--output", required=True, type=Path)
        if name == "compare":
            command.add_argument("--final-report", required=True, type=Path)
            command.add_argument("--draft-report", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "export":
        export(args.batch, args.output)
    else:
        compare(args.batch, args.output, args.final_report, args.draft_report)
