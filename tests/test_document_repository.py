import subprocess
from pathlib import Path

import pytest

from shopresolve.documents.repository import RepositoryDecision, RepositoryEnvironment
from shopresolve.errors import DomainError


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
    _, env = repository
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


def test_repository_requires_read_and_observed_evidence(repository):
    _, env = repository
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
