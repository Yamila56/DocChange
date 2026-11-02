"""Fetch pinned public inputs without model calls or user credentials."""

import hashlib
import json
import subprocess
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DOG_COMMIT = "35bc80734093ffc3d0801476b02ceb2ab0c80bd2"
DOG_REV = "f8e5dd2040414810cc79e8f77167c84a12d76932"
SCIDOCS_REV = "acebceec772628c6679d76d6cff30ad517d17f4e"
QREL_REV = "735ea1048e37b1ebce14c6dc3d33a5edaf66d3dc"


def fetch(url, path, checksum=None):
    if checksum is None:
        lock = json.loads((ROOT / "evals/public-benchmarks.lock.json").read_text(encoding="utf-8"))
        checksum = (
            lock["files"].get(str(path.relative_to(ROOT)).replace("\\", "/"), {}).get("sha256")
        )
        if checksum is None:
            checksum = (
                lock["files"].get(str(path.relative_to(ROOT)).replace("/", "\\"), {}).get("sha256")
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (
        not checksum or hashlib.sha256(path.read_bytes()).hexdigest() == checksum
    ):
        return
    partial = path.with_suffix(path.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=90) as response:
        response.raise_for_status()
        with partial.open("wb") as target:
            for chunk in response.iter_bytes():
                target.write(chunk)
    if checksum and hashlib.sha256(partial.read_bytes()).hexdigest() != checksum:
        raise ValueError(f"Checksum mismatch: {path.name}")
    partial.replace(path)


def main():
    upstream = ROOT / ".cache/upstream/dogbench"
    if not upstream.exists():
        upstream.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "git",
                "clone",
                "--no-checkout",
                "https://github.com/Promptless/dogbench.git",
                str(upstream),
            ],
            check=True,
        )
        subprocess.run(["git", "-C", str(upstream), "checkout", "--detach", DOG_COMMIT], check=True)
    actual = subprocess.run(
        ["git", "-C", str(upstream), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if actual != DOG_COMMIT:
        raise ValueError(
            "Existing benchmark checkout differs from the pinned commit; preserved unchanged"
        )
    files = []
    for name, sha in {
        "items.jsonl": "fd3c4707b67418da605edcadc47ff11da6c4412e0eafbea76ae855f4830412ef",
        "outcomes.jsonl": "48481665c0887a64a136a34dd7dc6d07c96dac3ed33716b674d538277b3efcce",
    }.items():
        path = ROOT / ".cache/benchmarks/dogbench-data" / name
        fetch(
            f"https://huggingface.co/datasets/promptless-research/dogbench-dev/resolve/{DOG_REV}/{name}",
            path,
            sha,
        )
        files.append(path)
    for remote, local in (
        ("corpus/corpus-00000-of-00001.parquet", "corpus/corpus-00000-of-00001.parquet"),
        ("queries/queries-00000-of-00001.parquet", "queries/queries-00000-of-00001.parquet"),
    ):
        path = ROOT / ".cache/benchmarks/scidocs-hf" / local
        fetch(f"https://huggingface.co/datasets/BeIR/scidocs/resolve/{SCIDOCS_REV}/{remote}", path)
        files.append(path)
    path = ROOT / ".cache/benchmarks/scidocs-qrels-hf/test.tsv"
    fetch(f"https://huggingface.co/datasets/BeIR/scidocs-qrels/resolve/{QREL_REV}/test.tsv", path)
    files.append(path)
    manifest = {
        "dogbench_commit": DOG_COMMIT,
        "dogbench_revision": DOG_REV,
        "scidocs_revision": SCIDOCS_REV,
        "scidocs_qrels_revision": QREL_REV,
        "files": {
            str(f.relative_to(ROOT)): {
                "sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
                "bytes": f.stat().st_size,
            }
            for f in files
        },
    }
    (ROOT / ".cache/benchmarks/public-manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(json.dumps({"files": len(files), "pinned": True, "model_calls": 0}))


if __name__ == "__main__":
    main()
