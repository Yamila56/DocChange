import argparse
import asyncio
import signal

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
    # signal.signal works on Windows, unlike loop.add_signal_handler.
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    try:
        if command == "worker":
            await Worker(settings, sessions, queue=queue).serve(stop)
        else:
            await Dispatcher(RunStore(sessions), queue).serve(stop)
    finally:
        await queue.close()
        await engine.dispose()


if __name__ == "__main__":
    main()
