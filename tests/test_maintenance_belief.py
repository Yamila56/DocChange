import json

import httpx
import pytest

from scripts.evaluate_models import EvaluationBudget, RecordedGateway
from shopresolve.agent.gateway import ModelReply
from shopresolve.config import Settings
from shopresolve.documents.maintenance_belief_protocol import (
    BELIEF_POLICY,
    BELIEF_PREFIX,
    PROTOCOLS,
    build_belief_payload,
)
from shopresolve.documents.repository import (
    RepositoryEnvironment,
    RepositoryGateway,
    RepositoryRuntime,
)
from shopresolve.models import ModelStep, Run
from shopresolve.runstore import RunStore
from tests.test_document_repository import git  # noqa: F401
from tests.test_document_repository import repository as repository_fixture  # noqa: F401
from tests.test_maintenance_taskgraph import submit


@pytest.fixture
def belief_repository(request, tmp_path):
    root, _ = request.getfixturevalue("repository_fixture")
    (root / "server.py").write_text("DEFAULT_TIMEOUT = 60\n", encoding="utf8")
    (root / "migration.md").write_text("Compatibility value was 30.\n", encoding="utf8")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "change",
    )
    return root, RepositoryEnvironment(
        root, root, tmp_path / "controller", retrieval_mode="section_bm25"
    )


def orientation():
    return {
        "kind": "orient",
        "semantic_changes": ["Default timeout becomes 60"],
        "potential_contracts": ["Timeout default"],
        "uncertainties": ["Which docs expose it?"],
        "questions": [
            {
                "root": "docs",
                "question": "Find current timeout",
                "paths": ["guide.mdx"],
                "queries": ["timeout", "default", "guide", "fourth query", "timeout"],
            },
            {"root": "code", "question": "Confirm implementation", "paths": ["server.py"]},
        ],
    }


def surface(context, path="guide.mdx", *, ownership="hand_maintained", action="edit_document"):
    refs = {e["path"]: e["evidence_id"] for e in context["evidence"]}
    return {
        "path": path,
        "changed_contract": "Default timeout becomes 60",
        "current_documented_claim": "The documented default is 30",
        "status": "confirmed_edit",
        "gap": "false",
        "implementation_evidence_ids": [context["implementation_evidence_id"]],
        "documentation_evidence_ids": [refs[path]],
        "user_impact": "Users would configure the wrong timeout. " * 35,
        "ownership": ownership,
        "maintenance_action": action,
    }


def review(kind="pass", **kwargs):
    def response(context):
        refs = [
            context["implementation_evidence_id"],
            *[e["evidence_id"] for e in context["evidence"]],
        ]
        check = {
            "status": "supported",
            "reason": "Checked against implementation and original docs",
            "evidence_ids": refs,
        }
        return {
            "kind": kind,
            **{key: check for key in ("necessity", "factuality", "completeness", "placement")},
            "evidence_ids": refs,
            "reason": "Review decision",
            **kwargs,
        }

    return response


class Gateway:
    def __init__(
        self,
        *,
        research=False,
        mismatch=False,
        transport=False,
        overturn=False,
        revise=False,
        schema=False,
    ):
        self.research, self.mismatch, self.transport = research, mismatch, transport
        self.overturn, self.revise, self.schema = overturn, revise, schema
        self.phases, self.drafted, self.reviewed = [], 0, 0
        self.original_edit = None

    async def request(self, phase, context):
        phase = phase.removeprefix(BELIEF_PREFIX)
        self.phases.append(phase)
        if self.transport and len(self.phases) == 1:
            raise httpx.ReadError("Interrupted model response")
        if phase == "ORIENT":
            assert not context["targets"]
            return ModelReply(orientation())
        if phase == "BELIEF_UPDATE":
            if self.schema:
                if self.phases.count(phase) == 1:
                    return ModelReply({"kind": "update", "next_action": "unsupported"})
                assert context["protocol_feedback"]["errors"]
            selected = "migration.md" if len(context["research_history"]) > 1 else "guide.mdx"
            if selected == "migration.md":
                assert context["current_candidate"]["edits"][0]["edit_id"] == self.original_edit
                assert context["review_requested_research"]
            return ModelReply(
                {
                    "kind": "update",
                    "surfaces": [surface(context, selected)],
                    "next_action": "assess",
                    "reason": "Observed public default mismatch",
                }
            )
        if phase in {"DRAFT", "DRAFT_DELTA"}:
            self.drafted += 1
            if phase == "DRAFT_DELTA" and self.research:
                assert context["current_candidate"]["edits"][0]["edit_id"] == self.original_edit
                edits = [{"path": "migration.md", "old": "was 30", "new": "is 60"}]
                replace = []
            else:
                old = "Timeout: 99" if self.mismatch and self.drafted == 1 else "Timeout: 30"
                edits = [{"path": "guide.mdx", "old": old, "new": "Timeout: 60"}]
                replace = [self.original_edit] if phase == "DRAFT_DELTA" else []
            if self.mismatch and self.drafted > 1:
                assert context["draft_feedback"] and context["evidence"]
            return ModelReply(
                {
                    "kind": "draft",
                    "text": "Correct the default",
                    "edits": edits,
                    "replace_edit_ids": replace,
                }
            )
        self.reviewed += 1
        if self.reviewed == 1:
            self.original_edit = context["candidate"]["edits"][0]["edit_id"]
            if self.research:
                return ModelReply(
                    review(
                        "research",
                        questions=[
                            {
                                "root": "docs",
                                "question": "Inspect migration instructions",
                                "paths": ["migration.md"],
                            }
                        ],
                    )(context)
                )
            if self.overturn:
                return ModelReply(review("no_change")(context))
            if self.revise:
                return ModelReply(review("revise", paths=["guide.mdx"])(context))
        return ModelReply(review()(context))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["normal", "research", "mismatch", "transport", "overturn", "revise", "schema"]
)
async def test_semantic_loop_preserves_drafts_and_recovers_mechanical_failures(
    belief_repository,
    commerce,
    tmp_path,
    mode,
):
    root, env = belief_repository
    store = RunStore(commerce[1])
    rid, lease = await submit(store, policy=BELIEF_POLICY, model_limit=12)
    gateway = Gateway(**({mode: True} if mode != "normal" else {}))
    result = await RepositoryRuntime(store, gateway, env, tmp_path / "trace.jsonl").run(
        lease, "Synchronize the timeout documentation with the implementation"
    )
    assert result["decision"] == ("abstain" if mode == "overturn" else "patch")
    assert git(root, "status", "--porcelain") == b""
    async with commerce[1]() as session:
        row = await session.get(Run, rid)
        data = row.state["maintenance"]
        assert row.model_calls == len(gateway.phases) <= 12
        assert data["belief"]["version"] >= 2
        assert len(next(iter(data["belief"]["surfaces"].values()))["user_impact"]) > 600
        assert data["research_history"][0]["branches"][0]["queries"] == [
            "timeout",
            "default",
            "guide",
        ]
        if mode == "research":
            assert result["changed_paths"] == ["guide.mdx", "migration.md"]
            assert data["research_history"][-1]["budget"] == "review"
            assert result["patch"].count("+Timeout: 60") == 1
            assert "DRAFT_DELTA" in gateway.phases
        if mode == "mismatch":
            assert data["recovery"]["counts"]["DRAFT:edit_read"] == 1
            assert gateway.phases.count("DRAFT") == 2
        if mode == "transport":
            assert data["recovery"]["counts"]["ORIENT:model_transport"] == 1
        if mode == "schema":
            assert data["protocol_retries"]["BELIEF_UPDATE"] == 1
            assert gateway.phases.count("BELIEF_UPDATE") == 2
        if mode == "overturn":
            assert data["previous_candidate"]["decision"] == "patch"
            assert data["belief"]["candidate_hash"] is None


@pytest.mark.asyncio
async def test_restart_resumes_persisted_belief_without_repeating_orientation(
    belief_repository, commerce, tmp_path
):
    root, env = belief_repository

    class ProcessDeath(BaseException):
        pass

    class CrashStore(RunStore):
        async def save(self, lease, state, **kwargs):
            await super().save(lease, state, **kwargs)
            if state["maintenance"]["phase"] == "IMPACT_GATE":
                raise ProcessDeath()

    store = CrashStore(commerce[1])
    rid, lease = await submit(store, policy=BELIEF_POLICY, model_limit=12)
    first = Gateway()
    with pytest.raises(ProcessDeath):
        await RepositoryRuntime(store, first, env, tmp_path / "trace.jsonl").run(
            lease, "Update timeout"
        )
    async with commerce[1].begin() as session:
        row = await session.get(Run, rid)
        row.lease_until = 0
    restored = RunStore(commerce[1])
    lease = await restored.claim(rid, "restored", lease_seconds=180)
    second = Gateway()
    env = RepositoryEnvironment(root, root, tmp_path / "restored", retrieval_mode="section_bm25")
    result = await RepositoryRuntime(restored, second, env, tmp_path / "trace.jsonl").run(
        lease, "Update timeout"
    )
    assert result["decision"] == "patch"
    assert first.phases == ["ORIENT", "BELIEF_UPDATE"] and second.phases == ["DRAFT", "VERIFY"]
    assert (await restored.get("alice", rid))["model_calls"] == 4


@pytest.mark.asyncio
async def test_belief_runs_through_recorded_repository_gateway(
    belief_repository, commerce, tmp_path
):
    _, env = belief_repository
    store = RunStore(commerce[1])
    _, lease = await submit(store, policy=BELIEF_POLICY, model_limit=12)
    scripted = Gateway()
    phases = ["ORIENT", "BELIEF_UPDATE", "DRAFT", "VERIFY"]

    async def respond(request):
        phase = phases[len(scripted.phases)]
        payload = json.loads(request.content)
        assert payload["thinking"]["type"] == ("disabled" if phase == "DRAFT" else "enabled")
        context = json.loads(payload["messages"][1]["content"])
        reply = await scripted.request(BELIEF_PREFIX + phase, context)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps(reply.output)}}
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 25},
            },
        )

    settings = Settings(
        _env_file=None,
        provider="deepseek",
        model="deepseek-flash",
        model_api_key="fixture-key",
        model_base_url="https://api.deepseek.com",
        allow_paid_model=True,
        model_max_output_tokens=8192,
    )
    gateway = RepositoryGateway(settings, transport=httpx.MockTransport(respond))
    budget = EvaluationBudget(tmp_path / "budget.sqlite", 12, 2, 0)
    recorded = RecordedGateway(gateway, budget, tmp_path / "model-calls.jsonl")
    try:
        result = await RepositoryRuntime(store, recorded, env, tmp_path / "trace.jsonl").run(
            lease, "Update timeout"
        )
    finally:
        await gateway.aclose()
    assert result["decision"] == "patch"
    assert scripted.phases == phases
    assert budget.summary()["by_model"][settings.model]["calls"] == len(phases)


def test_phase_schemas_allow_semantic_text_and_allocate_thinking_only_where_used():
    settings = Settings(
        _env_file=None, provider="deepseek", model="deepseek-flash", model_max_output_tokens=8192
    )
    PROTOCOLS["ORIENT"].validate_python(orientation())
    for phase in ("ORIENT", "BELIEF_UPDATE", "DRAFT", "DRAFT_DELTA", "VERIFY"):
        assert len(BELIEF_PREFIX + phase) <= ModelStep.__table__.c.phase.type.length
        payload = build_belief_payload(settings, BELIEF_PREFIX + phase, {})
        expected = "enabled" if phase in {"ORIENT", "BELIEF_UPDATE", "VERIFY"} else "disabled"
        assert payload["thinking"]["type"] == expected
        assert "private" not in json.loads(payload["messages"][1]["content"])
