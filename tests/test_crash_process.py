import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select

from shopresolve.models import Refund, Run
from shopresolve.schemas import Decision
from tests.test_runs import drive, new_run


@pytest.mark.mysql
@pytest.mark.parametrize("point", ["before_tool_commit", "after_tool_commit"])
async def test_hard_process_exit_preserves_exactly_one_local_effect(commerce, point):
    service, sessions, _ = commerce
    async with sessions() as session:
        if session.bind.dialect.name != "mysql":
            pytest.skip("Real crash recovery requires the disposable MySQL schema")
        url = session.bind.url.render_as_string(hide_password=False)
    store, rid, runtime = await new_run(commerce, "帮我取消 order-pending")
    waiting = await drive(store, rid, runtime)
    await service.decide(
        "alice",
        waiting["goals"][0]["proposal_id"],
        Decision(decision="approve", expected_version=1),
        "approve",
    )
    environment = os.environ.copy()
    environment["SHOPRESOLVE_DATABASE_URL"] = url
    root = Path(__file__).resolve().parents[1]
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        str(root / "scripts" / "crash_worker_once.py"),
        rid,
        point,
        env=environment,
        cwd=root,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        _, stderr = await asyncio.wait_for(child.communicate(), timeout=30)
    except BaseException:
        if child.returncode is None:
            child.kill()
            await child.wait()
        raise
    assert child.returncode == 23, stderr.decode(errors="replace")[-2000:]
    async with sessions.begin() as session:
        # Only this fixture's validated test schema is modified.
        (await session.get(Run, rid)).lease_until = 0
    final = await drive(store, rid, runtime, "recovery-process-owner")
    assert final["outcome"] == "FULFILLED", final
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Refund)) == 1
    assert (await service.get_order("alice", "order-pending"))["refunded_cents"] == 19900
