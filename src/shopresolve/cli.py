import argparse
import asyncio

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
    parser.add_argument("command", choices=["seed"])
    parser.parse_args()
    asyncio.run(run_seed())


if __name__ == "__main__":
    main()
