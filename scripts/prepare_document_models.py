"""Explicitly download pinned ONNX weights; application inference remains offline."""

import hashlib
import json
import os
from pathlib import Path

from huggingface_hub import snapshot_download

from shopresolve.documents.neural import MODEL_LOCK


def main():
    os.environ.setdefault("HF_XET_CACHE", str(Path(".cache/hf-xet").resolve()))
    manifest = json.loads(MODEL_LOCK.read_text(encoding="utf-8"))
    records = {}
    for kind, spec in manifest.items():
        path = Path(
            snapshot_download(
                spec["repository"],
                revision=spec["revision"],
                cache_dir=".cache/models",
                allow_patterns=[
                    "config.json",
                    "tokenizer.json",
                    "tokenizer_config.json",
                    "special_tokens_map.json",
                    "vocab.txt",
                    spec["model_file"],
                ],
            )
        )
        records[kind] = {
            **spec,
            "files": {
                str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in path.rglob("*")
                if p.is_file()
            },
        }
        print(json.dumps({"ready": kind, "revision": spec["revision"]}), flush=True)
    Path(".cache/models/document-model-manifest.json").write_text(
        json.dumps(records, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
