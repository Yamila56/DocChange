from uuid import uuid4

import httpx
import pytest

from shopresolve.api import create_app
from shopresolve.config import Settings
from shopresolve.documents.runtime import DocumentDemoGateway, DocumentRuntime
from shopresolve.documents.service import DocumentService
from shopresolve.errors import DomainError
from shopresolve.runstore import RunStore

DOCS = {
    "guide.md": {"role": "document", "content": "# Guide\n\nUse user_id.\n[API](api.md)\n"},
    "api.md": {"role": "document", "content": "# API\n\nuser_id required. Timeout 30.\n"},
    "release.md": {"role": "source", "content": "# V2\nuser_id is now account_id.\n"},
    "v1.md": {"role": "archive", "content": "# History\nuser_id was required.\n"},
}


async def submit(
    sessions, clock, wid, *, mode="update", text="user_id", old="user_id", new="account_id"
):
    store = RunStore(sessions, clock)
    conversation = await store.conversation("alice", str(uuid4()))
    cfg = {
        "provider": "demo",
        "model": "demo-v1",
        "domain": "document",
        "model_limit": 12,
        "active_budget_ms": 120000,
        "model_timeout_seconds": 30,
        "document": {
            "workspace_id": wid,
            "revision": 1,
            "mode": mode,
            "text": text,
            "old": old,
            "new": new,
        },
    }
    response = await store.submit(
        "alice",
        conversation["id"],
        {"text": text, "client_message_id": str(uuid4())},
        str(uuid4()),
        cfg,
    )
    lease = await store.claim(response["run_id"], "doc-test", lease_seconds=120)
    await DocumentRuntime(store, DocumentDemoGateway()).run(lease)
    return await store.get("alice", response["run_id"])


@pytest.mark.asyncio
async def test_document_publish_replay_and_snapshot(commerce):
    _, sessions, clock = commerce
    service = DocumentService(sessions)
    workspace = await service.create("alice", "test", DOCS, "create-doc")
    run = await submit(sessions, clock, workspace["id"])
    assert run["status"] == "WAITING_APPROVAL", run
    change = await service.change("alice", run["changeset_id"])
    before = await service.get("alice", workspace["id"])
    assert before["revision"] == 1
    result = await service.decide("alice", change["id"], True, change["payload_hash"], "approve")
    assert result == await service.decide(
        "alice", change["id"], True, change["payload_hash"], "approve"
    )
    after = await service.get("alice", workspace["id"])
    assert after["revision"] == 2
    assert "account_id" in after["documents"]["api.md"]["content"]
    assert after["documents"]["v1.md"] == DOCS["v1.md"]
    assert (await service.get("alice", workspace["id"], 1))["documents"] == DOCS
    assert await service.search("alice", workspace["id"], 2, "account_id", exact=True)
    assert (await RunStore(sessions, clock).get("alice", run["id"]))["status"] == "SUCCEEDED"


@pytest.mark.asyncio
async def test_document_reject_stale_and_owner(commerce):
    _, sessions, clock = commerce
    service = DocumentService(sessions)
    w = await service.create("alice", "test", DOCS, "create-doc")
    rejected = await submit(sessions, clock, w["id"])
    c = await service.change("alice", rejected["changeset_id"])
    await service.decide("alice", c["id"], False, c["payload_hash"], "reject")
    assert (await service.get("alice", w["id"]))["documents"] == DOCS
    a = await submit(sessions, clock, w["id"])
    b = await submit(sessions, clock, w["id"])
    ca = await service.change("alice", a["changeset_id"])
    cb = await service.change("alice", b["changeset_id"])
    await service.decide("alice", ca["id"], True, ca["payload_hash"], "a")
    assert (await service.decide("alice", cb["id"], True, cb["payload_hash"], "b"))[
        "status"
    ] == "STALE"
    with pytest.raises(DomainError, match="Workspace not found"):
        await service.get("bob", w["id"])
    with pytest.raises(DomainError):
        await service.change("bob", ca["id"])


@pytest.mark.asyncio
async def test_document_query_missing_and_link_validation(commerce):
    _, sessions, clock = commerce
    service = DocumentService(sessions)
    w = await service.create("alice", "test", DOCS, "create-doc")
    query = await submit(sessions, clock, w["id"], mode="query")
    assert query["status"] == "SUCCEEDED" and query["evidence"]
    assert query["changeset_id"] is None
    missing = await submit(sessions, clock, w["id"], old="missing-token")
    assert missing["status"] == "WAITING_USER"
    broken = await submit(sessions, clock, w["id"], old="api.md", new="missing.md")
    assert broken["status"] == "FAILED" and broken["changeset_id"] is None


@pytest.mark.asyncio
async def test_document_http_and_idempotency(commerce):
    _, sessions, clock = commerce
    app = create_app(
        Settings(_env_file=None, enable_demo=True, demo_code="test"), sessions=sessions, clock=clock
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        login = await client.post(
            "/v1/demo-sessions", json={"account": "alice", "demo_code": "test"}
        )
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        headers = {"Idempotency-Key": "create"}
        payload = {"name": "HTTP", "documents": DOCS}
        created = await client.post("/v1/documents/workspaces", headers=headers, json=payload)
        assert created.status_code == 201, created.text
        assert (
            await client.post("/v1/documents/workspaces", headers=headers, json=payload)
        ).json() == created.json()
        url = f"/v1/documents/workspaces/{created.json()['id']}/tasks"
        body = {"text": "migrate", "mode": "update", "old": "user_id", "new": "account_id"}
        task = await client.post(url, headers={"Idempotency-Key": "task"}, json=body)
        assert task.status_code == 202, task.text
        assert (
            await client.post(url, headers={"Idempotency-Key": "task"}, json=body)
        ).json() == task.json()
        store = RunStore(sessions, clock)
        lease = await store.claim(task.json()["run_id"], "http-smoke", lease_seconds=120)
        await DocumentRuntime(store, DocumentDemoGateway()).run(lease)
        result = await client.get("/v1/runs/" + task.json()["run_id"])
        assert result.json()["status"] == "WAITING_APPROVAL", result.text


@pytest.mark.asyncio
async def test_document_gateway_read_batch_never_salvages_writes():
    import json

    from shopresolve.documents.runtime import DocumentGateway

    outputs = [
        '{"kind":"read","path":"a.md"}\n{"kind":"read","path":"b.md"}',
        '{"kind":"read","path":"a.md"}\n{"kind":"propose","edits":[]}',
    ]

    def provider(request):
        payload = json.loads(request.content)
        assert "DocChange" in payload["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": outputs.pop(0)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            },
        )

    settings = Settings(
        _env_file=None,
        provider="deepseek",
        model="deepseek-flash",
        model_base_url="https://api.deepseek.com",
        model_api_key="test-placeholder",
        allow_paid_model=True,
    )
    gateway = DocumentGateway(settings, transport=httpx.MockTransport(provider))
    try:
        assert (await gateway.request("DOCUMENT", {})).output == {
            "kind": "read_many",
            "paths": ["a.md", "b.md"],
        }
        with pytest.raises(json.JSONDecodeError):
            await gateway.request("DOCUMENT", {})
    finally:
        await gateway.aclose()


@pytest.mark.asyncio
async def test_document_checkpoint_replays_saved_decision(commerce):
    from shopresolve.agent.gateway import ScriptedGateway

    _, sessions, clock = commerce
    service, store = DocumentService(sessions), RunStore(sessions, clock)
    w = await service.create("alice", "recovery", DOCS, "recover")
    c = await store.conversation("alice", "recover")
    cfg = {
        "domain": "document",
        "provider": "demo",
        "model": "demo-v1",
        "model_limit": 8,
        "active_budget_ms": 120000,
        "model_timeout_seconds": 30,
        "document": {"workspace_id": w["id"], "revision": 1, "mode": "query", "text": "user_id"},
    }
    rid = (
        await store.submit(
            "alice", c["id"], {"text": "query", "client_message_id": "recover"}, "recover", cfg
        )
    )["run_id"]
    lease = await store.claim(rid, "recover", lease_seconds=180)
    state, _, _ = await store.load(lease)
    seq = await store.reserve_model(lease, "DOCUMENT", "test", 8)
    await store.save_decision(lease, state, seq, {"kind": "read", "path": "api.md"})
    snap = await service.get("alice", w["id"])
    eid = next(e["id"] for e in snap["index"]["chunks"] if e["path"] == "api.md")
    gateway = ScriptedGateway(
        [{"kind": "answer", "text": "user_id required", "evidence_ids": [eid]}]
    )
    await DocumentRuntime(store, gateway).run(lease)
    assert gateway.calls == 1  # The persisted read decision did not call the model again.
    assert (await store.get("alice", rid))["status"] == "SUCCEEDED"
