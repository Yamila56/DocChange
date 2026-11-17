import asyncio
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from scripts.atomic_report import atomic
from shopresolve.agent.gateway import ModelReply
from shopresolve.config import Settings
from shopresolve.db import make_database
from shopresolve.documents.maintenance import phase_protocol
from shopresolve.documents.maintenance_runtime import MaintenanceRuntime, build_maintenance_payload
from shopresolve.documents.repository import RepositoryRuntime
from shopresolve.errors import DomainError
from shopresolve.models import Base, RunEvent
from shopresolve.runstore import RunStore
from shopresolve.seed import seed
from tests.test_document_repository import repository as repository_fixture  # noqa: F401
from tests.test_maintenance_taskgraph import (
    MaintenanceGateway,
    maintenance_repository,  # noqa: F401
    submit,
)


def test_stage_schemas_reject_cross_phase_fields_and_missing_metadata():
    settings = Settings(_env_file=None, model="deepseek-flash")
    for phase, expected, forbidden in [
        ("PLAN", '"tasks"', '"edits"'),
        ("EDIT", '"edits"', '"defects"'),
        ("VERIFY", '"defects"', '"tasks"'),
        ("REPAIR", '"edits"', '"tasks"'),
    ]:
        payload = build_maintenance_payload(settings, "MAINTENANCE_" + phase, {})
        schema = payload["messages"][0]["content"].split("Schema: ")[1]
        assert expected in schema and forbidden not in schema
    definitions = phase_protocol("EDIT").json_schema()["$defs"]
    assert definitions["ProposeDecision"]["properties"]["edits"]["minItems"] == 1
    assert definitions["NoChangeDecision"]["properties"]["edits"]["maxItems"] == 0
    definitions = phase_protocol("VERIFY").json_schema()["$defs"]
    assert definitions["DefectReviewDecision"]["properties"]["defects"]["minItems"] == 1
    assert definitions["ReadyReviewDecision"]["properties"]["defects"]["maxItems"] == 0
    with pytest.raises(ValidationError):
        phase_protocol("EDIT").validate_python({"kind": "propose", "edits": []})
    for ready, defects in [
        (False, []),
        (
            True,
            [
                {
                    "kind": "MissingEvidence",
                    "requirement_id": "R1",
                    "surface": "reference",
                    "reason": "No evidence",
                }
            ],
        ),
    ]:
        with pytest.raises(ValidationError):
            phase_protocol("VERIFY").validate_python(
                {"kind": "review", "ready": ready, "defects": defects}
            )


@pytest.mark.parametrize("alter_unrelated_cell", [False, True])
def test_repair_scope_checks_every_requirement_and_preserves_other_cells(alter_unrelated_cell):
    cell = {
        "requirement_id": "R1",
        "surface": "reference",
        "status": "edit_required",
        "paths": ["guide.md"],
        "evidence_ids": ["observed"],
        "reason": "timeout",
    }
    other = {**cell, "requirement_id": "R2", "status": "no_change_supported"}
    previous = {
        "path": "guide.md",
        "old": "30",
        "new": "60",
        "operation": "replace",
        "count": 1,
        "requirement_ids": ["R1"],
        "evidence_ids": ["observed"],
    }
    runtime = MaintenanceRuntime(None, None, None, None)
    runtime.data = {
        "candidate": {"edits": [previous], "coverage": [cell, other], "evidence_ids": ["observed"]},
        "review": {
            "defects": [{"requirement_id": "R1", "surface": "reference", "path": "guide.md"}]
        },
    }
    action = phase_protocol("REPAIR").validate_python(
        {
            "kind": "repair",
            "decision": "patch",
            "text": "Fix timeout",
            "edits": [
                previous
                if alter_unrelated_cell
                else {**previous, "new": "90", "requirement_ids": ["R1", "R2"]}
            ],
            "coverage": [
                cell,
                {**other, "reason": "Changed unrelated judgment"}
                if alter_unrelated_cell
                else other,
            ],
        }
    )
    with pytest.raises(DomainError) as error:
        runtime._merge_repair(action)
    assert error.value.code == "REPAIR_SCOPE"
    legal = action.model_copy(
        update={
            "edits": [
                phase_protocol("EDIT")
                .validate_python(
                    {
                        "kind": "propose",
                        "edits": [{**previous, "new": "75"}],
                        "coverage": [cell],
                        "text": "Update timeout",
                        "evidence_ids": ["observed"],
                    }
                )
                .edits[0]
            ],
            "coverage": [],
        }
    )
    merged = runtime._merge_repair(legal)
    assert merged.edits[0].new == "75"
    assert merged.coverage[1].model_dump() == other


@pytest.mark.asyncio
@pytest.mark.parametrize("always_invalid", [False, True])
async def test_protocol_correction_is_bounded_and_charged(
    maintenance_repository,  # noqa: F811
    commerce,
    tmp_path,
    always_invalid,  # noqa: F811
):
    _, env = maintenance_repository
    store = RunStore(commerce[1])
    rid, lease = await submit(store)

    class MalformedGateway(MaintenanceGateway):
        def __init__(self):
            super().__init__()
            self.edits = 0

        async def request(self, phase, context):
            reply = await super().request(phase, context)
            if phase == "MAINTENANCE_EDIT":
                self.edits += 1
                if self.edits > 1:
                    assert context["protocol_feedback"]["errors"]
                if self.edits == 1 or always_invalid:
                    return ModelReply({k: v for k, v in reply.output.items() if k != "text"})
            return reply

    gateway = MalformedGateway()
    runtime = RepositoryRuntime(store, gateway, env, tmp_path / "trace.jsonl")
    if always_invalid:
        with pytest.raises(ValidationError):
            await runtime.run(lease, "Update timeout")
    else:
        assert (await runtime.run(lease, "Update timeout"))["decision"] == "patch"
    run = await store.get("alice", rid)
    assert run["model_calls"] == (3 if always_invalid else 4)
    assert gateway.edits == 2
    async with commerce[1]() as session:
        from shopresolve.models import Run

        row = await session.get(Run, rid)
        state = row.state
    assert state["maintenance"]["protocol_retries"] == {"EDIT": 1}
    assert run["status"] == ("FAILED" if always_invalid else "WAITING_APPROVAL")


@pytest.mark.asyncio
async def test_different_roots_can_search_concurrently_without_duplicate_root_initialization():
    env = SimpleNamespace(roots={"docs": None, "code": None}, indices={})
    runtime = MaintenanceRuntime(None, None, env, None)
    runtime.data = {"plan_step": 1}
    entered, release = asyncio.Event(), asyncio.Event()
    active = set()

    async def tool(step, key, arguments, local, task_id):
        root = arguments["root"]
        assert root not in active
        active.add(root)
        if active == {"docs", "code"}:
            entered.set()
        await release.wait()
        local.indices[root] = root
        active.remove(root)
        return {}

    runtime._tool = tool
    jobs = [
        asyncio.create_task(runtime._discovery_tool(str(i), {"kind": "search", "root": root}))
        for i, root in enumerate(["docs", "code", "docs"])
    ]
    await asyncio.wait_for(entered.wait(), 2)
    release.set()
    await asyncio.gather(*jobs)
    assert env.indices == {"docs": "docs", "code": "code"}


@pytest.mark.asyncio
async def test_cold_implementation_indexing_is_bounded_across_runs():
    entered, release, docs_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
    active, maximum = 0, 0

    async def tool(step, key, arguments, local, task_id):
        nonlocal active, maximum
        if arguments["root"] == "docs":
            docs_done.set()
            return {}
        active += 1
        maximum = max(active, maximum)
        if active == 2:
            entered.set()
        await release.wait()
        local.indices["code"] = object()
        active -= 1
        return {}

    runtimes = []
    for _ in range(4):
        env = SimpleNamespace(roots={"docs": None, "code": None}, indices={})
        runtime = MaintenanceRuntime(None, None, env, None)
        runtime.data = {"plan_step": 1}
        runtime._tool = tool
        runtimes.append(runtime)
    jobs = [
        asyncio.create_task(r._discovery_tool("R1", {"kind": "search", "root": "code"}))
        for r in runtimes[:3]
    ]
    await asyncio.wait_for(entered.wait(), 2)
    await runtimes[3]._discovery_tool("R1", {"kind": "search", "root": "docs"})
    assert docs_done.is_set() and active == 2
    release.set()
    await asyncio.gather(*jobs)
    assert maximum == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("task_sources_only", [False, True])
async def test_policy_source_ids_are_adapted_to_observed_git_evidence(
    maintenance_repository,  # noqa: F811
    commerce,
    tmp_path,
    task_sources_only,  # noqa: F811
):
    _, env = maintenance_repository
    store = RunStore(commerce[1])
    rid, lease = await submit(store)

    class SourceGateway(MaintenanceGateway):
        async def request(self, phase, context):
            reply = await super().request(phase, context)
            if phase == "MAINTENANCE_EDIT":
                sources = [context["task_evidence_id"], context["implementation_evidence_id"]]
                reply.output["evidence_ids"] = sources + (
                    [] if task_sources_only else reply.output["evidence_ids"]
                )
            return reply

    result = await RepositoryRuntime(store, SourceGateway(), env, tmp_path / "trace.jsonl").run(
        lease, "Update timeout"
    )
    assert result["decision"] == "patch"
    assert (await store.get("alice", rid))["model_calls"] == 3


@pytest.mark.asyncio
async def test_sqlite_parallel_receipts_and_heartbeat_keep_unique_events(tmp_path):
    engine, sessions = make_database(f"sqlite+aiosqlite:///{tmp_path / 'concurrent.sqlite'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await seed(sessions)
        store = RunStore(sessions)
        rid, lease = await submit(store)
        step = await store.reserve_model(lease, "MAINTENANCE_PLAN", "hash", 32)

        async def receipt(i):
            identifier, cached = await store.invocation(lease, step, str(i), "read", {"i": i})
            assert cached is None
            await store.save_tool(lease, identifier, {"status": "ok", "i": i})

        await asyncio.gather(
            *(receipt(i) for i in range(12)),
            *(store.heartbeat(lease, seconds=180) for _ in range(12)),
        )
        async with sessions() as session:
            events = (
                await session.scalars(
                    select(RunEvent).where(RunEvent.run_id == rid).order_by(RunEvent.seq)
                )
            ).all()
        assert [e.seq for e in events] == list(range(1, len(events) + 1))
        assert sum(e.kind == "tool.completed" for e in events) == 12
        assert (await store.get("alice", rid))["model_calls"] == 1
    finally:
        await engine.dispose()


def test_atomic_report_retries_windows_sharing_and_preserves_previous_file(tmp_path, monkeypatch):
    target = tmp_path / "summary.json"
    target.write_text('{"old": true}', encoding="utf-8")
    original = Path.replace
    attempts = 0

    def replace(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            assert json.loads(target.read_text()) == {"old": True}
            raise PermissionError("simulated sharing violation")
        return original(source, destination)

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr("scripts.atomic_report.time.sleep", lambda _: None)
    atomic(target, {"new": True})
    assert attempts == 3 and json.loads(target.read_text()) == {"new": True}
    assert not list(tmp_path.glob("*.tmp"))

    attempts = 0

    def always_locked(*_):
        nonlocal attempts
        attempts += 1
        raise PermissionError("still locked")

    monkeypatch.setattr(Path, "replace", always_locked)
    with pytest.raises(PermissionError):
        atomic(target, {"uncommitted": True})
    assert attempts == 20 and json.loads(target.read_text()) == {"new": True}
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.asyncio
async def test_restored_deadline_cannot_grant_a_fresh_execution_window(
    maintenance_repository,  # noqa: F811
    commerce,
    tmp_path,  # noqa: F811
):
    _, env = maintenance_repository
    store = RunStore(commerce[1])
    rid, lease = await submit(store)
    state, _, _ = await store.load(lease)
    state["maintenance"] = {
        "protocol_version": "maintenance-v2h1",
        "deadline_unix_ms": time.time_ns() // 1000000 - 1000,
    }
    await store.save(lease, state)
    gateway = MaintenanceGateway()
    with pytest.raises(DomainError, match="deadline"):
        await RepositoryRuntime(store, gateway, env, tmp_path / "trace.jsonl").run(lease, "task")
    assert (await store.get("alice", rid))["stop_reason"] == "TIME_BUDGET"
    assert gateway.phases == []


@pytest.mark.asyncio
async def test_heartbeat_failure_is_a_task_error_not_controller_cancellation(monkeypatch):
    env = SimpleNamespace(cancel_event=threading.Event())

    async def fail(*_, **__):
        raise RuntimeError("database unavailable")

    class WaitingRuntime(RepositoryRuntime):
        async def _run(self, lease, prompt):
            await asyncio.Event().wait()

    original_sleep = asyncio.sleep

    async def fast_heartbeat(delay):
        await original_sleep(0 if delay == 45 else delay)

    monkeypatch.setattr("shopresolve.documents.repository.asyncio.sleep", fast_heartbeat)
    runtime = WaitingRuntime(SimpleNamespace(heartbeat=fail), None, env, None)
    with pytest.raises(DomainError) as error:
        await runtime.run(None, "task")
    assert error.value.code == "LEASE_HEARTBEAT"
    assert env.cancel_event.is_set()
