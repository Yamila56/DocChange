import json

import pytest

from shopresolve.agent.gateway import ModelReply
from shopresolve.config import Settings
from shopresolve.documents.maintenance_batched_protocol import BATCHED_POLICY, build_batched_payload
from shopresolve.documents.repository import RepositoryEnvironment, RepositoryRuntime
from shopresolve.models import Run
from shopresolve.runstore import RunStore
from tests.test_document_repository import git, repository  # noqa: F401
from tests.test_maintenance_taskgraph import submit


def question(*paths):
    return {"question": "Find documented timeout behavior", "root": "docs", "paths": list(paths)}


def sources(context):
    return {e["path"]: e["evidence_id"] for e in context["evidence"]}


@pytest.mark.asyncio
async def test_reviewer_research_can_add_a_material_change_without_releasing_draft(
    repository,  # noqa: F811
    commerce,
    tmp_path,
):
    root, _ = repository
    migration = root / "migration.md"
    migration.write_text("# Migration\nDefault timeout was 30.\n", encoding="utf-8")
    git(root, "add", "migration.md")
    git(
        root,
        "-c",
        "user.name=Smoke",
        "-c",
        "user.email=smoke@example.invalid",
        "commit",
        "-qm",
        "migration",
    )
    env = RepositoryEnvironment(root, None, tmp_path / "controller", retrieval_mode="section_bm25")
    store = RunStore(commerce[1])
    rid, lease = await submit(store, policy=BATCHED_POLICY)

    class Gateway:
        discovered = False
        phases = []

        async def request(self, phase, context):
            phase = phase.rsplit("_", 1)[-1]
            self.phases.append(phase)
            refs = sources(context)
            if phase == "REASON" and not context["research_history"]:
                return ModelReply(
                    {"kind": "investigate", "questions": [question("guide.mdx", "obsolete.rst")]}
                )
            if phase == "REASON":
                self.discovered = "migration.md" in refs
                targets = [
                    {
                        "id": "guide",
                        "description": "Update guide timeout",
                        "evidence_ids": [refs["guide.mdx"]],
                    }
                ]
                if self.discovered:
                    targets.append(
                        {
                            "id": "migration",
                            "description": "Update migration timeout",
                            "evidence_ids": [refs["migration.md"]],
                        }
                    )
                return ModelReply({"kind": "ready_to_edit", "targets": targets})
            if phase == "EDIT":
                edits = [
                    {
                        "path": "guide.mdx",
                        "old": "Timeout: 30",
                        "new": "Timeout: 60",
                        "requirement_ids": ["guide"],
                        "evidence_ids": [refs["guide.mdx"]],
                    }
                ]
                if self.discovered:
                    edits.append(
                        {
                            "path": "migration.md",
                            "old": "was 30",
                            "new": "is 60",
                            "requirement_ids": ["migration"],
                            "evidence_ids": [refs["migration.md"]],
                        }
                    )
                return ModelReply(
                    {"kind": "propose", "text": "Synchronize timeout", "edits": edits}
                )
            assert "obsolete.rst" in {e["path"] for e in context["challenge_evidence"]}
            if not self.discovered:
                assert (await store.get("alice", rid))["status"] == "RUNNING"
                return ModelReply({"kind": "investigate", "questions": [question("migration.md")]})
            return ModelReply({"kind": "pass"})

    gateway = Gateway()
    result = await RepositoryRuntime(store, gateway, env, tmp_path / "events.jsonl").run(
        lease, "Use timeout 60"
    )
    assert result["decision"] == "patch" and result["changed_paths"] == [
        "guide.mdx",
        "migration.md",
    ]
    assert git(root, "status", "--porcelain") == b""
    assert gateway.phases == ["REASON", "REASON", "EDIT", "VERIFY", "REASON", "EDIT", "VERIFY"]
    async with commerce[1]() as session:
        run = await session.get(Run, rid)
        assert run.status == "WAITING_APPROVAL"
        state = run.state["maintenance"]
        assert [r["trigger"] for r in state["research_history"]] == ["REASON", "VERIFY"]
        assert state["research_history"][1]["new_path_count"] == 1
        assert any(n["type"] == "research_question" for n in state["graph"]["nodes"])


@pytest.mark.asyncio
async def test_repeat_intent_stops_without_another_tool_batch(repository, commerce, tmp_path):  # noqa: F811
    _, env = repository
    store = RunStore(commerce[1])
    rid, lease = await submit(store, policy=BATCHED_POLICY)

    class Gateway:
        async def request(self, _, context):
            q = question("guide.mdx")
            if context["research_history"]:
                q["question"] = "Same path, new wording"
            return ModelReply({"kind": "investigate", "questions": [q]})

    trace = tmp_path / "events.jsonl"
    result = await RepositoryRuntime(store, Gateway(), env, trace).run(lease, "Investigate timeout")
    assert result["decision"] == "unresolved" and result["reason_code"] == "SEARCH_SATURATED"
    assert len(trace.read_text().splitlines()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("out_of_scope", [False, True])
async def test_local_repair_preserves_other_targets(repository, commerce, tmp_path, out_of_scope):  # noqa: F811
    _, env = repository
    store = RunStore(commerce[1])
    rid, lease = await submit(store, policy=BATCHED_POLICY)
    edits = [
        ("guide", "guide.mdx", "Timeout: 30", "Timeout: 60"),
        ("legacy", "obsolete.rst", "Old guide", "Legacy guide"),
    ]

    class Gateway:
        repaired = False

        async def request(self, phase, context):
            role = phase.rsplit("_", 1)[-1]
            refs = sources(context)
            if role == "REASON" and not refs:
                return ModelReply(
                    {"kind": "investigate", "questions": [question("guide.mdx", "obsolete.rst")]}
                )
            if role == "REASON":
                return ModelReply(
                    {
                        "kind": "ready_to_edit",
                        "targets": [
                            {
                                "id": rid,
                                "description": "Update " + path,
                                "evidence_ids": [refs[path]],
                            }
                            for rid, path, _, _ in edits
                        ],
                    }
                )
            if role in {"EDIT", "REPAIR"}:
                changes = edits
                if role == "REPAIR":
                    self.repaired = True
                    changes = [edits[1] if out_of_scope else (*edits[0][:3], "Timeout: 90")]
                return ModelReply(
                    {
                        "kind": "repair" if role == "REPAIR" else "propose",
                        "text": "Update target",
                        "edits": [
                            {
                                "path": path,
                                "old": old,
                                "new": new,
                                "requirement_ids": [rid],
                                "evidence_ids": [refs[path]],
                            }
                            for rid, path, old, new in changes
                        ],
                    }
                )
            if self.repaired:
                return ModelReply({"kind": "pass"})
            return ModelReply(
                {
                    "kind": "local_repair",
                    "defects": [
                        {
                            "requirement_id": "guide",
                            "path": "guide.mdx",
                            "reason": "Use timeout 90",
                        }
                    ],
                }
            )

    result = await RepositoryRuntime(store, Gateway(), env, tmp_path / "events.jsonl").run(
        lease, "Synchronize documentation"
    )
    if out_of_scope:
        assert result["decision"] == "unresolved" and result["reason_code"] == "REPAIR_SCOPE"
        assert "patch" not in result
    else:
        assert result["changed_paths"] == ["guide.mdx", "obsolete.rst"]
        assert "+Timeout: 90" in result["patch"] and "+Legacy guide" in result["patch"]


def test_selective_thinking_is_explicit_and_challenges_yield_to_context_budget():
    settings = Settings(
        _env_file=None,
        provider="deepseek",
        model="deepseek-flash",
        model_max_input_bytes=20000,
        model_max_output_tokens=8192,
    )
    for role, expected in [
        ("REASON", "enabled"),
        ("VERIFY", "enabled"),
        ("EDIT", "disabled"),
        ("REPAIR", "disabled"),
    ]:
        payload = build_batched_payload(
            settings,
            "MAINTENANCE_BATCHED_" + role,
            {
                "selective_thinking": True,
                "evidence": [{"content": "keep"}],
                "challenge_evidence": [{"content": "x" * 20000}],
            },
        )
        assert payload["thinking"]["type"] == expected
        assert payload["max_tokens"] == (8192 if expected == "enabled" else 4096)
        data = json.loads(payload["messages"][1]["content"])
        assert data["evidence"] == [{"content": "keep"}] and data["challenge_evidence"] == []
