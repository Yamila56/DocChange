"""TEST ONLY. Hard exit at a specified fault point on a disposable MySQL schema."""

import argparse
import asyncio
import os

from sqlalchemy import event
from sqlalchemy.engine import make_url

from shopresolve.agent.gateway import DemoGateway
from shopresolve.agent.runtime import AgentRuntime
from shopresolve.agent.tools import ToolRouter
from shopresolve.config import Settings
from shopresolve.db import make_database
from shopresolve.runstore import RunStore
from shopresolve.service import CommerceService


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument(
        "point", choices=["after_model_response_commit", "after_tool_commit", "before_tool_commit"]
    )
    args = parser.parse_args()
    url = Settings().database_url
    parsed = make_url(url)
    if parsed.get_backend_name() != "mysql" or parsed.database != "shopresolve_test":
        raise RuntimeError("Fault subprocess is restricted to shopresolve_test MySQL")
    engine, sessions = make_database(url)
    store = RunStore(sessions)

    async def fault(point, lease):
        if point == args.point:
            if point == "after_model_response_commit":
                saved, _, _ = await store.load(lease)
                if saved["pending"]["phase"] != "DECISION":
                    return
            os._exit(23)

    if args.point == "before_tool_commit":

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def before_insert(connection, cursor, statement, parameters, context, many):
            if statement.lstrip().upper().startswith("INSERT INTO REFUNDS"):
                os._exit(23)

    runtime = AgentRuntime(
        store, DemoGateway(), ToolRouter(CommerceService(sessions)), fault_hook=fault
    )
    lease = await store.claim(args.run_id, "fault-subprocess")
    if lease is None:
        raise RuntimeError("Cannot claim test run")
    await runtime.run(lease)
    await engine.dispose()
    raise RuntimeError("Fault point was not reached")


if __name__ == "__main__":
    asyncio.run(main())
