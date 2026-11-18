import json

import httpx
import pytest

from scripts.evaluate_models import EvaluationBudget, RecordedGateway
from shopresolve.config import Settings
from shopresolve.documents.maintenance_change_ledger import supplied_change_span
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
        "inherit",
        "ambiguous",
        "choose_change",
        "spec",
        "spec_no_change",
        "docs_only_no_change",
        "repeat",
        "redundant_quote",
        "requirements_delta",
        "requirements_recovery",
        "requirements_regression",
        "requirements_false_quote",
        "scope_expansion",
        "exact_range_read",
        "editorial",
        "editorial_unsupported",
    ],
)
async def test_proposal_first_through_repository_gateway(request, commerce, tmp_path, mode):
    root, environment = request.getfixturevalue("belief_repository")
    task = "Update timeout"
    quality_mode = mode.startswith("requirements_")
    reader_goal = "Choose the updated timeout and migrate older configurations"
    if mode == "exact_range_read":
        (root / "guide.mdx").write_text("# Guide\n" + "\n" * 160 + "Timeout: 30\n", encoding="utf8")
        git(root, "add", "guide.mdx")
        git(
            root,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "long docs",
        )
    if mode in {"spec", "spec_no_change"}:
        environment.roots["code"] = None
        task = "The timeout now defaults to 60 seconds."
    if mode.startswith("editorial"):
        environment.roots["code"] = None
        task = "Clarify how timeout compatibility is documented."

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
            if len(phases) == 1 or (mode == "repeat" and len(phases) == 2):
                questions = [
                    {"root": "docs", "paths": ["migration.md"], "question": "What do docs say?"}
                ]
                if mode in {"overturn", "counterevidence"}:
                    questions.append(
                        {"root": "code", "paths": ["server.py"], "question": "Check implementation"}
                    )
                result = {"action": "research", "questions": questions}
                if mode in {"ambiguous", "choose_change"}:
                    result["changes"] = [
                        {"summary": "The timeout changes", "evidence_ids": [impl]},
                        {"summary": "A distinct behavior changes", "evidence_ids": [impl]},
                    ]
                if mode in {"spec", "spec_no_change"}:
                    result["changes"] = [{"summary": task, "task_quote": task}]
                return result
            if (
                mode in {"reopen", "bare_no_change", "docs_only_no_change", "spec_no_change"}
                and phases.count("REASON") == 2
            ):
                return {"action": "no_change", "reason": "Maybe existing docs are enough"}
            proposed = "../invalid.md" if mode == "unsafe" else target
            if mode == "scope_expansion" and context.get("review_scope_gaps"):
                assert context["retained_draft_paths"] == [target]
                assert context["review_scope_gaps"][0]["path"] == "migration.md"
                return {
                    "action": "propose",
                    "surfaces": [
                        {
                            "path": "migration.md",
                            "reason": "Synchronize the maintained migration guide",
                            "doc_evidence_ids": [docs["migration.md"]],
                        }
                    ],
                }
            if quality_mode or mode.startswith("editorial"):
                requirements = (
                    [
                        {"statement": "State the timeout of `60` in the guide."},
                        {"statement": "Specify that this is the default timeout."},
                    ]
                    if quality_mode
                    else [
                        {
                            "statement": "State that the compatibility value was 30.",
                            "evidence_ids": [docs["migration.md"]] if mode == "editorial" else [],
                        }
                    ]
                )
                surface = {
                    "path": target,
                    "reason": "Clarify the timeout",
                    "doc_evidence_ids": [docs["migration.md"]],
                    "requirements": requirements,
                }
                action = {"action": "propose", "surfaces": [surface], "reader_goal": reader_goal}
                if mode.startswith("editorial"):
                    action["documentation_request_quote"] = task
                return action
            if mode in {
                "inherit",
                "ambiguous",
                "choose_change",
                "spec",
                "repeat",
                "redundant_quote",
            }:
                surface = {
                    "path": proposed,
                    "reason": "The documented timeout may need updating",
                    "doc_evidence_ids": [docs["migration.md"]],
                }
                if mode == "choose_change":
                    assert context["active_change_ref"] is None
                    surface["change_refs"] = ["C2"]
                if mode == "repeat":
                    assert "already ran" in context["research_feedback"]
                response = {"action": "propose", "surfaces": [surface]}
                if mode == "redundant_quote":
                    response["changes"] = [
                        {
                            "summary": "The default timeout changes",
                            "evidence_ids": [impl],
                            "task_quote": "Update timeout",
                        }
                    ]
                return response
            return {
                "action": "propose",
                "reader_goal": reader_goal,
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
            if mode == "scope_expansion":
                assert context["reader_goal"] == reader_goal
                if phases.count("DRAFT") > 1:
                    assert context["targets"][0]["path"] == "migration.md"
                    return {
                        "edits": [
                            {
                                "path": "migration.md",
                                "old": "Compatibility value was 30.",
                                "new": "Compatibility value is 60.",
                            }
                        ]
                    }
            if target == "added.md":
                return {
                    "edits": [
                        {"path": target, "operation": "create", "new": "# Timeout\n60 seconds.\n"}
                    ]
                }
            assert target in docs
            if quality_mode:
                assert context["reader_goal"] == reader_goal
                assert len(context["targets"][0]["requirements"]) == 2
                if phases.count("DRAFT") == 1:
                    return {
                        "edits": [
                            {"path": target, "old": "Timeout: 30", "new": "Timeout: 60"},
                            {"path": target, "old": "# Guide", "new": "# Timeout guide"},
                        ]
                    }
                previous = {edit["old"]: edit["edit_id"] for edit in context["prior_edits"]}
                assert context["draft_feedback"]["defects"][0]["requirement_id"].startswith("R")
                if mode == "requirements_recovery" and phases.count("DRAFT") == 3:
                    assert context["draft_feedback"]["code"] in {
                        "UNOBSERVED_EDIT",
                        "PATCH_MISMATCH",
                    }
                return {
                    "defect_ids": [context["draft_feedback"]["defects"][0]["id"]],
                    "replace_edit_ids": [
                        previous["Timeout: 30" if mode != "requirements_regression" else "# Guide"]
                    ],
                    "edits": [
                        {
                            "path": target,
                            "old": "Timeout: 999"
                            if mode == "requirements_recovery" and phases.count("DRAFT") == 2
                            else "Timeout: 30",
                            "new": "Timeout: 60 by default",
                        }
                        if mode != "requirements_regression"
                        else {"path": target, "old": "# Guide", "new": "# Default timeout"}
                    ],
                }
            if mode == "editorial":
                return {
                    "edits": [
                        {"path": target, "old": "Timeout: 30", "new": "Compatibility value was 30."}
                    ]
                }
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
            if mode in {"docs_only_no_change", "spec_no_change"}:
                refs = [
                    view["evidence_id"]
                    for view in context["challenge_evidence"]
                    if view["root"] == "docs"
                ]
                reply = {
                    "action": "no_change",
                    "reason": "The observed guidance already covers the task",
                    "evidence_ids": refs[:1],
                }
                if mode == "spec_no_change":
                    reply["change_refs"] = [context["active_change_ref"]]
                return reply
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
        if mode == "editorial":
            return {
                "action": "pass",
                "coverage": [
                    {
                        "requirement_id": context["requirements"][0]["id"],
                        "quote": "Compatibility value was 30.",
                    }
                ],
            }
        if mode == "scope_expansion":
            assert context["reader_goal"] == reader_goal
            if phases.count("QUALITY") == 1:
                return {
                    "action": "revise",
                    "defects": [
                        {
                            "path": "migration.md",
                            "problem": "The compatibility guide is stale.",
                            "expected": "Tell readers the compatibility value is 60.",
                            "evidence_ids": [impl, docs["migration.md"]],
                        }
                    ],
                }
            assert context["previous_scope_findings"][0]["path"] == "migration.md"
            return {
                "action": "pass",
                "coverage": [
                    {
                        "requirement_id": context["requirements"][0]["id"],
                        "quote": "Compatibility value is 60.",
                    }
                ],
            }
        if quality_mode:
            normal, default = (requirement["id"] for requirement in context["requirements"])
            coverage = [{"requirement_id": normal, "quote": "# Timeout guide"}]
            if mode == "requirements_false_quote":
                return {
                    "action": "pass",
                    "coverage": [
                        *coverage,
                        {"requirement_id": default, "quote": "Invented final quote."},
                    ],
                }
            if phases.count("QUALITY") == 1:
                return {
                    "action": "revise",
                    "coverage": [
                        {"requirement_id": normal, "quote": "An inexact supporting quote."},
                        *coverage,
                        {"requirement_id": default, "quote": "Timeout: 60"},
                    ],
                    "defects": [
                        {
                            "path": target,
                            "problem": "Mention that the value is only the default.",
                            "requirement_id": default,
                        }
                    ],
                }
            if mode == "requirements_regression":
                return {
                    "action": "revise",
                    "coverage": [
                        {"requirement_id": default, "quote": "# Default timeout"},
                    ],
                    "defects": [
                        {
                            "path": target,
                            "problem": "Lost the confirmed heading.",
                            "requirement_id": normal,
                        }
                    ],
                }
            assert context["previous_quality"]["claimed_defect_ids"]
            return {
                "action": "pass",
                "coverage": [
                    *coverage,
                    {"requirement_id": default, "quote": "Timeout: 60 by default"},
                ],
            }
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
        result = await runtime.run(lease, task)
    finally:
        await gateway.aclose()

    expected = {
        "overturn": "abstain",
        "counterevidence": "abstain",
        "unsafe": "unresolved",
        "bare_no_change": "unresolved",
        "docs_only_no_change": "unresolved",
        "spec_no_change": "abstain",
        "ambiguous": "unresolved",
        "editorial_unsupported": "unresolved",
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
    if mode in {"requirements_delta", "requirements_recovery"}:
        assert "by default" not in data["first_draft"]["patch"]
        assert "+# Timeout guide" in result["patch"]
        assert "+Timeout: 60 by default" in result["patch"]
        assert len(data["best_valid_candidate"]["candidate"]["quality"]["covered"]) == 2
    if mode == "scope_expansion":
        assert result["review_status"] == "verified"
        assert "migration.md" not in data["first_draft"]["changed_paths"]
        assert "Compatibility value is 60." in result["patch"]
        assert "+Timeout: 60" in result["patch"]
    if mode in {"requirements_regression", "requirements_false_quote"}:
        assert "+# Timeout guide" in result["patch"]
        assert "+Timeout: 60\n" in result["patch"]
        assert result["review_status"] == "degraded"
    if mode == "exact_range_read":
        assert phases.count("DRAFT") == 1
        assert any(key.startswith("exact-edit-read:") for key in data["tool_metrics"])
    if mode == "editorial":
        assert data["tasks"][0]["documentation_request"]["quote"] == task
        assert data["tasks"][0]["change_source_types"] == {}
    if mode == "editorial_unsupported":
        assert "DRAFT" not in phases
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
    if mode in {"inherit", "choose_change", "spec", "repeat", "redundant_quote"}:
        bound = data["tasks"][0]
        assert bound["change_refs"] == (
            ["C2"] if mode in {"choose_change", "redundant_quote"} else ["C1"]
        )
        assert bound["change_binding"] == ("explicit" if mode == "choose_change" else "inherited")
        source = "trusted_change_spec" if mode == "spec" else "implementation_diff"
        assert list(bound["change_source_types"].values()) == [source]
        assert not data.get("protocol_retries")
    if mode == "repeat":
        assert len(data["research_history"]) == data["repeated_research"] == 1
    if mode == "ambiguous":
        assert "DRAFT" not in phases


def test_change_spec_uses_a_verbatim_assertion_from_the_task_body():
    spec = "The timeout now defaults to 60 seconds."
    linked = "The limit now defaults to 5 requests."
    task = f"Provided task request:\n{spec}\n# Frozen pre-existing linked-source context\n{linked}"
    assert supplied_change_span(task, spec) == (spec, task.index(spec))
    with pytest.raises(DomainError):
        supplied_change_span(task, linked)
    generic = "Document handling errors when the broker uses an alternate port."
    with pytest.raises(DomainError):
        supplied_change_span(generic, generic)
    with pytest.raises(DomainError):
        supplied_change_span(generic, "the broker uses an alternate port.")


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
