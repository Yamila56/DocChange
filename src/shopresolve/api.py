import asyncio
import hashlib
import json
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text

from shopresolve.analyses import AnalysisService
from shopresolve.artifacts import ArtifactStore
from shopresolve.config import Settings
from shopresolve.db import make_database
from shopresolve.errors import DomainError
from shopresolve.models import Run, SessionToken
from shopresolve.runstore import TERMINAL, WAITING, RunStore
from shopresolve.schemas import (
    Decision,
    DemoLogin,
    PrepareAction,
    ResumeMessage,
    SubmitAnalysis,
    SubmitMessage,
)
from shopresolve.service import CommerceService

bearer = HTTPBearer(auto_error=False)
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


def create_app(settings=None, *, sessions=None, clock=time.time):
    settings = settings or Settings()
    engine = None
    if sessions is None:
        engine, sessions = make_database(settings.database_url)
    service = CommerceService(sessions, clock)
    runs = RunStore(sessions, clock)
    artifacts = ArtifactStore(sessions, settings.artifact_root)
    analyses = AnalysisService(sessions, artifacts, settings)

    @asynccontextmanager
    async def lifespan(app):
        yield
        if engine is not None:
            await engine.dispose()

    app = FastAPI(title="ShopResolve", version="0.1.0", lifespan=lifespan)
    app.state.service = service
    app.state.runs = runs

    @app.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Trace-ID"] = request.state.trace_id
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError):
        return JSONResponse(
            status_code=exc.status,
            content={
                "error_code": exc.code,
                "message": exc.message,
                "retryable": False,
                "trace_id": request.state.trace_id,
            },
        )

    async def owner(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        if credentials is None:
            raise DomainError("UNAUTHENTICATED", "Bearer token required", 401)
        token_hash = hashlib.sha256(credentials.credentials.encode()).hexdigest()
        async with sessions() as session:
            token = await session.get(SessionToken, token_hash)
            if token is None or token.expires_at <= int(clock()):
                raise DomainError("UNAUTHENTICATED", "Session expired or invalid", 401)
            return token.owner_id

    Owner = Annotated[str, Depends(owner)]

    @app.get("/health/live")
    async def live():
        return {"status": "ok", "simulated_business": True}

    @app.get("/health/ready")
    async def ready():
        try:
            async with sessions() as session:
                await session.execute(text("SELECT 1"))
                # A reachable server without migrated tables is not ready.
                await session.execute(select(SessionToken.token_hash).limit(1))
                await session.execute(select(Run.id).limit(1))
        except Exception:
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return {"status": "ready"}

    @app.post("/v1/demo-sessions")
    async def login(body: DemoLogin):
        if not settings.enable_demo or not settings.demo_code:
            raise DomainError("NOT_FOUND", "Resource not found", 404)
        if not secrets.compare_digest(body.demo_code, settings.demo_code):
            raise DomainError("UNAUTHENTICATED", "Invalid demo credentials", 401)
        token = secrets.token_urlsafe(32)
        async with sessions.begin() as session:
            session.add(
                SessionToken(
                    token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    owner_id=body.account,
                    expires_at=int(clock()) + 3600,
                )
            )
        return {"access_token": token, "token_type": "bearer", "expires_in": 3600}

    @app.get("/v1/orders")
    async def orders(user: Owner):
        return await service.list_orders(user)

    @app.get("/v1/orders/{order_id}")
    async def order(order_id: str, user: Owner):
        return await service.get_order(user, order_id)

    @app.post("/v1/proposals", status_code=201)
    async def prepare(body: PrepareAction, user: Owner, idempotency_key: IdempotencyKey):
        return await service.prepare(user, body, idempotency_key)

    @app.get("/v1/proposals/{proposal_id}")
    async def proposal(proposal_id: str, user: Owner):
        return await service.get_proposal(user, proposal_id)

    @app.post("/v1/proposals/{proposal_id}/decision")
    async def decide(
        proposal_id: str,
        body: Decision,
        user: Owner,
        idempotency_key: IdempotencyKey,
    ):
        return await service.decide(user, proposal_id, body, idempotency_key)

    @app.get("/v1/operations/{operation_id}")
    async def operation(operation_id: str, user: Owner):
        return await service.get_operation(user, operation_id)

    @app.post("/v1/conversations", status_code=201)
    async def conversation(user: Owner, idempotency_key: IdempotencyKey):
        return await runs.conversation(user, idempotency_key)

    @app.post("/v1/conversations/{conversation_id}/messages", status_code=202)
    async def submit(
        conversation_id: str, body: SubmitMessage, user: Owner, idempotency_key: IdempotencyKey
    ):
        config = {
            "provider": settings.provider,
            "model": settings.model,
            "model_limit": settings.model_limit,
            "active_budget_ms": settings.active_budget_ms,
            "model_timeout_seconds": settings.model_timeout_seconds,
        }
        return await runs.submit(user, conversation_id, body.model_dump(), idempotency_key, config)

    @app.get("/v1/runs")
    async def list_runs(user: Owner):
        return await runs.list_runs(user)

    @app.get("/v1/runs/{run_id}")
    async def run_snapshot(run_id: str, user: Owner):
        return await runs.get(user, run_id)

    @app.post("/v1/runs/{run_id}/cancel")
    async def cancel_run(run_id: str, user: Owner, idempotency_key: IdempotencyKey):
        return await runs.cancel(user, run_id, idempotency_key)

    @app.post("/v1/runs/{run_id}/revisions", status_code=202)
    async def revise_run(
        run_id: str, body: ResumeMessage, user: Owner, idempotency_key: IdempotencyKey
    ):
        return await runs.resume(user, run_id, body.model_dump(), idempotency_key)

    @app.get("/v1/runs/{run_id}/events")
    async def events(
        run_id: str,
        request: Request,
        user: Owner,
        last_event_id: Annotated[str | None, Header()] = None,
    ):
        await runs.get(user, run_id)
        try:
            cursor = int(last_event_id or "0")
            if cursor < 0:
                raise ValueError
        except ValueError as exc:
            raise DomainError("INVALID_CURSOR", "Last-Event-ID must be non-negative", 422) from exc

        async def stream():
            nonlocal cursor
            # DB polling avoids the replay/subscription race. Redis is only a
            # notification transport, never the authoritative SSE event store.
            while not await request.is_disconnected():
                batch = await runs.events(user, run_id, cursor)
                for event in batch:
                    cursor = event["seq"]
                    yield (
                        f"id: {cursor}\nevent: {event['kind']}\ndata: "
                        + json.dumps(event, ensure_ascii=False)
                        + "\n\n"
                    )
                current = await runs.get(user, run_id)
                if current["status"] in TERMINAL | WAITING and cursor >= current["last_event_seq"]:
                    return
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/v1/artifacts", status_code=201)
    async def upload_csv(
        request: Request,
        user: Owner,
        idempotency_key: IdempotencyKey,
        filename: Annotated[str, Query(min_length=1, max_length=100)],
    ):
        content = bytearray()
        async for chunk in request.stream():
            if len(content) + len(chunk) > 2 * 1024 * 1024:
                raise DomainError("FILE_LIMIT", "CSV exceeds 2 MiB", 413)
            content.extend(chunk)
        return await artifacts.upload(user, filename, bytes(content), idempotency_key)

    @app.get("/v1/artifacts/{artifact_id}")
    async def artifact_download(artifact_id: str, user: Owner):
        from urllib.parse import quote

        name, content = await artifacts.read(user, artifact_id)
        return Response(
            content,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/v1/analyses", status_code=202)
    async def analyze_csv(body: SubmitAnalysis, user: Owner, idempotency_key: IdempotencyKey):
        return await analyses.submit(user, body.artifact_id, idempotency_key)

    @app.get("/v1/analyses/{analysis_id}")
    async def analysis_status(analysis_id: str, user: Owner):
        return await analyses.get(user, analysis_id)

    frontend = Path("frontend/dist")
    if frontend.is_dir():

        @app.api_route(
            "/v1/{unmatched:path}",
            methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
            include_in_schema=False,
        )
        async def unknown_api(unmatched: str):
            raise DomainError("NOT_FOUND", "Resource not found", 404)

        app.mount("/", StaticFiles(directory=frontend, html=True), name="workbench")
    return app
