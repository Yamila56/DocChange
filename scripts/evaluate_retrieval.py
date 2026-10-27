"""Measured retrieval ablations; produces data, metrics and a reproducibility manifest."""

import argparse
import hashlib
import importlib.metadata
import json
import platform
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from shopresolve.retrieval.index import NeuralEncoder, NeuralReranker, PolicyIndex


def summarize(rows):
    result = {"n": len(rows)}
    for key in ("recall@1", "recall@3", "recall@6", "mrr@6"):
        result[key] = statistics.mean(r[key] for r in rows)
    latency = [r["latency_ms"] for r in rows]
    result |= {
        "p50_ms": float(np.percentile(latency, 50)),
        "p95_ms": float(np.percentile(latency, 95)),
    }
    hits = np.asarray([r["recall@3"] for r in rows])
    rng = np.random.default_rng(20261027)
    boot = np.mean(rng.choice(hits, size=(2000, len(hits)), replace=True), axis=1)
    result["recall@3_bootstrap_ci95"] = [float(x) for x in np.percentile(boot, [2.5, 97.5])]
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lexical-only", action="store_true")
    parser.add_argument("--output", default="docs/evaluation/retrieval-v1-results.json")
    args = parser.parse_args()
    data_path = Path("evals/data/retrieval-v1.json")
    dataset = json.loads(data_path.read_text(encoding="utf-8"))
    encoder = None if args.lexical_only else NeuralEncoder()
    reranker = None if args.lexical_only else NeuralReranker()
    configurations = [("bm25", "bm25", None)]
    if encoder:
        configurations += [
            ("dense", "dense", None),
            ("hybrid", "hybrid", None),
            ("hybrid_rerank", "hybrid", reranker),
        ]
    runs = []
    for label, mode, ranker in configurations:
        index = PolicyIndex(
            "data/policies/demo.json",
            encoder=encoder if mode != "bm25" else None,
            reranker=ranker,
            mode=mode,
        )
        index.search(
            dataset["cases"][0]["query"], policy_version="demo-policy-v1", owner_id="alice"
        )
        rows = []
        for case in dataset["cases"]:
            start = time.perf_counter()
            results = index.search(case["query"], policy_version="demo-policy-v1", owner_id="alice")
            latency = (time.perf_counter() - start) * 1000
            parents = [r["parent_id"] for r in results]
            relevant = set(case["relevant"])
            row = {**case, "retrieved": parents, "latency_ms": latency, "evidence": results}
            for k in (1, 3, 6):
                row[f"recall@{k}"] = len(relevant & set(parents[:k])) / len(relevant)
            row["mrr@6"] = next(
                (1 / rank for rank, pid in enumerate(parents, 1) if pid in relevant), 0
            )
            rows.append(row)
        runs.append(
            {
                "configuration": label,
                "manifest": index.manifest(),
                "cases": rows,
                "dev": summarize([r for r in rows if r["split"] == "dev"]),
                "test": summarize([r for r in rows if r["split"] == "test"]),
            }
        )
        print(label, json.dumps(runs[-1]["test"], ensure_ascii=False), flush=True)
    weights = []
    for path in Path(".cache/models").rglob("*.onnx"):
        # Public model weights only. No .env or user document traversal.
        weights.append(
            {
                "path": str(path),
                "sha256": hashlib.file_digest(path.open("rb"), "sha256").hexdigest(),
            }
        )
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": dataset["dataset_version"],
        "dataset_sha256": hashlib.sha256(data_path.read_bytes()).hexdigest(),
        "annotation_status": dataset["annotation_status"],
        "synthetic": True,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "fastembed": importlib.metadata.version("fastembed") if encoder else None,
            "threads": 2,
        },
        "model_files": weights,
        "runs": runs,
        "limitations": [
            "Small synthetic corpus, not a public leaderboard score",
            "Assistant-authored labels await independent human review",
            "Test results are not end-to-end LLM answer accuracy",
            "No tuning was performed using these test scores",
        ],
    }
    output = Path(args.output)
    if output.exists():
        raise RuntimeError(
            "Results already exist; choose a new --output instead of overwriting evidence"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Retrieval v1 measured results",
        "",
        "Synthetic corpus; independent label review pending.",
        "Test: 16 cases. Percentages below are retrieval metrics, not Agent success.",
        "",
        "| Configuration | Recall@1 | Recall@3 | Recall@6 | MRR@6 | P50 ms | P95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for run in runs:
        m = run["test"]
        lines.append(
            f"| {run['configuration']} | {m['recall@1']:.2%} | {m['recall@3']:.2%} | "
            f"{m['recall@6']:.2%} | {m['mrr@6']:.4f} | {m['p50_ms']:.1f} | {m['p95_ms']:.1f} |"
        )
    lines += [
        "",
        "See the paired JSON for hashes, per-case outputs, confidence intervals and limitations.",
    ]
    output.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
