"""Official frozen DoGBench rubrics/scorer, with a read-only Codex judge.

Only the CLI permission flags, retry cap, and per-item scheduling differ from
upstream. All judge prompts, schemas, evidence checks and numeric rules are
imported from the pinned upstream source, not reimplemented.
"""

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / ".cache/upstream/dogbench"
sys.path.insert(0, str(UPSTREAM))
from dogbench import judge  # noqa: E402
from dogbench.reporting import build_report  # noqa: E402


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def readonly_judge(prompt, schema, output, log, model):
    binary = shutil.which("codex")
    if not binary and os.name == "nt":
        candidates = sorted(
            (Path(os.environ["LOCALAPPDATA"]) / "OpenAI/Codex/bin").glob("*/codex.exe")
        )
        binary = str(candidates[-1]) if candidates else None
    if not binary:
        raise RuntimeError("Authenticated Codex CLI is required for the official Terra judge")
    # Ignore host plugins/MCP configuration. No provider/GitHub credentials are
    # inherited by the grading child; Codex uses its own existing login.
    environment = {
        k: v
        for k, v in os.environ.items()
        if not any(
            s in k.upper()
            for s in ("API_KEY", "GH_TOKEN", "GITHUB_TOKEN", "SHOPRESOLVE", "OPENAI_BASE_URL")
        )
    }
    with tempfile.TemporaryDirectory(prefix="docchange-judge-") as clean:
        command = [
            binary,
            "exec",
            "--ignore-user-config",
            "--ignore-rules",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--cd",
            clean,
            "--model",
            model,
            "-c",
            'model_reasoning_effort="high"',
            "-c",
            'web_search="disabled"',
            "--output-schema",
            str(schema.resolve()),
            "--output-last-message",
            str(output.resolve()),
            "--json",
        ]
        for feature in (
            "apps",
            "plugins",
            "shell_tool",
            "unified_exec",
            "browser_use",
            "browser_use_external",
            "computer_use",
            "in_app_browser",
            "skill_search",
            "multi_agent",
            "sleep_tool",
        ):
            command.extend(["--disable", feature])
        command.append("-")
        with prompt.open("rb") as source, log.open("wb") as destination:
            done = subprocess.run(
                command,
                stdin=source,
                stdout=destination,
                stderr=subprocess.STDOUT,
                env=environment,
                check=False,
                timeout=300,
            )
        if done.returncode:
            raise RuntimeError(f"Read-only judge exited {done.returncode}; see retained judge log")


def main(args):
    judge.run_codex = readonly_judge
    outcomes = [
        json.loads(line)
        for line in (ROOT / ".cache/benchmarks/dogbench-data/outcomes.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    by_id = {r["instance_id"]: r for r in outcomes}
    predictions = [
        json.loads(line)
        for line in args.predictions.read_text(encoding="utf-8").splitlines()
        if line
    ]
    for prediction in predictions:
        if prediction.get("status") in {"error", "timeout", "invalid"} and not prediction.get(
            "error"
        ):
            # Some httpx transport exceptions have an empty str(exc). Preserve
            # the failure status and zero-score semantics; repair only schema.
            prediction["error"] = (
                "Transport failure with an empty exception message; see original trace"
            )
    if args.limit:
        predictions = predictions[: args.limit]
    args.output.mkdir(parents=True, exist_ok=True)

    def one(prediction):
        key = prediction["instance_id"]
        binding = hashlib.sha256(json.dumps(prediction, sort_keys=True).encode()).hexdigest()
        folder = (
            args.output / "items" / hashlib.sha256(key.encode()).hexdigest()[:12] / binding[:12]
        )
        folder.mkdir(parents=True, exist_ok=True)
        result = folder / "result.json"
        if result.exists():
            return json.loads(result.read_text(encoding="utf-8"))
        try:
            rows = judge.score_predictions(
                [prediction],
                [by_id[key]],
                None,
                folder / str(uuid4())[:8],
                model=args.model,
                max_attempts=2,
            )
            row = rows[0]
            atomic(result, row)
            print(
                json.dumps(
                    {
                        "id": key,
                        "score": row["overall"],
                        "decision_correct": row["decision_correct"],
                    }
                ),
                flush=True,
            )
            return row
        except Exception as exc:
            atomic(folder / "judge-error.json", {"type": type(exc).__name__, "error": str(exc)})
            print(json.dumps({"id": key, "judge_error": str(exc)[:180]}), flush=True)
            return None

    scores = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for future in concurrent.futures.as_completed([pool.submit(one, p) for p in predictions]):
            row = future.result()
            if row:
                scores.append(row)
            if scores:
                report = build_report(scores, expected_ids=list(by_id))
                report["judge_configuration"] = {
                    "model": args.model,
                    "reasoning_effort": "high",
                    "max_attempts": 2,
                    "sandbox": "read-only",
                    "official_scoring_code": True,
                    "missing_judgments_are_not_zeroed": True,
                }
                atomic(args.output / "report.json", report)
    if len(scores) != len(predictions):
        raise SystemExit(
            "Some judgments failed; retained outputs support resumption. No fabricated scores."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--predictions", type=Path, default=Path(".cache/evals/dogbench-20261102/predictions.jsonl")
    )
    parser.add_argument(
        "--output", type=Path, default=Path(".cache/evals/dogbench-scored-20261102")
    )
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0)
    main(parser.parse_args())
