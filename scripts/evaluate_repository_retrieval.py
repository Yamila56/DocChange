"""Full released patch-task localization diagnostic; gold paths stay in the scorer.

This measures overlap with human changed paths, not canonical rubric correctness.
Candidate functions receive only authorized raw files and task/diff-derived queries.
"""

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path, PurePosixPath

from shopresolve.documents.repository_search import RepositorySearch, change_summary, encoder_for
from shopresolve.documents.retriever import DocumentRetriever
from shopresolve.documents.service import EDITABLE_SUFFIXES, valid_path

ROOT = Path(__file__).resolve().parents[1]
METHODS = ("file_bm25", "section_bm25", "dense", "hybrid", "hybrid_diff")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def git(root, *args, data=None):
    result = subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root), *args],
        input=data,
        capture_output=True,
        check=True,
    )
    return result.stdout


def windows_path(path):
    if path.startswith("/") and ROOT.drive:
        return Path("//wsl.localhost/Ubuntu" + path)
    return Path(path)


def raw_documents(root):
    records = []
    for row in git(root, "ls-tree", "-rlz", "HEAD").split(b"\0"):
        if not row:
            continue
        entry, raw_path = row.split(b"\t", 1)
        mode, kind, oid, size = entry.split()
        path = raw_path.decode("utf-8")
        if (
            mode not in {b"100644", b"100755"}
            or kind != b"blob"
            or not valid_path(path)
            or "\n" in path
            or "\r" in path
            or PurePosixPath(path).suffix.lower() not in EDITABLE_SUFFIXES
            or int(size) > 4 * 1024 * 1024
        ):
            continue
        records.append((path, oid))
    raw = git(root, "cat-file", "--batch", data=b"".join(oid + b"\n" for _, oid in records))
    position, documents = 0, {}
    for path, oid in records:
        end = raw.index(b"\n", position)
        actual, kind, size = raw[position:end].split()
        assert actual == oid and kind == b"blob"
        position = end + 1
        content = raw[position : position + int(size)]
        position += int(size) + 1
        try:
            # Match universal-newline reads used by RepositoryEnvironment.
            documents[path] = content.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeError:
            continue
    return dict(sorted(documents.items()))


def candidate_rankings(documents, query, seed_query, encoder, cache_dir, methods):
    """No item metadata, human changed paths, outcomes or rubric enter this function."""
    result = {}
    paths = list(documents)
    if "file_bm25" in methods:
        index = DocumentRetriever(paths, [p + "\n" + documents[p] for p in paths])
        result["file_bm25"] = list(index.rank(query, min(1000, len(paths)), require_match=True))
    index = RepositorySearch(documents, encoder=encoder, cache_dir=cache_dir)
    for method in methods:
        if method == "file_bm25":
            continue
        mode = "hybrid" if method == "hybrid_diff" else method
        queries = [query] + ([seed_query] if method == "hybrid_diff" and seed_query else [])
        ranked = index.rank(queries, mode, limit=1000)
        result[method] = list(dict.fromkeys(index.parts[int(i)]["path"] for i in ranked))
    return result, len(index.parts)


def metrics(ranking, gold):
    return {
        **{f"recall_{k}": len(set(ranking[:k]) & gold) / len(gold) for k in (5, 10, 20)},
        **{f"hit_{k}": float(bool(set(ranking[:k]) & gold)) for k in (5, 10, 20)},
        "mrr_top1000_sections": next((1 / i for i, p in enumerate(ranking, 1) if p in gold), 0),
    }


def main(args):
    if args.provider == "CUDAExecutionProvider":
        import onnxruntime as ort

        if args.cuda_dll_dir:
            ort.preload_dlls(directory=str(args.cuda_dll_dir))
    data = ROOT / ".cache/benchmarks/dogbench-data"
    items = [json.loads(s) for s in (data / "items.jsonl").read_text(encoding="utf-8").splitlines()]
    expected = {
        r["instance_id"]: r["expected_outcome"]
        for r in (
            json.loads(s)
            for s in (data / "outcomes.jsonl").read_text(encoding="utf-8").splitlines()
        )
    }
    items = [item for item in items if expected[item["instance_id"]] == "patch"]
    assert len(items) == 123
    source_files = [
        Path(__file__),
        ROOT / "src/shopresolve/documents/repository_search.py",
        ROOT / "src/shopresolve/documents/retriever.py",
        ROOT / "src/shopresolve/documents/neural.py",
        ROOT / "evals/document-retrieval-models.lock.json",
    ]
    plan = {
        "items": [i["instance_id"] for i in items],
        "methods": list(args.methods),
        "source_sha256": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source_files
        },
        "items_sha256": hashlib.sha256((data / "items.jsonl").read_bytes()).hexdigest(),
        "query": "Released context truncated to 12000 characters; no docs.paths or rubrics",
        "seed_query": "Up to 80 syntax identifiers/flags from authorized implementation diff",
        "provider": args.provider,
        "embedding_batch_size": args.embedding_batch_size,
        "embedding_cache": "Content-addressed same-model/runtime/provider cache; may be warm",
        "model_lock": read(ROOT / "evals/document-retrieval-models.lock.json"),
        "label": "Released docs.paths; existing paths and all changed paths scored separately",
        "scope": "Localization proxy, not end-to-end Agent or canonical-rubric score",
    }
    args.cache.mkdir(parents=True, exist_ok=True)
    if (args.cache / "plan.json").exists():
        assert read(args.cache / "plan.json") == plan, "Frozen diagnostic configuration changed"
    else:
        dump(args.cache / "plan.json", plan)
    encoder = (
        encoder_for(".cache/models", args.provider, args.embedding_batch_size)
        if any(m in args.methods for m in ("dense", "hybrid", "hybrid_diff"))
        else None
    )
    results = []
    for number, item in enumerate(items, 1):
        key = item["instance_id"]
        target = args.cache / "tasks" / f"{key}.json"
        if target.exists():
            results.append(read(target))
            continue
        started = time.perf_counter()
        prepared = read(args.prepared / f"{key}.json")
        workspace = windows_path(prepared["path"])
        documents = raw_documents(workspace / "docs")
        if not documents:
            raise ValueError("No eligible documents: " + key)
        summary = (
            change_summary(
                git(workspace / "code", "diff", "--unified=2", "HEAD^", "HEAD").decode(
                    errors="replace"
                )
            )
            if prepared["has_code"]
            else {"seed_terms": []}
        )
        rankings, chunks = candidate_rankings(
            documents,
            item["context"][:12000],
            " ".join(summary["seed_terms"]),
            encoder,
            args.vector_cache,
            args.methods,
        )
        # Labels are read only after candidate rankings have been computed.
        gold = {
            p for p in item["docs"]["paths"] if PurePosixPath(p).suffix.lower() in EDITABLE_SUFFIXES
        }
        existing = gold & set(documents)
        result = {
            "instance_id": key,
            "documents": len(documents),
            "sections": chunks,
            "gold_paths": sorted(gold),
            "existing_gold_paths": sorted(existing),
            "unretrievable_gold_paths": sorted(gold - existing),
            "rankings": rankings,
            "metrics_all_changed": {m: metrics(r, gold) for m, r in rankings.items()}
            if gold
            else {},
            "metrics_existing": {m: metrics(r, existing) for m, r in rankings.items()}
            if existing
            else {},
            "seconds": time.perf_counter() - started,
        }
        dump(target, result)
        results.append(result)
        print(
            json.dumps(
                {
                    "done": number,
                    "total": 123,
                    "id": key,
                    "documents": len(documents),
                    "sections": chunks,
                    "seconds": round(result["seconds"], 1),
                }
            ),
            flush=True,
        )
    report = {"plan": plan, "complete": len(results) == 123, "tasks": results, "summary": {}}
    for scope in ("metrics_all_changed", "metrics_existing"):
        eligible = [r[scope] for r in results if r[scope]]
        report["summary"][scope] = {
            "tasks": len(eligible),
            "methods": {
                m: {k: sum(row[m][k] for row in eligible) / len(eligible) for k in eligible[0][m]}
                for m in args.methods
            },
        }
    dump(args.output, report)
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--prepared",
        type=Path,
        default=Path("//wsl.localhost/Ubuntu/home/duyumeng/.cache/docchange/dogbench-prepared"),
    )
    parser.add_argument("--methods", nargs="+", choices=METHODS, default=list(METHODS))
    parser.add_argument(
        "--cache", type=Path, default=Path(".cache/evals/repository-retrieval-20261103")
    )
    parser.add_argument("--vector-cache", type=Path, default=Path(".cache/repository-vectors"))
    parser.add_argument(
        "--provider",
        choices=["CPUExecutionProvider", "CUDAExecutionProvider"],
        default="CPUExecutionProvider",
    )
    parser.add_argument("--cuda-dll-dir", type=Path)
    parser.add_argument("--embedding-batch-size", type=int, default=32)
    parser.add_argument(
        "--output", type=Path, default=Path("docs/evaluation/repository-retrieval-2026-11-03.json")
    )
    main(parser.parse_args())
