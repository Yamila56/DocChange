"""Read-only audit of existing candidates; no generation or LLM calls."""

import argparse
import json
import subprocess
from pathlib import Path, PurePosixPath


def main(args):
    predictions = [
        json.loads(line)
        for line in args.predictions.read_text(encoding="utf-8").splitlines()
        if line
    ]
    rows = []
    for prediction in predictions:
        if prediction.get("status") != "completed" or prediction.get("decision") != "patch":
            continue
        key = prediction["instance_id"]
        prepared = json.loads((args.prepared / f"{key}.json").read_text(encoding="utf-8"))
        root = Path(prepared["path"]) / "docs"
        patch = prediction["patch"].encode("utf-8")

        def git(*arguments, data=None, root=root):
            return subprocess.run(
                ["git", "-C", str(root), *arguments],
                input=data,
                capture_output=True,
                check=False,
                timeout=60,
            )

        before = git("status", "--porcelain", "--untracked-files=all")
        check = git("apply", "--check", "-", data=patch)
        stats = git("apply", "--numstat", "-z", "-", data=patch)
        paths = [
            record.split("\t", 2)[-1]
            for record in stats.stdout.decode("utf-8").split("\0")
            if record
        ]
        after = git("status", "--porcelain", "--untracked-files=all")
        rows.append(
            {
                "instance_id": key,
                "apply_check_passed": check.returncode == 0,
                "worktree_clean": before.returncode == 0 and not before.stdout.strip(),
                "audit_did_not_mutate_worktree": before.stdout == after.stdout,
                "changed_paths": paths,
                "documentation_paths_only": all(
                    PurePosixPath(path).suffix.lower() in {".md", ".mdx", ".rst", ".txt"}
                    for path in paths
                ),
                "apply_error": check.stderr.decode("utf-8", errors="replace")[:1000]
                if check.returncode
                else None,
            }
        )
    report = {
        "patches": len(rows),
        "apply_check_passed": sum(r["apply_check_passed"] for r in rows),
        "worktrees_clean": sum(r["worktree_clean"] for r in rows),
        "documentation_paths_only": sum(r["documentation_paths_only"] for r in rows),
        "model_calls": 0,
        "semantic_correctness_checked": False,
        "results": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--predictions",
        type=Path,
        default=Path(".cache/evals/dogbench-final-20261102/predictions.jsonl"),
    )
    parser.add_argument(
        "--prepared", type=Path, default=Path.home() / ".cache/docchange/dogbench-prepared"
    )
    parser.add_argument(
        "--output", type=Path, default=Path(".cache/evals/dogbench-patch-audit-20261102.json")
    )
    main(parser.parse_args())
