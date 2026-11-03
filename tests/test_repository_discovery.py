from uuid import uuid4

import numpy as np
import pytest
from sqlalchemy import update

from shopresolve.agent.gateway import ModelReply
from shopresolve.documents.repository import (
    RepositoryDecision,
    RepositoryEnvironment,
    RepositoryRuntime,
)
from shopresolve.documents.repository_search import RepositorySearch, sections
from shopresolve.errors import DomainError
from shopresolve.models import Run
from shopresolve.runstore import RunStore
from tests.test_document_repository import git


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "guide.mdx").write_text("# Guide\nTimeout: 30\n", encoding="utf-8")
    (root / "obsolete.rst").write_text("Old guide\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "base",
    )
    return root, RepositoryEnvironment(root, None, tmp_path / "controller")


def test_sections_preserve_context_and_exact_raw_ranges():
    text = "# Install\n" + "Unrelated setup.\n" * 100 + "## Authentication\nAUTH_MODE=managed\n"
    parts = sections("install.md", text)
    target = next(p for p in parts if "AUTH_MODE" in p["content"])
    assert target["heading"] == "Install > Authentication"
    assert target["start_line"] > 100
    for part in parts:
        assert text[part["start_char"] : part["end_char"]] == part["content"]
        assert len(part["content"]) <= 1800


def test_anchored_batch_retains_missing_query_without_fake_evidence(repository):
    _, env = repository
    result = env.execute(
        RepositoryDecision(
            kind="read_many",
            reads=[
                {"path": "guide.mdx", "query": "Timeout"},
                {"path": "obsolete.rst", "query": "not-present"},
            ],
        )
    )
    first, missing = result["files"]
    assert "Timeout: 30" in first["content"]
    assert missing["found"] is False and "evidence_id" not in missing
    assert "obsolete.rst" not in env.read_paths


def test_search_produces_evidence_and_unseen_replacement_is_rejected(repository):
    root, env = repository
    env.execute(RepositoryDecision(kind="read", path="guide.mdx", limit=1))
    evidence = next(iter(env.evidence))
    edit = {"path": "guide.mdx", "old": "Timeout: 30", "new": "Timeout: 60"}
    with pytest.raises(DomainError, match="Read all target ranges"):
        env.execute(RepositoryDecision(kind="propose", evidence_ids=[evidence], edits=[edit]))
    found = env.execute(
        RepositoryDecision(kind="search", query="Timeout", retrieval_mode="section_bm25")
    )["results"][0]
    assert found["heading"] == "Guide" and found["evidence_id"] in env.evidence
    result = env.execute(
        RepositoryDecision(kind="propose", evidence_ids=[found["evidence_id"]], edits=[edit])
    )
    assert "Timeout: 60" in result["patch"] and not git(root, "status", "--porcelain")


def test_deletion_requires_entire_file_and_unchanged_observed_content(repository):
    root, env = repository
    read = env.execute(RepositoryDecision(kind="read", path="guide.mdx", limit=1))
    with pytest.raises(DomainError, match="Read all target ranges"):
        env.execute(
            RepositoryDecision(
                kind="propose",
                evidence_ids=[read["evidence_id"]],
                edits=[{"path": "guide.mdx", "operation": "delete"}],
            )
        )
    (root / "guide.mdx").write_text("# Changed\nTimeout: 30\n", encoding="utf-8")
    with pytest.raises(DomainError, match="Read all target ranges"):
        env.execute(
            RepositoryDecision(
                kind="propose",
                evidence_ids=[read["evidence_id"]],
                edits=[{"path": "guide.mdx", "old": "# Changed", "new": "# Other"}],
            )
        )


def test_adjacent_observed_ranges_can_support_one_multiline_edit(repository):
    _, env = repository
    first = env.execute(RepositoryDecision(kind="read", path="guide.mdx", offset=0, limit=1))
    second = env.execute(RepositoryDecision(kind="read", path="guide.mdx", offset=1, limit=1))
    result = env.execute(
        RepositoryDecision(
            kind="propose",
            evidence_ids=[first["evidence_id"], second["evidence_id"]],
            edits=[
                {
                    "path": "guide.mdx",
                    "old": "# Guide\nTimeout: 30\n",
                    "new": "# Guide\nTimeout: 60\n",
                }
            ],
        )
    )
    assert "Timeout: 60" in result["patch"]


def test_section_dense_and_hybrid_use_real_encoder_interface_with_cache(tmp_path):
    class Encoder:
        cache_signature = "unit-only-encoder"
        calls = 0

        def documents(self, texts):
            self.calls += 1
            vectors = np.zeros((len(texts), 768), dtype=np.float32)
            for i, text in enumerate(texts):
                vectors[i, int("Retry" in text)] = 1
            return vectors

        def query(self, text):
            vector = np.zeros(768, dtype=np.float32)
            vector[1] = 1
            return vector

    encoder = Encoder()
    docs = {"retry.md": "# Network\nRetry after disconnection", "theme.md": "# Theme\nColors"}
    index = RepositorySearch(docs, encoder=encoder, cache_dir=tmp_path)
    assert index.search(["reconnect"], "dense")[0]["path"] == "retry.md"
    assert index.search(["reconnect", "network"], "hybrid")
    second = RepositorySearch(docs, encoder=encoder, cache_dir=tmp_path)
    second.search(["reconnect"], "dense")
    assert encoder.calls == 1


async def test_cached_search_restores_ranges_after_fault_before_checkpoint(
    repository, commerce, tmp_path
):
    root, env = repository
    _, sessions, clock = commerce
    store = RunStore(sessions, clock)
    conversation = await store.conversation("alice", str(uuid4()))
    response = await store.submit(
        "alice",
        conversation["id"],
        {"text": "Update timeout", "client_message_id": str(uuid4())},
        str(uuid4()),
        {"provider": "demo", "domain": "repository", "model_limit": 3},
    )
    lease = await store.claim(response["run_id"], "first", lease_seconds=180)

    class Interrupted(BaseException):
        pass

    class Gateway:
        calls = 0

        async def request(self, phase, context):
            self.calls += 1
            if not context["observations"]:
                return ModelReply(
                    {"kind": "search", "query": "Timeout", "retrieval_mode": "section_bm25"}
                )
            hit = context["observations"][0]["result"]["results"][0]
            return ModelReply(
                {
                    "kind": "propose",
                    "evidence_ids": [hit["evidence_id"]],
                    "edits": [{"path": "guide.mdx", "old": "30", "new": "60"}],
                }
            )

    gateway = Gateway()
    original = store.save_tool

    async def fault(*args):
        await original(*args)
        raise Interrupted()

    store.save_tool = fault
    with pytest.raises(Interrupted):
        await RepositoryRuntime(store, gateway, env, tmp_path / "trace.jsonl").run(
            lease, "Update timeout"
        )
    store.save_tool = original
    clock.value += 181
    # MySQL uses database time; expire the isolated test run without changing production clocks.
    async with sessions.begin() as session:
        await session.execute(update(Run).where(Run.id == response["run_id"]).values(lease_until=0))
    new_lease = await store.claim(response["run_id"], "recovered", lease_seconds=180)
    restored = RepositoryEnvironment(root, None, tmp_path / "new-controller")
    result = await RepositoryRuntime(store, gateway, restored, tmp_path / "trace.jsonl").run(
        new_lease, "Update timeout"
    )
    assert result["decision"] == "patch" and gateway.calls == 3
    assert restored.observed_spans["guide.mdx"]
    assert not restored.indices  # Search came from durable tool result, not re-execution.
    assert not git(root, "status", "--porcelain")
