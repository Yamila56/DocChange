from uuid import uuid4

import pytest

from shopresolve.config import Settings
from shopresolve.models import Outbox
from shopresolve.queue import Dispatcher, TaskQueue, Worker
from tests.test_runs import new_run


@pytest.fixture
async def task_queue():
    settings = Settings(queue_name=f"shopresolve:test:{uuid4()}")
    queue = TaskQueue(settings)
    try:
        try:
            await queue.redis.ping()
        except Exception:
            pytest.skip("Local Redis unavailable; integration test not executed")
        await queue.initialize()
        yield queue, settings
    finally:
        if queue.stream.startswith("shopresolve:test:"):
            try:
                await queue.redis.delete(queue.stream)
            except Exception:
                pass
        await queue.close()


async def test_outbox_worker_and_duplicate_notification(commerce, task_queue):
    queue, settings = task_queue
    store, rid, _ = await new_run(commerce)
    dispatcher = Dispatcher(store, queue)
    assert await dispatcher.dispatch_once() == 1
    worker = Worker(settings, commerce[1], queue=queue)
    message_id, fields = (await queue.read(worker.worker_id))[0]
    await worker.handle(message_id, fields)
    result = await store.get("alice", rid)
    assert result["status"] == "SUCCEEDED", result
    calls = result["model_calls"]
    await queue.publish(rid, 0)
    duplicate_id, fields = (await queue.read(worker.worker_id))[0]
    await worker.handle(duplicate_id, fields)
    assert (await store.get("alice", rid))["model_calls"] == calls


async def test_redis_notification_loss_recovers_from_database(commerce, task_queue):
    queue, settings = task_queue
    store, rid, _ = await new_run(commerce)
    dispatcher = Dispatcher(store, queue)
    await dispatcher.dispatch_once()
    # Delete ONLY the disposable namespace, never FLUSHDB or the application stream.
    assert queue.stream.startswith("shopresolve:test:")
    await queue.redis.delete(queue.stream)
    await queue.initialize()
    async with commerce[1].begin() as session:
        from sqlalchemy import select

        row = await session.scalar(select(Outbox).where(Outbox.run_id == rid))
        row.published_at = 0
    await dispatcher.reconcile_once(requeue_after=0)
    assert await dispatcher.dispatch_once() == 1
    worker = Worker(settings, commerce[1], queue=queue)
    message_id, fields = (await queue.read(worker.worker_id))[0]
    await worker.handle(message_id, fields)
    assert (await store.get("alice", rid))["status"] == "SUCCEEDED"
