"""Resume-safe trusted controller around the unmodified official prepare_item.

Labels, source IDs, source GitHub credentials and attestations stay outside sealed
agent workspaces. Optional released-input compatibility retains the original
audit findings, admitting only byte-bound official inputs with prose overlap.
"""

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
UPSTREAM = ROOT / ".cache/upstream/dogbench"
sys.path.insert(0, str(UPSTREAM))

import dogbench.sources as source_transport  # noqa: E402
import dogbench.workspaces as workspaces  # noqa: E402
from dogbench.items import load_items  # noqa: E402
from dogbench.research_inputs import research_input  # noqa: E402
from dogbench.workspaces import prepare_item  # noqa: E402

ORIGINAL_AUDIT = workspaces.context_audit
ORIGINAL_FETCH = workspaces._fetch
ORIGINAL_ATTEST = source_transport.attest_github_component
LOCKS = {}
LOCK_GUARD = threading.Lock()


def attest_repository_alias(**kwargs):
    pr = source_transport.parse_github_pr_url(kwargs["source_url"])
    declared = source_transport._github_repo_slug(kwargs["repo_url"])
    if pr and declared and pr.slug.casefold() != declared.casefold():
        left, right = github_json("/repos/" + pr.slug), github_json("/repos/" + declared)
        if not left.get("id") or left["id"] != right.get("id"):
            raise source_transport.SourceAttestationError("Repository alias IDs differ")
        original_url = kwargs["repo_url"]
        result = ORIGINAL_ATTEST(**{**kwargs, "repo_url": "https://github.com/" + pr.slug})
        result["repository_alias_binding"] = {
            "declared_url": original_url,
            "source_slug": pr.slug,
            "github_repository_id": left["id"],
        }
        return result
    return ORIGINAL_ATTEST(**kwargs)


def released_input_audit(context, item, human_diff, **kwargs):
    result = ORIGINAL_AUDIT(context, item, human_diff, **kwargs)
    result["upstream_ok"] = result["ok"]
    allowed = {"source_pr_prose_overlap", "heldout_gold_token_overlap", "heldout_gold_line_overlap"}
    if not result["ok"] and all(f["code"] in allowed for f in result["findings"]):
        frozen = research_input(item, required=True)
        result["ok"] = True
        result["compatibility_admission"] = {
            "policy": "exact-released-input-with-retained-prose-overlap-v1",
            "item_sha256": frozen["item_sha256"],
            "prompt_sha256": frozen["prompt_sha256"],
            "reason": "Upstream known-issues: context checks reject approved development inputs",
            "strict_upstream_eligible": False,
        }
    return result


def precache(item, cache):
    for component in (item["docs"], item.get("code")):
        if not component:
            continue
        url = component["repo_url"]
        name = hashlib.sha256(url.encode()).hexdigest()[:24]
        target = cache / name
        with LOCK_GUARD:
            lock = LOCKS.setdefault(name, threading.Lock())
        with lock:
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                workspaces._run(["git", "init", "--bare", "--quiet", str(target)])
            for sha in {component["base_sha"], component.get("head_sha")} - {None}:
                present = workspaces._run(
                    ["git", "-C", str(target), "cat-file", "-e", sha], check=False
                )
                if present.returncode:
                    command = [
                        "git",
                        "-C",
                        str(target),
                        "fetch",
                        "--quiet",
                        "--no-tags",
                        "--depth=1",
                        url,
                        sha,
                    ]
                    for attempt in range(3):
                        try:
                            workspaces._run(command)
                            break
                        except workspaces.WorkspacePreparationError:
                            if attempt == 2:
                                raise
                            time.sleep(attempt + 1)
                workspaces._run(["git", "-C", str(target), "update-ref", "refs/bench/" + sha, sha])


def install_cache(cache):
    def fetch(repo, url, sha):
        target = cache / hashlib.sha256(url.encode()).hexdigest()[:24]
        # Git copies objects; no alternates or shared object store reaches the agent.
        return ORIGINAL_FETCH(repo, str(target), sha)

    workspaces._fetch = fetch


def github_json(path):
    # Only the HTTP transport changes: all official source, tree, prompt and
    # contamination checks remain intact. urllib returned truncated HTTP bodies
    # in this environment; httpx validates the complete JSON response.
    import httpx

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "dogbench-docchange-controller",
        "Authorization": "Bearer " + os.environ["GH_TOKEN"],
    }
    for attempt in range(3):
        try:
            response = httpx.get(
                "https://api.github.com" + path, headers=headers, timeout=45, follow_redirects=True
            )
            response.raise_for_status()
            return response.json()
        except (httpx.TransportError, json.JSONDecodeError):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--retry-failed", action="store_true")
    p.add_argument("--released-input-compatibility", action="store_true")
    p.add_argument("--api-only-symlinks", action="store_true")
    p.add_argument(
        "--output", type=Path, default=Path.home() / ".cache/docchange/dogbench-prepared"
    )
    a = p.parse_args()
    source = ROOT / ".cache/benchmarks/dogbench-data/items.jsonl"
    reference = json.loads((UPSTREAM / "data/release-reference.json").read_text())
    if (
        hashlib.sha256(source.read_bytes()).hexdigest()
        != reference["files"]["items.jsonl"]["sha256"]
    ):
        raise ValueError("Public dataset checksum mismatch")
    if not os.environ.get("GH_TOKEN"):
        gh = shutil.which("gh") or str(
            Path(os.environ["LOCALAPPDATA"]) / "Programs/GitHubCLI/2.103.3/bin/gh.exe"
        )
        token = subprocess.run(
            [gh, "auth", "token"], capture_output=True, text=True, check=True
        ).stdout.strip()
        os.environ["GH_TOKEN"] = token  # trusted controller only; never passed to a candidate
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    source_transport._github_json = github_json
    source_transport.attest_github_component = attest_repository_alias
    if a.api_only_symlinks:
        from scripts.dogbench_compat import install_api_only_symlink_policy

        install_api_only_symlink_policy()
    if a.released_input_compatibility:
        workspaces.context_audit = released_input_audit
    cache = Path.home() / ".cache/docchange/git-cache"
    install_cache(cache)
    items = load_items(source)
    if a.limit:
        items = items[: a.limit]
    output = a.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    def one(item):
        key = item["instance_id"]
        result = output / f"{key}.json"
        previous = None
        if result.exists():
            previous = json.loads(result.read_text(encoding="utf-8"))
            if previous["status"] == "prepared" or not a.retry_failed:
                return previous
        try:
            precache(item, cache)
            prepared = prepare_item(item, output / "sealed", offline=False)
            row = {"id": key, "status": "prepared", **prepared}
            attestation = json.loads(Path(prepared["attestation"]).read_text())
            row["strict_context_eligible"] = attestation["context_audit"].get("upstream_ok", True)
            row["context_findings"] = attestation["context_audit"]["findings"]
            if a.api_only_symlinks:
                from scripts.dogbench_compat import symlink_manifest

                row["api_only_symlink_compatibility"] = True
                row["reference_symlinks"] = symlink_manifest(Path(prepared["path"]))
        except Exception as exc:
            row = {
                "id": key,
                "status": "preparation_failed",
                "error_type": type(exc).__name__,
                "error": str(exc)[:1800],
            }
        if previous:
            row["previous_attempt"] = previous
        result.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
        return row

    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:
        for future in concurrent.futures.as_completed([pool.submit(one, item) for item in items]):
            row = future.result()
            rows.append(row)
            print(
                json.dumps(
                    {
                        "done": len(rows),
                        "total": len(items),
                        "id": row["id"],
                        "status": row["status"],
                        "error": row.get("error", "")[:160],
                    }
                ),
                flush=True,
            )
            (output / "summary.json").write_text(
                json.dumps(
                    {
                        "total": len(items),
                        "done": len(rows),
                        "prepared": sum(r["status"] == "prepared" for r in rows),
                        "rows": rows,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )


if __name__ == "__main__":
    main()
