"""Frozen full-corpus BM25/dense/RRF/cross-encoder comparison; no paid APIs."""

import argparse
import gzip
import hashlib
import importlib.metadata
import json
import statistics
import time
from pathlib import Path

import numpy as np
import pytrec_eval

from scripts.evaluate_public_ir import load
from shopresolve.documents.neural import MODEL_LOCK, QUERY_PREFIX, DocumentEncoder, DocumentReranker
from shopresolve.documents.retriever import SemanticDocumentRetriever

ROOT = Path(__file__).resolve().parents[1]
MODES = ("bm25", "dense", "hybrid", "hybrid_rerank")
KS = (1, 3, 5, 10, 100, 1000)


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def encode(encoder, texts, path, *, queries=False):
    signature = hashlib.sha256(json.dumps(texts, ensure_ascii=False).encode()).hexdigest()
    path = path / signature
    path.mkdir(parents=True, exist_ok=True)
    shards = []
    started = time.perf_counter()
    for start in range(0, len(texts), 128):
        target = path / f"{start:06d}.npy"
        if not target.exists():
            batch = texts[start : start + 128]
            vectors = encoder.queries(batch) if queries else encoder.documents(batch)
            # Write/close the complete shard before it can be reused on resume.
            with target.with_suffix(".tmp").open("wb") as handle:
                np.save(handle, vectors, allow_pickle=False)
            target.with_suffix(".tmp").replace(target)
        vectors = np.load(target, allow_pickle=False)
        if vectors.shape != (min(128, len(texts) - start), 768):
            raise ValueError("Incomplete embedding shard")
        shards.append(vectors)
        if start % 512 == 0 or start + 128 >= len(texts):
            print(
                json.dumps(
                    {
                        "stage": "query_encoding" if queries else "corpus_encoding",
                        "done": min(start + 128, len(texts)),
                        "total": len(texts),
                        "elapsed_seconds": round(time.perf_counter() - started, 1),
                    }
                ),
                flush=True,
            )
    return np.vstack(shards)


def main(args):
    import onnxruntime as ort

    cache = args.cache
    cache.mkdir(parents=True, exist_ok=True)
    files = [Path(__file__), ROOT / "scripts/evaluate_public_ir.py", MODEL_LOCK] + [
        ROOT / "src/shopresolve/documents" / f for f in ("retriever.py", "neural.py", "service.py")
    ]
    plan = {
        "datasets": args.datasets,
        "modes": MODES,
        "rrf_k": 60,
        "candidates_per_branch": 1000,
        "rerank_top_k": 20,
        "document_units": "Original title + abstract, no relevance filtering or chunk expansion",
        "query_prefix": QUERY_PREFIX,
        "embedding_max_tokens": 512,
        "model_lock": json.loads(MODEL_LOCK.read_text(encoding="utf-8")),
        "model_files": json.loads(
            (ROOT / ".cache/models/document-model-manifest.json").read_text(encoding="utf-8")
        ),
        "source_sha256": {str(p.relative_to(ROOT)): sha(p) for p in files},
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("fastembed", "onnxruntime", "numpy", "pytrec-eval-terrier")
        },
        "provider": args.provider,
        "loaded_onnxruntime_version": ort.__version__,
        "billing": "Local ONNX; no generative model or paid API",
        "encoder_batch_size": 8,
        "threads": 4,
        "rerank_batch_size": 8,
        "selection": "Four predefined variants; no qrels-based parameter search. "
        "Test sets previously inspected: descriptive system comparison, not an unseen holdout.",
    }
    if args.provider == "CUDAExecutionProvider":
        plan["packages"]["onnxruntime-gpu"] = importlib.metadata.version("onnxruntime-gpu")
        plan["hardware"] = "NVIDIA GeForce RTX 4050 Laptop GPU / 6 GiB"
    # JSON round trip canonicalizes tuples before checking a resumed plan.
    plan = json.loads(json.dumps(plan))
    if (cache / "plan.json").exists():
        if json.loads((cache / "plan.json").read_text(encoding="utf-8")) != plan:
            raise ValueError("Frozen configuration changed; preserve this batch and use a new path")
    else:
        dump(cache / "plan.json", plan)
        for file in files:
            target = cache / "source" / file.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(file.read_bytes())
    if args.cuda_dll_dir:
        ort.preload_dlls(directory=str(args.cuda_dll_dir))
    encoder = DocumentEncoder(provider=args.provider)
    reranker = DocumentReranker(provider=args.provider)
    for name in args.datasets:
        dataset_start = time.perf_counter()
        corpus, queries, qrels, source = load(name, Path(".cache/benchmarks/beir"))
        identifiers = [d["_id"] for d in corpus]
        texts = [d.get("title", "") + " " + d["text"] for d in corpus]
        qids = list(qrels)
        # Candidate encoding sees text only. Relevance labels enter only the scorer below.
        vectors = encode(encoder, texts, cache / name / "embeddings")
        query_vectors = encode(
            encoder, [queries[q] for q in qids], cache / name / "query-embeddings", queries=True
        )
        retriever = SemanticDocumentRetriever(
            identifiers, texts, encoder, vectors=vectors, reranker=reranker
        )
        runs = {mode: {} for mode in MODES}
        timings = {"candidate_ranking_ms": [], "rerank_top20_ms": []}
        for i, qid in enumerate(qids):
            record_path = cache / name / "queries" / f"{i:04d}.json.gz"
            if record_path.exists():
                with gzip.open(record_path, "rt", encoding="utf-8") as handle:
                    record = json.load(handle)
                assert record["qid"] == qid
            else:
                started = time.perf_counter()
                rankings = retriever.rankings(queries[qid], 1000, query_vector=query_vectors[i])
                rank_ms = (time.perf_counter() - started) * 1000
                started = time.perf_counter()
                rankings["hybrid_rerank"] = retriever.rerank(queries[qid], rankings["hybrid"])
                rerank_ms = (time.perf_counter() - started) * 1000
                for hits in rankings.values():
                    hits.pop(qid, None)  # Official BEIR self-match exclusion.
                record = {
                    "qid": qid,
                    "runs": rankings,
                    "candidate_ranking_ms": rank_ms,
                    "rerank_top20_ms": rerank_ms,
                }
                record_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = record_path.with_suffix(".tmp")
                with gzip.open(temporary, "wt", encoding="utf-8") as handle:
                    json.dump(record, handle)
                temporary.replace(record_path)
            for mode in MODES:
                runs[mode][qid] = record["runs"][mode]
            for key in timings:
                timings[key].append(record[key])
            if (i + 1) % 25 == 0:
                print(
                    json.dumps(
                        {
                            "stage": "ranking_and_reranking",
                            "dataset": name,
                            "done": i + 1,
                            "total": len(qids),
                            "elapsed_seconds": round(time.perf_counter() - dataset_start, 1),
                        }
                    ),
                    flush=True,
                )
        measures = {f"{m}.{','.join(map(str, KS))}" for m in ("ndcg_cut", "map_cut", "recall", "P")}
        measures.add("recip_rank")
        evaluator = pytrec_eval.RelevanceEvaluator(qrels, measures)
        output = {
            "dataset": name,
            "split": "test",
            "queries": len(qids),
            "corpus": len(corpus),
            "source_pin": source,
            "plan_sha256": sha(cache / "plan.json"),
            "variants": {},
            "timing_scope": "Query embeddings precomputed; ranking and reranking timed separately. "
            "Not end-to-end online latency. Encoding logs retained in cache.",
            "timings_ms": {
                k: {"p50": statistics.median(v), "p95": sorted(v)[int(0.95 * (len(v) - 1))]}
                for k, v in timings.items()
            },
        }
        for mode in MODES:
            per_query = evaluator.evaluate(runs[mode])
            assert set(per_query) == set(qids)
            for qid in qids:
                for k in (1, 3, 5, 10):
                    per_query[qid][f"hit_{k}"] = float(
                        bool(
                            set(list(runs[mode][qid])[:k])
                            & {d for d, r in qrels[qid].items() if r > 0}
                        )
                    )
            means = {
                k: statistics.mean(v[k] for v in per_query.values()) for k in per_query[qids[0]]
            }
            output["variants"][mode] = {"metrics": means, "per_query": per_query}
            with gzip.open(cache / f"{name}-{mode}.run.trec.gz", "wt", encoding="utf-8") as handle:
                for qid, hits in runs[mode].items():
                    for rank, (did, score) in enumerate(hits.items(), 1):
                        handle.write(f"{qid} Q0 {did} {rank} {score} docchange-{mode}\n")
        # The BM25 branch must reproduce the previously published exact-score baseline.
        old = json.loads(
            (ROOT / f"docs/evaluation/public-ir-2026-11-02/{name}.json").read_text(encoding="utf-8")
        )
        assert all(
            abs(output["variants"]["bm25"]["metrics"][k] - v) < 1e-12
            for k, v in old["metrics"].items()
        )
        output["baseline_reproduced"] = True
        dump(args.output / f"{name}.json", output)
        dump(args.output / "plan.json", plan)
        print(
            json.dumps(
                {
                    "dataset": name,
                    "complete": True,
                    "metrics": {
                        m: {
                            k: output["variants"][m]["metrics"][k]
                            for k in ("recall_5", "recall_10", "ndcg_cut_10", "hit_10")
                        }
                        for m in MODES
                    },
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--datasets", nargs="+", choices=["scidocs", "scifact"], default=["scidocs", "scifact"]
    )
    parser.add_argument("--cache", type=Path, default=Path(".cache/evals/public-ir-v03-20261103"))
    parser.add_argument(
        "--output", type=Path, default=Path("docs/evaluation/public-ir-v03-2026-11-03")
    )
    parser.add_argument(
        "--provider",
        default="CPUExecutionProvider",
        choices=["CPUExecutionProvider", "CUDAExecutionProvider"],
    )
    parser.add_argument("--cuda-dll-dir", type=Path)
    main(parser.parse_args())
