"""Check the new document migration from the previous schema, in an isolated file."""

import asyncio
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config

from shopresolve.db import make_database
from shopresolve.documents import models  # noqa: F401
from shopresolve.models import Base


async def prepare(url):
    engine, _ = make_database(url)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda c: Base.metadata.create_all(
                c, tables=[t for t in Base.metadata.sorted_tables if not t.name.startswith("doc_")]
            )
        )
    await engine.dispose()


if __name__ == "__main__":
    path = Path(".cache/docchange") / f"migration-from-previous-{uuid4().hex}.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    url = "sqlite+aiosqlite:///" + str(path)
    os.environ["SHOPRESOLVE_DATABASE_URL"] = url
    asyncio.run(prepare(url))
    command.stamp(Config("alembic.ini"), "d3cf3cd9b079")
    command.upgrade(Config("alembic.ini"), "head")
    print("DOCUMENT_MIGRATION_FROM_PREVIOUS_SCHEMA_OK")
