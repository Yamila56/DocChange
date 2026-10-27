import os
from dataclasses import dataclass

import pytest_asyncio
from sqlalchemy.engine import make_url

from shopresolve.db import make_database
from shopresolve.models import Base
from shopresolve.seed import seed
from shopresolve.service import CommerceService


@dataclass
class Clock:
    value: int = 1800000000

    def __call__(self):
        return self.value


@pytest_asyncio.fixture
async def commerce(tmp_path):
    url = os.getenv("SHOPRESOLVE_TEST_DATABASE_URL")
    if url is not None:
        parsed = make_url(url)
        if parsed.get_backend_name() != "mysql" or parsed.database != "shopresolve_test":
            raise RuntimeError("Integration tests may reset ONLY the 'shopresolve_test' MySQL DB")
    else:
        url = f"sqlite+aiosqlite:///{tmp_path / 'test.sqlite'}"
    engine, sessions = make_database(url)
    async with engine.begin() as conn:
        # Target was validated above; never runs against the demo or any existing user DB.
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    clock = Clock()
    await seed(sessions, now=clock.value)
    service = CommerceService(sessions, clock)
    yield service, sessions, clock
    await engine.dispose()
