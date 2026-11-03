import json
from uuid import uuid4

import pytest

from shopresolve.agent.gateway import ModelReply
from shopresolve.documents.coverage import evaluate_review, require_edit_bindings, require_plan
from shopresolve.documents.model_profile import pro_settings
from shopresolve.documents.repository import (
    RepositoryDecision,
    RepositoryGateway,
    RepositoryRuntime,
)
from shopresolve.errors import DomainError
from shopresolve.runstore import RunStore
from tests.test_document_repository import repository  # noqa: F401


def test_unknown_sources_and_unbound_edits_fail_closed():
    plan = RepositoryDecision(
        kind="plan",
        requirements=[
            {"id": "R1", "description": "Document timeout", "evidence_ids": ["invented"]}
        ],
    )
    with pytest.raises(DomainError, match="unknown"):
        require_plan(plan.requirements, {"task"})
    action = RepositoryDecision(
        kind="propose", edits=[{"path": "guide.mdx", "old": "30", "new": "60"}]
    )
    with pytest.raises(DomainError, match="requirement_ids"):
        require_edit_bindings(action, [{"id": "R1"}], {"task"})
    review = RepositoryDecision(
        kind="review",
        ready=True,
        coverage=[
            {
                "requirement_id": "R1",
                "status": "covered",
                "evidence_ids": ["task"],
                "reason": "done",
            }
        ],
    )
    with pytest.raises(DomainError, match="associated edit"):
        evaluate_review(review, [{"id": "R1"}], {"decision": "patch", "edits": []}, {"task"})


def test_reviewer_does_not_receive_author_trajectory():
    gateway = RepositoryGateway(pro_settings(_env_file=None, model_api_key="unit-test-only"))
    context = {
        "task_prompt": "Change timeout",
        "observations": [{"secret_author_argument": "trust me"}],
        "review_input": {"candidate": {"patch": "actual patch"}},
        "policy": {"phase": "REVIEW"},
    }
    payload = gateway.build_payload("DOCUMENT_REVIEW", context)
    text = json.dumps(payload)
    assert "actual patch" in text and "secret_author_argument" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("approve_after_repair", [True, False])
async def test_coverage_review_drives_repair_and_controls_finalization(
    repository,  # noqa: F811
    commerce,
    tmp_path,
    approve_after_repair,
):
    root, env = repository
    _, sessions, _ = commerce
    store = RunStore(sessions)
    conversation = await store.conversation("alice", str(uuid4()))
    rid = (
        await store.submit(
            "alice",
            conversation["id"],
            {"text": "Change timeout to 60", "client_message_id": str(uuid4())},
            str(uuid4()),
            {
                "provider": "demo",
                "domain": "repository",
                "model_limit": 8,
                "repository_policy": "coverage-v1",
            },
        )
    )["run_id"]
    lease = await store.claim(rid, "coverage-test", lease_seconds=180)

    class Gateway:
        reviews = 0

        async def request(self, phase, context):
            policy = context["policy"]
            source = policy["task_evidence_id"]
            if phase == "DOCUMENT_PLAN":
                return ModelReply(
                    {
                        "kind": "plan",
                        "requirements": [
                            {"id": "R1", "description": "Update timeout", "evidence_ids": [source]}
                        ],
                    }
                )
            if phase == "DOCUMENT_REVIEW":
                self.reviews += 1
                ready = self.reviews == 2 and approve_after_repair
                return ModelReply(
                    {
                        "kind": "review",
                        "ready": ready,
                        "coverage": [
                            {
                                "requirement_id": "R1",
                                "status": "covered" if ready else "missing",
                                "evidence_ids": [source],
                                "reason": "Check exact timeout before finalization",
                            }
                        ],
                        "text": "Repair timeout description",
                    }
                )
            if not context["read_paths"]:
                return ModelReply({"kind": "read", "path": "guide.mdx"})
            evidence = next(
                o["result"]["evidence_id"]
                for o in context["observations"]
                if o.get("action", {}).get("kind") == "read"
            )
            return ModelReply(
                {
                    "kind": "propose",
                    "evidence_ids": [evidence],
                    "edits": [
                        {
                            "path": "guide.mdx",
                            "old": "30",
                            "new": "60",
                            "requirement_ids": ["R1"],
                            "evidence_ids": [source, evidence],
                        }
                    ],
                }
            )

    runtime = RepositoryRuntime(store, Gateway(), env, tmp_path / "coverage.events.jsonl")
    if approve_after_repair:
        result = await runtime.run(lease, "Change timeout to 60")
        assert result["decision"] == "patch"
    else:
        with pytest.raises(DomainError, match="after repair"):
            await runtime.run(lease, "Change timeout to 60")
    run = await store.get("alice", rid)
    assert run["status"] == ("WAITING_APPROVAL" if approve_after_repair else "FAILED")
    assert run["model_calls"] == 6
    assert "Timeout: 30" in (root / "guide.mdx").read_text()
