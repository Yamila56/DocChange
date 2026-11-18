import json

import httpx
import pytest

from scripts.evaluate_models import EvaluationBudget, RecordedGateway
from shopresolve.config import Settings
from shopresolve.documents.maintenance_proposal import ProposalMaintenanceRuntime
from shopresolve.documents.maintenance_proposal_protocol import (
    PROPOSAL_POLICY,
    PROPOSAL_PREFIX,
    parse_decision,
)
from shopresolve.documents.repository import RepositoryGateway, RepositoryRuntime
from shopresolve.errors import DomainError
from shopresolve.models import ModelStep, Run
from shopresolve.runstore import RunStore
from tests.test_document_repository import git
from tests.test_maintenance_belief import belief_repository, repository_fixture  # noqa: F401
from tests.test_maintenance_taskgraph import submit


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "tracked",
        "create",
        "overturn",
        "revise",
        "reopen",
        "research",
        "budget",
        "unsafe",
        "bare_no_change",
        "bad_review",
        "weak_overturn",
        "stalled",
        "invalid_delta",
        "budget_review",
        "counterevidence",
        "source_changed",
        "restart",
        "review_unresolved",
    ],
)
async def test_proposal_first_through_repository_gateway(request, commerce, tmp_path, mode):
    root, environment = request.getfixturevalue("belief_repository")

    class ProcessDeath(BaseException):
        pass

    class CrashAfterDraft(RunStore):
        async def save(self, lease, state, **kwargs):
            await super().save(lease, state, **kwargs)
            if state["maintenance"]["phase"] == "NECESSITY":
                raise ProcessDeath()

    store = (CrashAfterDraft if mode == "restart" else RunStore)(commerce[1])
    limit = 5 if mode in {"budget", "budget_review"} else 12
    run_id, lease = await submit(store, policy=PROPOSAL_POLICY, model_limit=limit)
    phases = []
    target = "added.md" if mode in {"create", "overturn"} else "guide.mdx"

    def response_for(phase, context):
        docs = {e["path"]: e["evidence_id"] for e in context["evidence"] if e["root"] == "docs"}
        impl = context.get("implementation_evidence_id")
        if phase == "REASON":
            if len(phases) == 1:
                questions = [
                    {"root": "docs", "paths": ["migration.md"], "question": "What do docs say?"}
                ]
                if mode in {"overturn", "counterevidence"}:
                    questions.append(
                        {"root": "code", "paths": ["server.py"], "question": "Check implementation"}
                    )
                return {"action": "research", "questions": questions}
            if mode in {"reopen", "bare_no_change"} and phases.count("REASON") == 2:
                return {"action": "no_change", "reason": "Maybe existing docs are enough"}
            proposed = "../invalid.md" if mode == "unsafe" else target
            return {
                "action": "propose",
                "surfaces": [
                    {
                        "path": proposed,
                        "reason": "The documented timeout may need updating",
                        "evidence_ids": [impl, docs["migration.md"]],
                        "confidence": "high",
                    }
                ],
            }
        if phase == "DRAFT":
            assert len(context["targets"]) == 1
            if target == "added.md":
                return {
                    "edits": [
                        {"path": target, "operation": "create", "new": "# Timeout\n60 seconds.\n"}
                    ]
                }
            assert target in docs
            if mode == "invalid_delta" and phases.count("DRAFT") > 1:
                return {
                    "edits": [{"path": "unauthorized.md", "operation": "create", "new": "wrong"}]
                }
            if mode == "revise" and phases.count("DRAFT") > 1:
                assert context["draft_feedback"]["defects"][0]["problem"].startswith("Mention")
            value = "60 by default" if phases.count("DRAFT") > 1 else "60"
            return {"edits": [{"path": target, "old": "Timeout: 30", "new": f"Timeout: {value}"}]}
        if phase == "NECESSITY":
            assert "targets" not in context
            assert "coverage" not in context["candidate"]
            if mode == "overturn" or (mode == "counterevidence" and phases.count("NECESSITY") == 2):
                cited = [
                    item["evidence_id"]
                    for item in context["challenge_evidence"]
                    if item["root"] == "code"
                ][:1]
                document = "migration.md" if mode == "overturn" else target
                return {
                    "action": "no_change",
                    "reason": "Existing migration guidance covers this",
                    "counterevidence_ids": [impl, docs[document], *cited],
                }
            if mode == "weak_overturn":
                return {"action": "no_change", "reason": "Uncertain", "counterevidence_ids": [impl]}
            if mode == "bare_no_change":
                return {"action": "no_change", "reason": "Unsupported", "evidence_ids": [impl]}
            if mode == "reopen" and context["candidate"]["decision"] == "abstain":
                return {
                    "action": "keep_patch",
                    "reason": "Missing note",
                    "evidence_ids": [
                        context["challenge_evidence"][0]["evidence_id"],
                    ],
                }
            return {"action": "keep_patch", "evidence_ids": list(docs.values())[:1]}
        if mode == "source_changed":
            (root / target).write_text("Timeout: 900\n", encoding="utf-8")
            return {"action": "unresolved"}
        if mode == "bad_review":
            return {"action": "wrong-action"}
        if mode in {"restart", "review_unresolved"}:
            return {"action": "unresolved", "reason": "Might need additional confirmation"}
        if mode == "counterevidence":
            extra = [
                item["evidence_id"]
                for item in context["challenge_evidence"]
                if item["root"] == "code"
            ][:1]
            return {
                "action": "counterevidence",
                "reason": "No gap exists",
                "counterevidence_ids": [impl, docs[target], *extra],
            }
        if mode == "stalled" or (
            mode in {"revise", "invalid_delta", "budget_review"} and phases.count("QUALITY") == 1
        ):
            return {
                "action": "revise",
                "defects": [
                    {
                        "path": target,
                        "problem": "Mention that the value is only the default.",
                        "evidence_ids": [docs[target]],
                    }
                ],
            }
        if mode == "research" and phases.count("QUALITY") == 1:
            return {
                "action": "revise",
                "defects": [
                    {
                        "path": target,
                        "problem": "Check placement",
                        "question": "Does migration guidance describe compatibility?",
                    }
                ],
            }
        return {"action": "pass", "reason": {"text": "Verified", "confidence": 0.9}}

    async def respond(request):
        payload = json.loads(request.content)
        assert payload["thinking"] == {"type": "disabled"}
        async with commerce[1]() as session:
            entry = await session.get(ModelStep, (run_id, len(phases)))
        assert entry.phase.startswith(PROPOSAL_PREFIX)
        phase = entry.phase.removeprefix(PROPOSAL_PREFIX)
        phases.append(phase)
        context = json.loads(payload["messages"][1]["content"])
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(response_for(phase, context))},
                    }
                ]
            },
        )

    gateway = RepositoryGateway(
        Settings(
            _env_file=None,
            provider="deepseek",
            model="deepseek-flash",
            model_base_url="https://api.deepseek.com",
            model_api_key="test-key",
            allow_paid_model=True,
        ),
        transport=httpx.MockTransport(respond),
    )
    budget = EvaluationBudget(tmp_path / "ledger.sqlite", 12, 2, 0)
    recorded = RecordedGateway(gateway, budget, tmp_path / "calls.jsonl")
    try:
        runtime = RepositoryRuntime(store, recorded, environment, tmp_path / "trace.jsonl")
        if mode == "source_changed":
            with pytest.raises(DomainError) as captured:
                await runtime.run(lease, "Update timeout")
            assert captured.value.code == "VERSION_CONFLICT"
            return
        if mode == "restart":
            with pytest.raises(ProcessDeath):
                await runtime.run(lease, "Update timeout")
            async with commerce[1].begin() as session:
                saved = await session.get(Run, run_id)
                assert saved.state["maintenance"]["best_valid_candidate"]["candidate"]["patch"]
                saved.lease_until = 0
            restored = RunStore(commerce[1])
            lease = await restored.claim(run_id, "restored", lease_seconds=180)
            runtime = RepositoryRuntime(restored, recorded, environment, tmp_path / "trace.jsonl")
        result = await runtime.run(lease, "Update timeout")
    finally:
        await gateway.aclose()

    expected = {
        "overturn": "abstain",
        "counterevidence": "abstain",
        "unsafe": "unresolved",
        "bare_no_change": "unresolved",
    }
    assert result["decision"] == expected.get(mode, "patch")
    assert git(root, "status", "--porcelain") == b""
    assert "IMPACT" not in phases
    assert len(phases) <= limit
    async with commerce[1]() as session:
        data = (await session.get(Run, run_id)).state["maintenance"]
    if mode in {"tracked", "budget"}:
        assert phases == ["REASON", "REASON", "DRAFT", "NECESSITY", "QUALITY"]
        assert any(key.startswith("proposal-read:") for key in data["tool_metrics"])
    if mode == "create":
        assert "new file mode" in result["patch"]
    if mode == "overturn":
        assert data["previous_candidate"]["decision"] == "patch"
    if mode in {"overturn", "counterevidence"}:
        assert data["counterevidence_ids"] and "best_valid_candidate" not in data
    if mode == "revise":
        assert phases[-4:] == ["NECESSITY", "QUALITY", "DRAFT", "QUALITY"]
        assert "+Timeout: 60 by default" in result["patch"]
    if mode == "reopen":
        assert phases[:4] == ["REASON", "REASON", "NECESSITY", "REASON"]
    if mode == "research":
        assert any(entry["budget"] == "review" for entry in data["research_history"])
    if mode in {
        "bad_review",
        "weak_overturn",
        "stalled",
        "invalid_delta",
        "budget_review",
        "restart",
        "review_unresolved",
    }:
        assert data["review_fallback"]["phase"] in {"DRAFT", "QUALITY", "NECESSITY"}
        assert result["review_status"] == ("unavailable" if mode == "bad_review" else "degraded")
        assert result["patch_hash"] == data["best_valid_candidate"]["candidate"]["patch_hash"]
        assert "+Timeout: 60\n" in result["patch"]
    if mode == "restart":
        assert phases == ["REASON", "REASON", "DRAFT", "NECESSITY", "QUALITY"]


def test_proposal_input_accepts_metadata_and_rejects_conflicting_actions():
    quality, normalized = parse_decision(
        "QUALITY",
        {
            "kind": " PASS ",
            "action": "pass",
            "reason": {"confidence": "unknown"},
            "questions": "unused",
            "debug": True,
        },
    )
    assert quality.kind == "pass" and normalized
    with pytest.raises(ValueError, match="Conflicting"):
        parse_decision("REASON", {"kind": "no_change", "action": "propose"})


def test_candidate_checkpoint_promotes_reviewed_improvement_and_preserves_scope():
    runtime = ProposalMaintenanceRuntime(None, None, None, None)
    first = {"patch_hash": "first", "changed_paths": ["guide.mdx"]}
    improved = {"patch_hash": "second", "changed_paths": ["guide.mdx"]}
    wider = {"patch_hash": "third", "changed_paths": ["guide.mdx", "migration.md"]}
    runtime.data = {"candidate": first, "tasks": [{"path": "guide.mdx"}]}
    runtime._checkpoint_candidate(defect_count=2)
    runtime.data["candidate"] = improved
    runtime._checkpoint_candidate()
    assert runtime.data["best_valid_candidate"]["candidate"] == first
    runtime._checkpoint_candidate(defect_count=1)
    assert runtime.data["best_valid_candidate"]["candidate"] == improved
    runtime.data["candidate"] = wider
    runtime._checkpoint_candidate(defect_count=1)
    assert runtime.data["best_valid_candidate"]["candidate"] == improved
    runtime._checkpoint_candidate(defect_count=0)
    assert runtime.data["best_valid_candidate"]["candidate"] == wider
