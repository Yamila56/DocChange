import asyncio
from uuid import uuid4

import pytest

from shopresolve.agent.gateway import ModelReply
from shopresolve.documents.maintenance import task_layers
from shopresolve.documents.maintenance_discovery import discover
from shopresolve.documents.maintenance_runtime import MaintenanceRuntime
from shopresolve.documents.model_profile import pro_settings
from shopresolve.documents.repository import (
    RepositoryEnvironment,
    RepositoryGateway,
    RepositoryRuntime,
)
from shopresolve.errors import DomainError
from shopresolve.models import Run
from shopresolve.runstore import RunStore
from tests.test_document_repository import repository as repository_fixture  # noqa: F401


@pytest.fixture
def maintenance_repository(repository_fixture):  # noqa: F811
    root, environment = repository_fixture
    environment.retrieval_mode = "section_bm25"
    return root, environment


def test_task_graph_layers_are_deterministic_and_validate_dependencies():
    tasks = [
        {"id": "B", "dependencies": ["A"]},
        {"id": "A", "dependencies": []},
        {"id": "C", "dependencies": []},
    ]
    assert task_layers(tasks) == [["A", "C"], ["B"]]
    for invalid in (
        [{"id": "A", "dependencies": ["missing"]}],
        [{"id": "A", "dependencies": ["A"]}],
        [{"id": "A", "dependencies": ["B"]}, {"id": "B", "dependencies": ["A"]}],
    ):
        with pytest.raises(DomainError):
            task_layers(invalid)


@pytest.mark.asyncio
async def test_discovery_uses_bounded_parallel_layers_without_model_decisions():
    active, maximum = 0, 0
    entered, release = asyncio.Event(), asyncio.Event()
    finished = set()
    tasks = [
        {"id": key, "dependencies": deps, "queries": [key], "code_queries": [], "path_hints": []}
        for key, deps in [("A", []), ("B", []), ("C", ["A", "B"])]
    ]

    async def execute(key, arguments):
        nonlocal active, maximum
        if key == "C":
            assert finished == {"A", "B"}
        active += 1
        maximum = max(maximum, active)
        if maximum == 2:
            entered.set()
        if key in {"A", "B"}:
            await release.wait()
        active -= 1
        finished.add(key)
        return {"results": []}

    running = asyncio.create_task(discover(tasks, execute, {"docs": []}, concurrency=2))
    await asyncio.wait_for(entered.wait(), 3)
    release.set()
    result = await asyncio.wait_for(running, 3)
    assert maximum == 2
    assert all(row["status"] == "unresolved" for row in result.values())


@pytest.mark.asyncio
async def test_discovery_keeps_rank_and_reads_a_valid_exact_hint_within_the_two_path_limit():
    doc_hits = [
        {"path": path, "evidence_id": key, "content": key, "start_line": line}
        for path, key, line in [
            ("docs/overview.md", "ff-top", 20),
            ("docs/config.md", "00-second", 8),
            ("docs/overview.md", "11-other-section", 50),
        ]
    ]
    reads = []

    async def execute(_key, args):
        if args["kind"] == "search":
            return {
                "results": doc_hits
                if args["root"] == "docs"
                else [{"path": "impl.py", "evidence_id": "bb-code", "content": "implementation"}]
            }
        reads.append(args)
        return {
            "files": [
                {"evidence_id": "read-" + r["path"], "content": r["path"]} for r in args["reads"]
            ]
        }

    task = {
        "id": "R1",
        "dependencies": [],
        "queries": ["auth"],
        "code_queries": ["auth"],
        "path_hints": ["docs/*.md", "docs/auth.md", "../private.md"],
    }
    paths = {"docs": ["docs/config.md", "docs/auth.md", "docs/overview.md"]}
    result = (await discover([task], execute, paths))["R1"]
    assert reads[0]["reads"] == [
        {"path": "docs/overview.md", "offset": 15, "limit": 120},
        {"path": "docs/auth.md", "offset": 0, "limit": 120},
    ]
    assert result["evidence_ids"][:3] == ["ff-top", "00-second", "11-other-section"]
    assert result["editor_evidence_ids"] == [
        "read-docs/overview.md",
        "read-docs/auth.md",
        "ff-top",
        "bb-code",
    ]
    runtime = MaintenanceRuntime(None, None, None, None)
    runtime.data = {
        "task_id": "t",
        "implementation_id": "i",
        "implementation": {},
        "tasks": [task],
        "discovery": {"R1": result},
        "evidence": {key: {"evidence_id": key, "content": key} for key in result["evidence_ids"]},
    }
    assert [e["evidence_id"] for e in runtime._context("EDIT", "auth")["evidence"]] == (
        result["editor_evidence_ids"]
    )


class MaintenanceGateway:
    def __init__(
        self, *, no_change=False, uncertain=False, missing=False, reject=0, extra_edit=False
    ):
        self.no_change, self.uncertain, self.missing = no_change, uncertain, missing
        self.reject, self.extra_edit = reject, extra_edit
        self.reviews = 0
        self.phases = []

    async def request(self, phase, context):
        self.phases.append(phase)
        if phase == "MAINTENANCE_PLAN":
            return ModelReply(
                {
                    "kind": "plan",
                    "impact": {
                        "kind": "uncertain"
                        if self.uncertain
                        else "internal_only"
                        if self.no_change
                        else "public_change",
                        "evidence_ids": [context["task_evidence_id"]],
                        "reason": "Task requests timeout update",
                    },
                    "tasks": [
                        {
                            "id": "R1",
                            "description": "Check timeout reference",
                            "evidence_ids": [context["task_evidence_id"]],
                            "surfaces": ["reference"],
                            "queries": ["absent-symbol-xyz" if self.missing else "Timeout"],
                        }
                    ],
                }
            )
        if phase == "MAINTENANCE_VERIFY":
            self.reviews += 1
            assert "observations" not in context and context["evidence"]
            ready = self.reviews > self.reject
            return ModelReply(
                {
                    "kind": "review",
                    "ready": ready,
                    "defects": []
                    if ready
                    else [
                        {
                            "kind": "UnsupportedEdit",
                            "requirement_id": "R1",
                            "surface": "reference",
                            "path": "guide.mdx",
                            "reason": "Clarify timeout value",
                        }
                    ],
                }
            )
        evidence = next(
            (e["evidence_id"] for e in context["evidence"] if e.get("root")),
            context["task_evidence_id"],
        )
        edits = (
            []
            if self.no_change
            else [
                {
                    "path": "guide.mdx",
                    "old": "30",
                    "new": "60",
                    "requirement_ids": ["R1"],
                    "evidence_ids": [evidence],
                }
            ]
        )
        if phase == "MAINTENANCE_REPAIR" and self.extra_edit:
            edits.append(
                {
                    "path": "obsolete.rst",
                    "old": "Old",
                    "new": "New",
                    "requirement_ids": ["R1"],
                    "evidence_ids": [evidence],
                }
            )
        output = {
            "kind": "no_change" if self.no_change else "propose",
            "edits": edits,
            "evidence_ids": [evidence],
            "text": "Supported maintenance decision",
            "coverage": [
                {
                    "requirement_id": "R1",
                    "surface": "reference",
                    "status": "no_change_supported" if self.no_change else "edit_required",
                    "paths": ["guide.mdx"],
                    "evidence_ids": [evidence],
                    "reason": "Observed documentation supports the decision",
                }
            ],
        }
        if phase == "MAINTENANCE_REPAIR":
            output.update(kind="repair", decision="no_change" if self.no_change else "patch")
            output.pop("evidence_ids")
        return ModelReply(output)


async def submit(store, policy="taskgraph-v2", model_limit=8):
    conversation = await store.conversation("alice", str(uuid4()))
    run = await store.submit(
        "alice",
        conversation["id"],
        {"text": "Update timeout", "client_message_id": str(uuid4())},
        str(uuid4()),
        {
            "provider": "demo",
            "domain": "repository",
            "repository_policy": policy,
            "model_limit": model_limit,
        },
    )
    return run["run_id"], await store.claim(run["run_id"], "maintenance-test", lease_seconds=180)


@pytest.mark.asyncio
async def test_taskgraph_happy_path_uses_three_model_calls_and_persists_graph(
    maintenance_repository,
    commerce,
    tmp_path,  # noqa: F811
):
    root, env = maintenance_repository
    store = RunStore(commerce[1])
    rid, lease = await submit(store)
    gateway = MaintenanceGateway()
    result = await RepositoryRuntime(store, gateway, env, tmp_path / "trace.jsonl").run(
        lease, "Update timeout"
    )
    assert result["decision"] == "patch" and "60" in result["patch"]
    run = await store.get("alice", rid)
    assert run["model_calls"] == 3 and run["status"] == "WAITING_APPROVAL"
    assert "Timeout: 30" in (root / "guide.mdx").read_text()
    async with commerce[1]() as session:
        saved = await session.get(Run, rid)
        data = saved.state["maintenance"]
        assert isinstance(data["deadline_unix_ms"], int) and "deadline_unix" not in data
        assert isinstance(data["discovery_elapsed_ms"], int)
        assert all(isinstance(t["elapsed_ms"], int) for t in data["tool_metrics"].values())
        assert data["phase"] == "FINALIZE" and data["discovery"]["R1"]["status"] == "evidence_found"
        assert {n["type"] for n in data["graph"]["nodes"]} == {
            "requirement",
            "evidence",
            "coverage",
            "edit",
            "review",
        }
        assert any(edge["type"] == "cited_by" for edge in data["graph"]["edges"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "uncertain,missing,expected",
    [
        (False, False, None),
        (True, False, "UNSUPPORTED_ABSTENTION"),
        (False, True, "MISSING_EVIDENCE"),
    ],
)
async def test_no_change_is_supported_or_fails_unresolved(
    maintenance_repository,
    commerce,
    tmp_path,
    uncertain,
    missing,
    expected,  # noqa: F811
):
    _, env = maintenance_repository
    store = RunStore(commerce[1])
    rid, lease = await submit(store)
    runtime = RepositoryRuntime(
        store,
        MaintenanceGateway(no_change=True, uncertain=uncertain, missing=missing),
        env,
        tmp_path / "trace.jsonl",
    )
    if expected:
        result = await runtime.run(lease, "Check timeout without changes")
        assert result["decision"] == "unresolved" and result["reason_code"] == expected
    else:
        result = await runtime.run(lease, "Check timeout without changes")
        assert result["decision"] == "abstain" and result["semantic_decision"] == "no_change"
    run = await store.get("alice", rid)
    assert run["status"] == "SUCCEEDED"
    assert run["outcome"] == ("UNRESOLVED" if expected else "DECLINED")


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["unresolved", "updated_impact", "coverage_missing"])
async def test_semantic_stop_and_impact_after_discovery(
    maintenance_repository,
    commerce,
    tmp_path,
    scenario,  # noqa: F811
):
    _, env = maintenance_repository
    store = RunStore(commerce[1])
    rid, lease = await submit(store)

    class Gateway(MaintenanceGateway):
        async def request(self, phase, context):
            reply = await super().request(phase, context)
            if phase == "MAINTENANCE_PLAN" and scenario == "coverage_missing":
                reply.output["tasks"][0]["surfaces"].append("migration")
            if phase == "MAINTENANCE_EDIT" and scenario == "unresolved":
                return ModelReply({"kind": "unresolved", "text": "Missing version history"})
            if phase == "MAINTENANCE_EDIT" and scenario == "updated_impact":
                reply.output["impact"] = {
                    "kind": "internal_only",
                    "evidence_ids": reply.output["evidence_ids"],
                    "reason": "Only internal timekeeping is affected",
                }
            return reply

    gateway = Gateway(no_change=True, uncertain=True)
    result = await RepositoryRuntime(store, gateway, env, tmp_path / "trace.jsonl").run(
        lease, "Check timeout"
    )
    if scenario == "updated_impact":
        assert result["decision"] == "abstain"
        assert result["impact_assessment"]["kind"] == "internal_only"
    else:
        assert result["decision"] == "unresolved" and "patch" not in result
        assert result["reason_code"] == (
            "MODEL_UNRESOLVED" if scenario == "unresolved" else "INCOMPLETE_COVERAGE"
        )
        assert gateway.phases.count("MAINTENANCE_EDIT") == (1 if scenario == "unresolved" else 2)
    assert (await store.get("alice", rid))["status"] == "SUCCEEDED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reject,extra_edit,expected",
    [(1, False, None), (2, False, "UNRESOLVED_AFTER_REVIEW"), (1, True, "REPAIR_SCOPE")],
)
async def test_one_structured_repair_and_path_scope(
    maintenance_repository,
    commerce,
    tmp_path,
    reject,
    extra_edit,
    expected,  # noqa: F811
):
    _, env = maintenance_repository
    store = RunStore(commerce[1])
    rid, lease = await submit(store)
    gateway = MaintenanceGateway(reject=reject, extra_edit=extra_edit)
    runtime = RepositoryRuntime(store, gateway, env, tmp_path / "trace.jsonl")
    result = await runtime.run(lease, "Update timeout")
    assert result["decision"] == ("unresolved" if expected else "patch")
    assert result.get("reason_code") == expected
    assert gateway.phases.count("MAINTENANCE_REPAIR") == (2 if extra_edit else 1)
    assert (await store.get("alice", rid))["model_calls"] <= 5


def test_reviewer_preserves_all_cited_evidence_without_author_trace():
    runtime = MaintenanceRuntime(None, None, None, None)
    original = "observed source\n" * 700
    runtime.data = {
        "task_id": "t",
        "implementation_id": "i",
        "implementation": {},
        "tasks": [],
        "evidence": {"old": {"evidence_id": "old", "content": original}},
        "candidate": {"evidence_ids": ["old"], "edits": [], "coverage": []},
    }
    context = runtime._context("VERIFY", "task")
    assert context["evidence"][0]["content"] == original
    gateway = RepositoryGateway(pro_settings(_env_file=None, model_api_key="unit-test-only"))
    payload = gateway.build_payload("MAINTENANCE_VERIFY", context)
    assert (
        original
        in __import__("json").loads(payload["messages"][1]["content"])["evidence"][0]["content"]
    )


@pytest.mark.asyncio
async def test_changed_source_blocks_finalization(maintenance_repository, commerce, tmp_path):  # noqa: F811
    root, env = maintenance_repository
    store = RunStore(commerce[1])
    rid, lease = await submit(store)

    class ChangingGateway(MaintenanceGateway):
        async def request(self, phase, context):
            reply = await super().request(phase, context)
            if phase == "MAINTENANCE_VERIFY":
                (root / "guide.mdx").write_text("# Guide\nTimeout: 31\n", encoding="utf-8")
            return reply

    with pytest.raises(DomainError) as error:
        await RepositoryRuntime(store, ChangingGateway(), env, tmp_path / "trace.jsonl").run(
            lease, "Update timeout"
        )
    assert error.value.code == "VERSION_CONFLICT"
    assert (await store.get("alice", rid))["status"] == "FAILED"


@pytest.mark.asyncio
async def test_restart_reuses_discovery_receipts_and_restores_observed_ranges(
    maintenance_repository,
    commerce,
    tmp_path,  # noqa: F811
):
    root, env = maintenance_repository

    class ProcessDeath(BaseException):
        pass

    class CrashingStore(RunStore):
        async def save_tool(self, lease, identifier, result):
            await super().save_tool(lease, identifier, result)
            if result.get("files"):
                raise ProcessDeath()

    store = CrashingStore(commerce[1])
    rid, lease = await submit(store)
    gateway = MaintenanceGateway()
    with pytest.raises(ProcessDeath):
        await RepositoryRuntime(store, gateway, env, tmp_path / "trace.jsonl").run(
            lease, "Update timeout"
        )
    async with commerce[1].begin() as session:
        row = await session.get(Run, rid)
        row.lease_until = 0
    store = RunStore(commerce[1])
    lease = await store.claim(rid, "restored-worker", lease_seconds=180)
    restored = RepositoryEnvironment(
        root, None, tmp_path / "restored-controller", retrieval_mode="section_bm25"
    )
    original_execute = restored.execute
    executed = []

    def execute(action):
        executed.append(action.kind)
        return original_execute(action)

    restored.execute = execute
    result = await RepositoryRuntime(store, gateway, restored, tmp_path / "trace.jsonl").run(
        lease, "Update timeout"
    )
    assert result["decision"] == "patch" and executed == ["propose"]
    assert (await store.get("alice", rid))["model_calls"] == 3
