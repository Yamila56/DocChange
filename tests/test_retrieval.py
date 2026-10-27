from pathlib import Path

import numpy as np
import pytest

from shopresolve.retrieval.index import PolicyIndex

CORPUS = Path(__file__).resolve().parents[1] / "data/policies/demo.json"


class TestOnlyEncoder:
    """Deterministic fixture vectors, never used for reported neural quality."""

    name = "test-only-length-vectors"

    def documents(self, texts):
        return np.asarray(
            [[len(t), t.count("退货") + 1, t.count("取消") + 1] for t in texts], dtype=float
        )

    def query(self, text):
        return self.documents([text])[0]


@pytest.mark.parametrize("mode", ["bm25", "dense", "hybrid"])
def test_version_permission_and_parent_dedup(mode):
    index = PolicyIndex(CORPUS, mode=mode, encoder=TestOnlyEncoder() if mode != "bm25" else None)
    results = index.search(
        "bob 旧版三十天退货备注", policy_version="demo-policy-v1", owner_id="alice"
    )
    assert all(r["version"] == "demo-policy-v1" for r in results)
    assert not {"private-note", "old-return-window"} & {r["parent_id"] for r in results}
    assert len({r["parent_id"] for r in results}) == len(results)


def test_dense_without_model_is_not_silently_faked():
    with pytest.raises(ValueError, match="real encoder"):
        PolicyIndex(CORPUS, mode="dense")


def test_bm25_retrieves_exact_state_and_expands_parent():
    index = PolicyIndex(CORPUS)
    results = index.search("PENDING_SHIPMENT", policy_version="demo-policy-v1")
    assert results[0]["parent_id"] == "cancel-pending"
    assert "明确批准" in results[0]["quote"]
    assert results[0]["snapshot_id"] == index.snapshot_id


def test_unknown_version_has_no_results():
    assert PolicyIndex(CORPUS).search("退货", policy_version="unknown") == []
