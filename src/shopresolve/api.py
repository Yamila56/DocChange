import hashlib
import secrets
import time
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, text

from shopresolve.config import Settings
from shopresolve.db import make_database
from shopresolve.errors import DomainError
from shopresolve.models import SessionToken
from shopresolve.schemas import Decision, DemoLogin, PrepareAction
from shopresolve.service import CommerceService

bearer = HTTPBearer(auto_error=False)
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


def create_app(settings=None, *, sessions=None, clock=time.time):
    settings = settings or Settings()
    engine = None
    if sessions is None:
        engine, sessions = make_database(settings.database_url)
    service = CommerceService(sessions, clock)

    @asynccontextmanager
    async def lifespan(app):
        yield
        if engine is not None:
            await engine.dispose()

    app = FastAPI(title="ShopResolve", version="0.1.0", lifespan=lifespan)
    app.state.service = service

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
        return {"status": "ok", "milestone": "M1", "simulated_business": True}

    @app.get("/health/ready")
    async def ready():
        try:
            async with sessions() as session:
                await session.execute(text("SELECT 1"))
                # A reachable server without migrated tables is not ready.
                await session.execute(select(SessionToken.token_hash).limit(1))
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

    return app
