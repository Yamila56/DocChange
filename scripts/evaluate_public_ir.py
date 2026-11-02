"""Full official BEIR test qrels, TREC evaluator, actual DocChange BM25 tokenizer.

Original document units are preserved. No query/corpus filtering by relevance.
"""

import argparse
import hashlib
import json
import statistics
import time
import urllib.request
import zipfile
from pathlib import Path

import pytrec_eval

from shopresolve.documents.retriever import DocumentRetriever

DATA = {
    "scifact": "5f7d1de60b170fc8027bb7898e2efca1",
    "scidocs": "38121350fc3a4d2f48850f6aff52e4a9",
}


def load(name, root):
    if name == "scidocs":
        # Official BEIR Hugging Face mirror; the legacy ZIP host stalled here.
        import pyarrow.parquet as pq

        corpus_root = root.parent / "scidocs-hf"
        qrel_root = root.parent / "scidocs-qrels-hf"
        corpus = pq.read_table(corpus_root / "corpus/corpus-00000-of-00001.parquet").to_pylist()
        queries = {
            r["_id"]: r["text"]
            for r in pq.read_table(
                corpus_root / "queries/queries-00000-of-00001.parquet"
            ).to_pylist()
        }
        qrels = {}
        for line in (qrel_root / "test.tsv").read_text(encoding="utf-8").splitlines()[1:]:
            qid, did, relevance = line.split("\t")
            qrels.setdefault(qid, {})[did] = int(relevance)
        return (
            corpus,
            queries,
            qrels,
            "HF:acebceec772628c6679d76d6cff30ad517d17f4e+735ea1048e37b1ebce14c6dc3d33a5edaf66d3dc",
        )
    archive = root / f"{name}.zip"
    root.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        url = f"https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/{name}.zip"
        urllib.request.urlretrieve(url, archive)
    checksum = hashlib.md5(archive.read_bytes()).hexdigest()
    if checksum != DATA[name]:
        raise RuntimeError(f"Official archive checksum mismatch: {name}")
    with zipfile.ZipFile(archive) as z:
        if any(Path(n).is_absolute() or ".." in Path(n).parts for n in z.namelist()):
            raise ValueError("Unsafe dataset archive path")
        if not (root / name / "corpus.jsonl").exists():
            z.extractall(root)
    path = root / name
    corpus = [json.loads(line) for line in (path / "corpus.jsonl").open(encoding="utf-8")]
    queries = {
        r["_id"]: r["text"]
        for r in (json.loads(line) for line in (path / "queries.jsonl").open(encoding="utf-8"))
    }
    qrels = {}
    for line in (path / "qrels/test.tsv").read_text(encoding="utf-8").splitlines()[1:]:
        qid, did, relevance = line.split("\t")
        qrels.setdefault(qid, {})[did] = int(relevance)
    return corpus, queries, qrels, checksum


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--datasets", nargs="+", choices=list(DATA), default=list(DATA))
    p.add_argument("--output", type=Path, default=Path("docs/evaluation/public-ir-2026-11-02"))
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=True)
    for name in a.datasets:
        corpus, queries, qrels, checksum = load(name, Path(".cache/benchmarks/beir"))
        retriever = DocumentRetriever(
            [d["_id"] for d in corpus], [d.get("title", "") + " " + d["text"] for d in corpus]
        )
        run, timings = {}, []
        for i, qid in enumerate(qrels):
            started = time.perf_counter()
            run[qid] = retriever.rank(queries[qid], 1000)
            # BEIR's official evaluator removes self-matches.
            run[qid].pop(qid, None)
            timings.append((time.perf_counter() - started) * 1000)
            if (i + 1) % 100 == 0:
                print(
                    json.dumps({"dataset": name, "done": i + 1, "queries": len(qrels)}), flush=True
                )
        metrics = {
            f"{m}.{','.join(map(str, (1, 3, 5, 10, 100, 1000)))}"
            for m in ("ndcg_cut", "map_cut", "recall", "P")
        }
        metrics.add("recip_rank")
        per_query = pytrec_eval.RelevanceEvaluator(qrels, metrics).evaluate(run)
        means = {
            k: statistics.mean(r[k] for r in per_query.values())
            for k in next(iter(per_query.values()))
        }
        for k in (1, 3, 5, 10):
            means[f"hit_{k}"] = statistics.mean(
                bool(set(list(run[q])[:k]) & {d for d, v in qrels[q].items() if v > 0})
                for q in qrels
            )
        result = {
            "dataset": name,
            "source_pin": checksum,
            "split": "test",
            "corpus": len(corpus),
            "queries": len(qrels),
            "graded_queries": len(per_query),
            "retriever": "DocChange BM25Okapi defaults, production tokens; original documents",
            "metric_implementation": "pytrec-eval-terrier 0.5.11 / TREC metrics used by BEIR",
            "language": "English",
            "source": "https://github.com/beir-cellar/beir",
            "scope": "Full public IR component evaluation; not editing or RAG answer accuracy",
            "metrics": means,
            "latency_ms": {
                "p50": statistics.median(timings),
                "p95": sorted(timings)[int(0.95 * (len(timings) - 1))],
            },
            "per_query": per_query,
        }
        (a.output / f"{name}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        run_dir = Path(".cache/evals/public-ir")
        run_dir.mkdir(parents=True, exist_ok=True)
        with (run_dir / f"{name}.run.trec").open("w", encoding="utf-8") as f:
            for q, hits in run.items():
                for rank, (d, score) in enumerate(hits.items(), 1):
                    f.write(f"{q} Q0 {d} {rank} {score} docchange-bm25\n")
        print(json.dumps({k: v for k, v in result.items() if k != "per_query"}), flush=True)


if __name__ == "__main__":
    main()
