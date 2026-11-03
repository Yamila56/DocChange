import numpy as np
import pytest

from shopresolve.documents import service as document_service
from shopresolve.documents.retriever import SemanticDocumentRetriever
from shopresolve.documents.service import DocumentService
from shopresolve.errors import DomainError


class Encoder:
    def documents(self, texts):
        return np.asarray([[0, 1] if "retry" in t.lower() else [1, 0] for t in texts], np.float32)

    def query(self, text):
        return np.asarray([0, 1], np.float32)


def test_dense_can_retrieve_without_lexical_overlap_and_rrf_is_reproducible():
    retriever = SemanticDocumentRetriever(
        ["irrelevant", "meaning"], ["Server color settings", "Retry after disconnect"], Encoder()
    )
    ranks = retriever.rankings("reconnect", 2)
    assert list(ranks["dense"])[0] == "meaning"
    for key in ranks["hybrid"]:
        expected = sum(1 / (60 + list(ranks[m]).index(key) + 1) for m in ("bm25", "dense"))
        assert ranks["hybrid"][key] == pytest.approx(expected)
    with pytest.raises(ValueError):
        retriever.rankings("anything", query_vector=np.asarray([1, 2, 3]))


def test_rerank_changes_only_candidate_order_preserving_tail_and_membership():
    class Reranker:
        def scores(self, query, documents):
            return list(range(len(documents)))

    retriever = SemanticDocumentRetriever(
        ["a", "b", "c"], ["first", "second", "third"], Encoder(), reranker=Reranker()
    )
    result = retriever.rerank("request", {"a": 3, "b": 2, "c": 1}, candidate_k=2)
    assert list(result) == ["b", "a", "c"]
    assert list(result.values()) == [-1, -2, -3]


@pytest.mark.asyncio
async def test_optional_semantics_keeps_owner_revision_and_exact_scan_contract(
    commerce, monkeypatch
):
    _, sessions, _ = commerce
    monkeypatch.setattr(document_service, "neural_models", lambda *args: (Encoder(), None))
    service = DocumentService(sessions, retrieval_mode="dense")
    workspace = await service.create(
        "alice",
        "semantic-scope",
        {
            "theme.md": {"content": "Server color settings", "role": "document"},
            "retry.md": {"content": "retry after disconnect", "role": "document"},
        },
        "semantic-scope",
    )
    hits = await service.search("alice", workspace["id"], 1, "reconnect")
    assert hits[0]["path"] == "retry.md" and hits[0]["version"] == f"{workspace['id']}@1"
    assert await service.search("alice", workspace["id"], 1, "reconnect", exact=True) == []
    assert len(await service.search("alice", workspace["id"], 1, "retry", exact=True)) == 1
    with pytest.raises(DomainError, match="Workspace not found"):
        await service.search("bob", workspace["id"], 1, "reconnect")
