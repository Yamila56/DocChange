"""One-command local workbench. Durable DB polling is the Redis-free demo mode.

Production-style Redis worker/dispatcher entrypoints remain shopresolve.cli.
No .env changes and no paid requests; existing CLI services use configured MySQL/Redis.
"""

import argparse
import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import uvicorn
from sqlalchemy import select

from shopresolve.api import create_app
from shopresolve.config import Settings
from shopresolve.db import make_database
from shopresolve.documents.models import Workspace  # noqa: F401
from shopresolve.documents.runtime import DocumentDemoGateway, DocumentRuntime
from shopresolve.models import Base, Run
from shopresolve.runstore import RunStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--remote", action="store_true", help="Explicit paid DeepSeek mode")
    parser.add_argument(
        "--retrieval-mode", choices=["bm25", "dense", "hybrid", "hybrid_rerank"], default="bm25"
    )
    args = parser.parse_args()
    folder = Path(".cache/docchange")
    folder.mkdir(parents=True, exist_ok=True)
    configured = Settings()
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{folder / 'workspace.sqlite'}",
        enable_demo=True,
        demo_code="docs-local-2026",
        provider="demo",
        model="demo-v1",
        allow_paid_model=False,
        sandbox_url=configured.sandbox_url,
        sandbox_token=configured.sandbox_token,
        document_retrieval_mode=args.retrieval_mode,
        model_cache_dir=configured.model_cache_dir,
    )
    inner = None
    gateway = DocumentDemoGateway()
    if args.remote:
        from shopresolve.documents.runtime import DocumentGateway

        if __package__:
            from scripts.evaluate_models import EvaluationBudget, RecordedGateway
        else:
            from evaluate_models import EvaluationBudget, RecordedGateway
        settings = Settings(
            database_url=settings.database_url,
            enable_demo=True,
            demo_code=settings.demo_code,
            provider="deepseek",
            model="deepseek-flash",
            model_base_url="https://api.deepseek.com",
            allow_paid_model=True,
            document_retrieval_mode=args.retrieval_mode,
            model_cache_dir=configured.model_cache_dir,
        )
        inner = DocumentGateway(settings)
        budget = EvaluationBudget(folder / "interactive-budget.sqlite", 20, 0.5, 0)
        gateway = RecordedGateway(inner, budget, folder / "interactive-calls.jsonl")
    engine, sessions = make_database(settings.database_url)
    store = RunStore(sessions)

    async def poll():
        runtime = DocumentRuntime(store, gateway, model_cache_dir=settings.model_cache_dir)
        while True:
            async with sessions() as session:
                ids = (
                    await session.scalars(
                        select(Run.id).where(Run.status.in_(["QUEUED", "RUNNING"])).limit(20)
                    )
                ).all()
            for rid in ids:
                lease = await store.claim(rid, "local-docchange", lease_seconds=180)
                if lease:
                    state, _, _ = await store.load(lease)
                    if state["config"]["provider"] != settings.provider:
                        await store.finish(
                            lease, state, "FAILED", "UNRESOLVED", "PROVIDER_CONFIG_CHANGED"
                        )
                        continue
                    await runtime.run(lease)
            await asyncio.sleep(0.3)

    app = create_app(settings, sessions=sessions)

    @asynccontextmanager
    async def lifespan(_):
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        task = asyncio.create_task(poll())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            await engine.dispose()
            if inner is not None:
                await inner.aclose()

    app.router.lifespan_context = lifespan
    mode = "DeepSeek: persistent 20 requests / 0.5 CNY cap" if args.remote else "free demo"
    print(f"DocChange http://127.0.0.1:{args.port} | alice / docs-local-2026 | {mode}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
