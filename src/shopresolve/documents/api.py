from typing import Literal

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, ConfigDict, Field

from shopresolve.service import digest

from .service import DocumentService


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(max_length=60000)
    role: Literal["document", "source", "archive"] = "document"


class CreateWorkspace(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    documents: dict[str, Document]


class Task(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    mode: Literal["query", "update"]
    old: str = Field(default="", max_length=10000)
    new: str = Field(default="", max_length=10000)
    revision: int | None = Field(default=None, ge=1)
    editable_paths: list[str] | None = Field(default=None, max_length=50)
    creatable_paths: list[str] = Field(default_factory=list, max_length=50)
    required_absent: dict[str, list[str]] = Field(default_factory=dict)
    required_present: dict[str, list[str]] = Field(default_factory=dict)


class Review(BaseModel):
    approve: bool
    expected_hash: str


def router(sessions, runs, owner, settings):
    api = APIRouter(prefix="/v1/documents", tags=["DocChange"])
    service = DocumentService(sessions)
    owner_dependency = Depends(owner)

    @api.get("/runs/{rid}/timeline")
    async def timeline(rid: str, user=owner_dependency):
        return await runs.events(user, rid)

    @api.get("/workspaces")
    async def listing(user=owner_dependency):
        return await service.list(user)

    @api.post("/workspaces", status_code=201)
    async def create(
        body: CreateWorkspace,
        user=owner_dependency,
        idempotency_key: str = Header(min_length=1, max_length=128),
    ):
        return await service.create(
            user, body.name, {p: d.model_dump() for p, d in body.documents.items()}, idempotency_key
        )

    @api.get("/workspaces/{wid}")
    async def workspace(
        wid: str, revision: int | None = Query(default=None, ge=1), user=owner_dependency
    ):
        return await service.get(user, wid, revision)

    @api.get("/workspaces/{wid}/search")
    async def search(
        wid: str,
        query: str,
        revision: int | None = None,
        exact: bool = False,
        user=owner_dependency,
    ):
        snap = await service.get(user, wid, revision)
        return await service.search(user, wid, snap["revision"], query, exact=exact)

    @api.post("/workspaces/{wid}/tasks", status_code=202)
    async def task(
        wid: str,
        body: Task,
        user=owner_dependency,
        idempotency_key: str = Header(min_length=1, max_length=128),
    ):
        snap = await service.get(user, wid, body.revision)
        service.validate_contract(snap["documents"], body.model_dump())
        cfg = {
            "provider": settings.provider,
            "model": settings.model,
            "domain": "document",
            "model_limit": settings.model_limit,
            "active_budget_ms": settings.active_budget_ms,
            "model_timeout_seconds": settings.model_timeout_seconds,
            "document": {**body.model_dump(), "workspace_id": wid, "revision": snap["revision"]},
        }
        conversation = await runs.conversation(user, "doc:" + idempotency_key)
        return await runs.submit(
            user,
            conversation["id"],
            {"text": body.text, "client_message_id": digest(body.model_dump())[:36]},
            idempotency_key,
            cfg,
        )

    @api.get("/changesets/{cid}")
    async def changeset(cid: str, user=owner_dependency):
        return await service.change(user, cid)

    @api.post("/changesets/{cid}/checks")
    async def sandbox_check(cid: str, user=owner_dependency):
        from .sandbox import check_in_sandbox

        return await check_in_sandbox(await service.change(user, cid), settings)

    @api.post("/changesets/{cid}/decision")
    async def decide(
        cid: str,
        body: Review,
        user=owner_dependency,
        idempotency_key: str = Header(min_length=1, max_length=128),
    ):
        return await service.decide(user, cid, body.approve, body.expected_hash, idempotency_key)

    return api
