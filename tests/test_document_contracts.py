from uuid import uuid4

import pytest

from scripts.evaluate_documents import DOCUMENTS, execute
from shopresolve.documents.service import DocumentService
from shopresolve.errors import DomainError


@pytest.mark.asyncio
async def test_scope_contract_and_impact(commerce):
    _, sessions, _ = commerce
    service = DocumentService(sessions)
    w = await service.create("alice", "scoped", DOCUMENTS, str(uuid4()))
    run = await execute(
        sessions,
        service,
        w["id"],
        {
            "mode": "update",
            "old": "user_id",
            "new": "account_id",
            "editable_paths": ["api.md"],
            "required_absent": {"api.md": ["user_id"]},
            "required_present": {"api.md": ["account_id", "Timeout 30"]},
        },
    )
    assert run["status"] == "WAITING_APPROVAL", run
    change = await service.change("alice", run["changeset_id"])
    assert change["impact"]["changed_paths"] == ["api.md"]
    await service.decide("alice", change["id"], True, change["payload_hash"], "publish")
    after = await service.get("alice", w["id"])
    assert after["documents"]["guide.md"] == DOCUMENTS["guide.md"]


@pytest.mark.asyncio
async def test_postcondition_blocks_incomplete_change(commerce):
    _, sessions, _ = commerce
    service = DocumentService(sessions)
    w = await service.create("alice", "incomplete", DOCUMENTS, str(uuid4()))
    run = await execute(
        sessions,
        service,
        w["id"],
        {
            "mode": "update",
            "old": "user_id",
            "new": "account_id",
            "required_present": {"api.md": ["must-have-v2-header"]},
        },
    )
    assert run["status"] == "FAILED"
    assert (await service.get("alice", w["id"]))["revision"] == 1


def test_contract_rejects_unknown_or_readonly_scope():
    for path in ("../secret", "release.md", "history.md"):
        with pytest.raises(DomainError):
            DocumentService.validate_contract(DOCUMENTS, {"editable_paths": [path]})
