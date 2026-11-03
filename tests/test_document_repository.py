import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from shopresolve.agent.gateway import ModelReply
from shopresolve.documents.repository import (
    RepositoryDecision,
    RepositoryEnvironment,
    RepositoryRuntime,
)
from shopresolve.errors import DomainError
from shopresolve.runstore import RunStore


def git(root: Path, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True).stdout


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "guide.mdx").write_text("# Guide\nTimeout: 30\n", encoding="utf-8")
    (root / "obsolete.rst").write_text("Old guide\n", encoding="utf-8")
    (root / "server.py").write_text("SECRET = 'source is read-only'\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Smoke",
        "-c",
        "user.email=smoke@example.invalid",
        "commit",
        "-qm",
        "snapshot",
    )
    return root, RepositoryEnvironment(root, None, tmp_path / "controller")


def test_staged_create_replace_delete_patch_does_not_mutate_source(repository):
    root, env = repository
    evidence = []
    for path in ("guide.mdx", "obsolete.rst"):
        result = env.execute(RepositoryDecision(kind="read", path=path))
        evidence.append(result["evidence_id"])
    result = env.execute(
        RepositoryDecision(
            kind="propose",
            evidence_ids=evidence,
            edits=[
                {"path": "guide.mdx", "old": "Timeout: 30", "new": "Timeout: 60"},
                {"path": "obsolete.rst", "operation": "delete"},
                {"path": "new.rst", "operation": "create", "new": "New guide\n"},
            ],
        )
    )
    assert "new file mode" in result["patch"] and "deleted file mode" in result["patch"]
    assert not git(root, "status", "--porcelain")
    assert "Timeout: 30" in (root / "guide.mdx").read_text()
    applied = subprocess.run(
        ["git", "-C", str(root), "apply", "-"], input=result["patch"].encode(), capture_output=True
    )
    assert applied.returncode == 0, applied.stderr
    assert "Timeout: 60" in (root / "guide.mdx").read_text()
    assert not (root / "obsolete.rst").exists() and (root / "new.rst").exists()


def test_repository_rejects_history_escape_and_source_write(repository):
    root, env = repository
    (root / "local-secret.txt").write_text("not a tracked document", encoding="utf-8")
    with pytest.raises(DomainError):
        env.execute(RepositoryDecision(kind="read", path="local-secret.txt"))
    for path in ("../controller/index", ".git/config", "/etc/passwd"):
        with pytest.raises(DomainError):
            env.execute(RepositoryDecision(kind="read", path=path))
    evidence = env.execute(RepositoryDecision(kind="read", path="server.py"))["evidence_id"]
    with pytest.raises(DomainError):
        env.execute(
            RepositoryDecision(
                kind="propose",
                evidence_ids=[evidence],
                edits=[{"path": "server.py", "old": "SECRET", "new": "PUBLIC"}],
            )
        )


@pytest.mark.asyncio
async def test_draft_is_reviewed_before_approval(repository, commerce, tmp_path):
    root, env = repository
    _, sessions, _ = commerce
    store = RunStore(sessions)
    conversation = await store.conversation("alice", str(uuid4()))
    cfg = {"provider": "demo", "domain": "repository", "model_limit": 4}
    rid = (
        await store.submit(
            "alice",
            conversation["id"],
            {"text": "Change timeout to 60", "client_message_id": str(uuid4())},
            str(uuid4()),
            cfg,
        )
    )["run_id"]
    lease = await store.claim(rid, "review-test", lease_seconds=180)

    class Gateway:
        async def request(self, phase, context):
            if not context["observations"]:
                return ModelReply({"kind": "read", "path": "guide.mdx", "query": "missing-symbol"})
            if len(context["observations"]) == 1:
                return ModelReply({"kind": "read_many", "paths": ["guide.mdx", "obsolete.rst"]})
            first = context["observations"][1]["result"]["files"][0]
            return ModelReply(
                {
                    "kind": "propose",
                    "edits": [{"path": "guide.mdx", "old": "30", "new": "60"}],
                    "evidence_ids": [first["evidence_id"]],
                }
            )

    result = await RepositoryRuntime(store, Gateway(), env, tmp_path / "actions.jsonl").run(
        lease, "Change timeout to 60"
    )
    run = await store.get("alice", rid)
    assert result["decision"] == "patch" and run["status"] == "WAITING_APPROVAL"
    assert run["model_calls"] == 4
    assert "Timeout: 30" in (root / "guide.mdx").read_text()


def test_repository_requires_read_and_observed_evidence(repository):
    _, env = repository
    missing = env.execute(RepositoryDecision(kind="read", path="guide.mdx", query="not-present"))
    assert not missing["found"] and "evidence_id" not in missing
    assert "guide.mdx" not in env.read_paths
    with pytest.raises(DomainError):
        env.execute(
            RepositoryDecision(
                kind="propose",
                evidence_ids=["invented"],
                edits=[{"path": "guide.mdx", "old": "30", "new": "60"}],
            )
        )
    evidence = env.execute(RepositoryDecision(kind="read", path="obsolete.rst"))["evidence_id"]
    with pytest.raises(DomainError):
        env.execute(
            RepositoryDecision(
                kind="propose",
                evidence_ids=[evidence],
                edits=[{"path": "guide.mdx", "old": "30", "new": "60"}],
            )
        )
