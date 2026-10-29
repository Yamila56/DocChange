import argparse
import asyncio
import signal
from contextlib import suppress

from sqlalchemy.engine import make_url

from shopresolve.config import Settings
from shopresolve.db import make_database
from shopresolve.seed import seed


async def run_seed():
    settings = Settings()
    url = make_url(settings.database_url)
    if url.database != "shopresolve":
        raise RuntimeError("Demo seed is restricted to the dedicated 'shopresolve' database")
    engine, sessions = make_database(settings.database_url)
    try:
        await seed(sessions)
        print("Synthetic demo data created. No real orders or payments are connected.")
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["seed", "worker", "dispatcher"])
    args = parser.parse_args()
    asyncio.run(run_seed() if args.command == "seed" else run_service(args.command))


async def run_service(command):
    from shopresolve.queue import Dispatcher, TaskQueue, Worker
    from shopresolve.runstore import RunStore

    settings = Settings()
    engine, sessions = make_database(settings.database_url)
    queue = TaskQueue(settings)
    stop = asyncio.Event()
    worker = None
    # signal.signal works on Windows, unlike loop.add_signal_handler.
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    try:
        if command == "worker":
            worker = Worker(settings, sessions, queue=queue)
            await serve_until_stopped(worker.serve(stop), stop, settings.shutdown_grace_seconds)
        else:
            await serve_until_stopped(
                Dispatcher(RunStore(sessions), queue).serve(stop),
                stop,
                settings.shutdown_grace_seconds,
            )
    finally:
        if worker is not None:
            close = getattr(worker.runtime.gateway, "aclose", None)
            if close is not None:
                await close()
            close_document = getattr(worker.document_runtime.gateway, "aclose", None)
            if close_document is not None:
                await close_document()
        await queue.close()
        await engine.dispose()


async def serve_until_stopped(service, stop, grace_seconds):
    """Stop intake first; on grace expiry cancel without ACK/forged terminal state."""
    task = asyncio.create_task(service)
    stopping = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait({task, stopping}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            await task  # propagate unexpected service failure
        else:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=grace_seconds)
            except TimeoutError:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
    finally:
        stopping.cancel()
        with suppress(asyncio.CancelledError):
            await stopping
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


if __name__ == "__main__":
    main()
