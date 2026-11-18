import json

import httpx
import pytest

from scripts.evaluate_models import EvaluationBudget, RecordedGateway
from shopresolve.config import Settings
from shopresolve.documents.maintenance_ledger_protocol import (
    LEDGER_POLICY,
    LEDGER_PREFIX,
    PROTOCOLS,
    decode_ledger_json,
)
from shopresolve.documents.repository import (
    RepositoryGateway,
    RepositoryRuntime,
    run_with_lease_recovery,
)
from shopresolve.errors import DomainError
from shopresolve.models import ModelStep, Run
from shopresolve.runstore import RunStore
from tests.test_document_repository import git
from tests.test_maintenance_belief import (
    belief_repository,  # noqa: F401
    repository_fixture,  # noqa: F401
)
from tests.test_maintenance_taskgraph import submit


def question(path, *, queries=None):
    return {
        "question": "Does this document describe the timeout?",
        "root": " DOCS ",
        "paths": [path, path],
        "queries": queries or [],
        "optional_label": "ignored",
    }


def edit_target(context, path):
    docs = [item["evidence_id"] for item in context["evidence"] if item["path"] == path]
    return {
        "path": path,
        "reason": "Document the current 60-second value",
        "evidence_ids": [context["implementation_evidence_id"], *docs],
    }


def no_change(context):
    return {
        "kind": "no_change",
        "reason": "Internal change with no effect on users",
        "evidence_ids": [
            context["implementation_evidence_id"],
            *[e["evidence_id"] for e in context["evidence"] if e["root"] == "docs"][:8],
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    ["normal", "impact", "research", "read", "revise", "overturn", "lease", "nochange", "unsafe"],
)
async def test_ledger_workflow_through_http_and_budget(request, commerce, tmp_path, mode):
    root, env = request.getfixturevalue("belief_repository")
    phases, contexts = [], []

    class ReclaimStore(RunStore):
        async def save(self, lease, state, **kwargs):
            await super().save(lease, state, **kwargs)
            if mode == "lease" and state["maintenance"]["phase"] == "DRAFT":
                async with self.sessions.begin() as session:
                    run = await session.get(Run, lease.run_id)
                    run.lease_until = 0
                raise DomainError("LEASE_LOST", "Injected old lease")

    store = ReclaimStore(commerce[1])
    run_id, lease = await submit(store, policy=LEDGER_POLICY, model_limit=12)

    def decision(phase, context):
        if phase == "REASON":
            if len(phases) == 1:
                path = "migration.md" if mode == "impact" else "guide.mdx"
                return {
                    "kind": " INVESTIGATE ",
                    "questions": [
                        question(
                            path, queries=["timeout", "timeout", "configuration", "value", "fourth"]
                        )
                    ]
                    if mode != "impact"
                    else [question(path)],
                    "debug": True,
                }
            if mode == "impact" and phases.count("REASON") == 2:
                return {
                    "kind": "research",
                    "questions": [question("migration.md", queries=["compatibility"])],
                }
            if mode == "nochange":
                return no_change(context)
            return {"kind": "edit", "surfaces": [edit_target(context, "guide.mdx")]}
        if phase == "IMPACT":
            path = "migration.md" if mode == "research" else "guide.mdx"
            return {"kind": "edit", "surfaces": [edit_target(context, path)]}
        if phase == "DRAFT":
            assert "task" not in context and "implementation" not in context
            path = context["targets"][0]["path"]
            if mode == "unsafe":
                return {"edits": [{"path": "migration.md", "old": "was 30", "new": "is 60"}]}
            if path == "migration.md":
                return {"edits": [{"path": path, "old": "was 30", "new": "is 60"}]}
            if mode == "read" and phases.count("DRAFT") == 1:
                return {"edits": [{"path": path, "old": "Timeout: 900", "new": "Timeout: 60"}]}
            return {"edits": [{"path": path, "old": "Timeout: 30", "new": "Timeout: 60"}]}
        if phase == "NECESSITY":
            if mode in {"overturn", "nochange"}:
                return no_change(context)
            return {
                "kind": "necessary",
                "reason": "Users need the corrected default",
                "evidence_ids": [e["evidence_id"] for e in context["evidence"][:1]],
            }
        if phases.count("QUALITY") == 1:
            if mode == "research":
                return {
                    "kind": "research",
                    "reason": "Verify migration guidance",
                    "questions": [question("migration.md")],
                }
            if mode == "revise":
                return {"kind": "revise", "reason": "Clarify units", "paths": ["guide.mdx"]}
        return {"kind": "pass", "reason": "Facts, coverage and placement checked"}

    async def respond(request):
        payload = json.loads(request.content)
        step = len(phases)
        async with commerce[1]() as session:
            call = await session.get(ModelStep, (run_id, step))
            phase = call.phase.removeprefix(LEDGER_PREFIX)
        phases.append(phase)
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["max_tokens"] == (4096 if phase == "DRAFT" else 1536)
        context = json.loads(payload["messages"][1]["content"])
        contexts.append(context)
        output = json.dumps(decision(phase, context))
        if step == 0:
            output = "```json\n" + output + "\n```"
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": output}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 25},
            },
        )

    if mode == "lease":
        async with commerce[1].begin() as session:
            run = await session.get(Run, run_id)
            run.lease_until = 0
    gateway = RepositoryGateway(
        Settings(
            _env_file=None,
            provider="deepseek",
            model="deepseek-flash",
            model_api_key="test-key",
            model_base_url="https://api.deepseek.com",
            model_max_output_tokens=4096,
            allow_paid_model=True,
        ),
        transport=httpx.MockTransport(respond),
    )
    budget = EvaluationBudget(tmp_path / "budget.sqlite", 12, 2, 0)
    wrapped = RecordedGateway(gateway, budget, tmp_path / "calls.jsonl")
    trace = tmp_path / "trace.jsonl"
    try:
        if mode == "lease":
            result = await run_with_lease_recovery(
                store, run_id, wrapped, env, trace, "Update timeout", worker_id="replacement"
            )
        else:
            result = await RepositoryRuntime(store, wrapped, env, trace).run(
                lease, "Update timeout"
            )
    finally:
        await gateway.aclose()
    expected = {"overturn": "abstain", "nochange": "abstain", "unsafe": "unresolved"}
    assert result["decision"] == expected.get(mode, "patch")
    assert git(root, "status", "--porcelain") == b""
    async with commerce[1]() as session:
        run = await session.get(Run, run_id)
        data = run.state["maintenance"]
        assert (
            run.model_calls
            == len(contexts)
            == budget.summary()["by_model"]["deepseek-flash"]["calls"]
        )
        assert data["normalized_steps"]["0"] == "REASON"
        if mode == "unsafe":
            assert result["reason_code"] == "REPAIR_SCOPE"
            assert "candidate" not in data
        else:
            assert not data.get("protocol_retries")
        if mode == "normal":
            assert phases == ["REASON", "REASON", "DRAFT", "NECESSITY", "QUALITY"]
        if mode == "impact":
            assert [r["budget"] for r in data["research_history"]] == [
                "general",
                "general",
                "impact",
            ]
        if mode == "research":
            assert data["research_history"][-1]["budget"] == "review"
            assert result["changed_paths"] == ["guide.mdx", "migration.md"]
        if mode == "revise":
            assert phases[-4:] == ["QUALITY", "DRAFT", "NECESSITY", "QUALITY"]
        if mode == "read":
            assert data["recovery"]["counts"]["DRAFT:edit_read"] == 1
        if mode == "overturn":
            assert "QUALITY" not in phases
            assert data["surface_ledger"]["guide.mdx"]["status"] == "dismissed"
            assert data["previous_candidate"]["decision"] == "patch"
        if mode == "lease":
            assert run.fencing_token == 3 and phases.count("DRAFT") == 1
        if mode == "nochange":
            assert phases == ["REASON", "REASON", "NECESSITY"]


def test_normalization_does_not_accept_ambiguous_json():
    with pytest.raises(ValueError):
        decode_ledger_json('{"kind":"edit"}{"kind":"no_change"}')
    with pytest.raises(ValueError, match="Duplicate JSON"):
        decode_ledger_json('{"kind":"edit","kind":"no_change"}')


def test_quality_preserves_detailed_revision_guidance():
    reason = "Revision guidance grounded in evidence. " * 45
    review = PROTOCOLS["QUALITY"].validate_python(
        {"kind": "revise", "reason": reason, "paths": ["guide.mdx"]}
    )
    assert review.reason == reason and len(review.reason) > 1200
