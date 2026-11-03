"""Linux controller verification for a native Windows candidate process."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".cache/upstream/dogbench"))


def main(key):
    from dogbench.research_inputs import verify_research_workspace
    from dogbench.verify import verify_attestation

    from scripts.dogbench_compat import install_api_only_symlink_policy

    install_api_only_symlink_policy()
    items = [
        json.loads(s)
        for s in (ROOT / ".cache/benchmarks/dogbench-data/items.jsonl").read_text().splitlines()
        if s
    ]
    item = next(i for i in items if i["instance_id"] == key)
    base = Path.home() / ".cache/docchange/dogbench-prepared"
    prepared = json.loads((base / (key + ".json")).read_text())
    workspace = Path(prepared["path"])
    verify_attestation(base / "sealed", Path(prepared["attestation"]))
    prompt = (workspace / "TASK.md").read_bytes()
    verify_research_workspace(item, workspace, prompt)
    print(json.dumps({"ok": True, "prompt_sha256": hashlib.sha256(prompt).hexdigest()}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("key")
    main(parser.parse_args().key)
