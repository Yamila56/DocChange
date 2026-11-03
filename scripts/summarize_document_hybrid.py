"""Summarize complete paired public retrieval results without further model calls."""

import argparse
import csv
import gzip
import json
from pathlib import Path

import numpy as np

from scripts.evaluate_public_ir import load


def main(args):
    summary, rows = {}, []
    for name in ("scidocs", "scifact"):
        raw = json.loads((args.results / f"{name}.json").read_text(encoding="utf-8"))
        assert raw["baseline_reproduced"]
        variants = raw["variants"]
        base = variants["bm25"]["per_query"]
        qids = sorted(base)
        assert len(qids) == (1000 if name == "scidocs" else 300)
        rng = np.random.default_rng(20261103)
        indices = rng.integers(0, len(qids), size=(2000, len(qids)))
        dataset = {"queries": len(qids), "variants": {}, "paired_differences": {}}
        for mode, result in variants.items():
            dataset["variants"][mode] = result["metrics"]
            for metric, value in result["metrics"].items():
                rows.append({"dataset": name, "mode": mode, "metric": metric, "value": value})
            if mode == "bm25":
                continue
            dataset["paired_differences"][mode] = {}
            for metric in ("recall_5", "recall_10", "ndcg_cut_10", "hit_10"):
                diff = np.asarray([result["per_query"][q][metric] - base[q][metric] for q in qids])
                intervals = np.quantile(diff[indices].mean(axis=1), [0.025, 0.975])
                dataset["paired_differences"][mode][metric] = {
                    "delta": float(diff.mean()),
                    "bootstrap_95": intervals.tolist(),
                    "wins": int((diff > 1e-12).sum()),
                    "ties": int((abs(diff) <= 1e-12).sum()),
                    "losses": int((diff < -1e-12).sum()),
                }
        _, queries, qrels, _ = load(name, Path(".cache/benchmarks/beir"))
        complement = {
            "bm25_only_relevant_at10": 0,
            "dense_only_relevant_at10": 0,
            "shared_relevant_at10": 0,
            "queries_dense_adds_relevant_at10": 0,
        }
        examples = []
        for path in sorted((args.cache / name / "queries").glob("*.json.gz")):
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                record = json.load(handle)
            qid, runs = record["qid"], record["runs"]
            relevant = {d for d, r in qrels[qid].items() if r > 0}
            sparse = set(list(runs["bm25"])[:10]) & relevant
            dense = set(list(runs["dense"])[:10]) & relevant
            complement["bm25_only_relevant_at10"] += len(sparse - dense)
            complement["dense_only_relevant_at10"] += len(dense - sparse)
            complement["shared_relevant_at10"] += len(sparse & dense)
            complement["queries_dense_adds_relevant_at10"] += bool(dense - sparse)
            examples.append(
                {
                    "qid": qid,
                    "query": queries[qid],
                    "relevant_documents": len(relevant),
                    "relevant_hits_at10": {
                        mode: sorted(set(list(run)[:10]) & relevant) for mode, run in runs.items()
                    },
                    "dense_recall10_delta": variants["dense"]["per_query"][qid]["recall_10"]
                    - base[qid]["recall_10"],
                }
            )
        assert len(examples) == len(qids)
        examples.sort(key=lambda row: (row["dense_recall10_delta"], row["qid"]))
        dataset["complementarity"] = complement
        dataset["dense_gain_examples"] = list(reversed(examples[-3:]))
        dataset["dense_loss_examples"] = examples[:3]
        summary[name] = dataset
    output = {
        "bootstrap": {"replicates": 2000, "seed": 20261103, "unit": "paired query"},
        "scope": "Descriptive comparison on previously inspected public test sets",
        "datasets": summary,
    }
    (args.results / "comparison.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (args.results / "metrics.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(
        json.dumps(
            {
                name: {
                    mode: {
                        metric: row["variants"][mode][metric]
                        for metric in ("recall_5", "recall_10", "ndcg_cut_10", "hit_10")
                    }
                    for mode in row["variants"]
                }
                for name, row in summary.items()
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--results", type=Path, default=Path("docs/evaluation/public-ir-v03-2026-11-03")
    )
    parser.add_argument(
        "--cache", type=Path, default=Path(".cache/evals/public-ir-v03-cuda-20261103")
    )
    main(parser.parse_args())
