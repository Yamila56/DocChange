"""At-least-once notification transport; MySQL remains the source of truth."""

import asyncio
import logging
import time
from contextlib import suppress
from uuid import uuid4

from redis.asyncio import Redis
from sqlalchemy import select

from shopresolve.agent.gateway import build_gateway
from shopresolve.agent.runtime import AgentRuntime
from shopresolve.agent.tools import ToolRouter
from shopresolve.models import Outbox, Run
from shopresolve.retrieval.index import build_index
from shopresolve.runstore import TERMINAL, WAITING, RunStore, db_now
from shopresolve.service import CommerceService

logger = logging.getLogger("shopresolve.worker")


class TaskQueue:
    def __init__(self, settings):
        self.redis = Redis.from_url(
            settings.redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=3
        )
        self.stream = settings.queue_name
        self.group = "workers-v1"

    async def initialize(self):
        from redis.exceptions import ResponseError

        try:
            await self.redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def publish(self, run_id, generation):
        return await self.redis.xadd(self.stream, {"run_id": run_id, "generation": str(generation)})

    async def read(self, consumer):
        rows = await self.redis.xreadgroup(
            self.group, consumer, {self.stream: ">"}, count=1, block=1000
        )
        return rows[0][1] if rows else []

    async def reclaim(self, consumer, min_idle_ms=30000):
        result = await self.redis.xautoclaim(
            self.stream, self.group, consumer, min_idle_ms, "0-0", count=10
        )
        return result[1]

    async def ack(self, message_id):
        await self.redis.xack(self.stream, self.group, message_id)

    async def close(self):
        await self.redis.aclose()


class Dispatcher:
    def __init__(self, store, queue):
        self.store, self.queue = store, queue

    async def dispatch_once(self):
        # Do not keep SQL locks/transactions open across a Redis request.
        async with self.store.sessions() as session:
            rows = (
                await session.scalars(
                    select(Outbox)
                    .where(Outbox.published_at.is_(None))
                    .order_by(Outbox.id)
                    .limit(100)
                )
            ).all()
            messages = [(r.id, r.run_id, r.generation) for r in rows]
        for oid, rid, generation in messages:
            await self.queue.publish(rid, generation)
            async with self.store.sessions.begin() as session:
                row = await session.get(Outbox, oid)
                if row and row.published_at is None:
                    row.published_at = await db_now(session, self.store.clock)
        return len(messages)

    async def reconcile_once(self, requeue_after=10):
        async with self.store.sessions.begin() as session:
            now = await db_now(session, self.store.clock)
            runs = (
                await session.scalars(
                    select(Run)
                    .where(Run.status.in_(["QUEUED", "RUNNING", *WAITING]))
                    .with_for_update(skip_locked=True)
                    .limit(100)
                )
            ).all()
            for run in runs:
                if run.expires_at <= now and (run.status != "RUNNING" or run.lease_until <= now):
                    await self.store._finish(
                        session, run, "EXPIRED", "UNRESOLVED", "WAIT_EXPIRED", now
                    )
                    continue
                if run.status == "QUEUED" or (run.status == "RUNNING" and run.lease_until <= now):
                    row = await session.scalar(
                        select(Outbox)
                        .where(
                            Outbox.run_id == run.id,
                            Outbox.generation == run.resume_generation,
                        )
                        .with_for_update()
                    )
                    if row is None:
                        session.add(
                            Outbox(id=str(uuid4()), run_id=run.id, generation=run.resume_generation)
                        )
                    elif row.published_at is not None and row.published_at <= now - requeue_after:
                        row.published_at = None

    async def serve(self, stop):
        while not stop.is_set():
            try:
                await self.reconcile_once()
                await self.dispatch_once()
            except Exception as exc:
                logger.warning("dispatcher retry: %s", type(exc).__name__)
            await asyncio.sleep(0.5)


class Worker:
    def __init__(self, settings, sessions, *, gateway=None, queue=None, retriever=None):
        self.settings = settings
        self.store = RunStore(sessions)
        self.queue = queue or TaskQueue(settings)
        self.worker_id = str(uuid4())
        self.runtime = AgentRuntime(
            self.store,
            gateway or build_gateway(settings),
            ToolRouter(CommerceService(sessions), retriever or build_index(settings)),
        )

    async def handle(self, message_id, fields):
        lease = await self.store.claim(
            fields["run_id"],
            self.worker_id,
            generation=int(fields["generation"]),
            lease_seconds=self.settings.lease_seconds,
        )
        if lease is None:
            await self.queue.ack(message_id)
            return
        state, _, _ = await self.store.load(lease)
        if (
            state["config"]["provider"] != self.settings.provider
            or state["config"]["model"] != self.settings.model
        ):
            await self.store.finish(lease, state, "FAILED", "UNRESOLVED", "PROVIDER_CONFIG_CHANGED")
            await self.queue.ack(message_id)
            return
        task = asyncio.create_task(self.runtime.run(lease))
        heartbeat_lost = False

        async def heartbeat():
            nonlocal heartbeat_lost
            while not task.done():
                await asyncio.sleep(max(1, self.settings.lease_seconds / 3))
                try:
                    await self.store.heartbeat(lease, self.settings.lease_seconds)
                except Exception:
                    heartbeat_lost = True
                    task.cancel()
                    return

        beat = asyncio.create_task(heartbeat())
        try:
            await task
            run = await self.store.get(lease.owner_id, lease.run_id)
            if run["status"] in TERMINAL | WAITING:
                await self.queue.ack(message_id)
        except asyncio.CancelledError:
            if not heartbeat_lost:
                raise
        finally:
            beat.cancel()
            with suppress(asyncio.CancelledError):
                await beat
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    async def serve(self, stop):
        last_reclaim = 0
        while not stop.is_set():
            try:
                await self.queue.initialize()
                pending = []
                if time.monotonic() - last_reclaim > 5:
                    pending = await self.queue.reclaim(
                        self.worker_id, min_idle_ms=self.settings.lease_seconds * 1000
                    )
                    last_reclaim = time.monotonic()
                for message_id, fields in pending or await self.queue.read(self.worker_id):
                    await self.handle(message_id, fields)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Never log exception text that might include provider credentials/SQL params.
                logger.warning("worker retry: %s", type(exc).__name__)
                await asyncio.sleep(1)
