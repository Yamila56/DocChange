import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx

from shopresolve.api import create_app
from shopresolve.config import Settings
from shopresolve.db import make_database
from shopresolve.documents.runtime import DocumentDemoGateway, DocumentRuntime
from shopresolve.models import Base
from shopresolve.runstore import RunStore
from shopresolve.seed import seed


async def main():
    folder = Path(".cache/semantic-workbench-smoke")
    folder.mkdir(exist_ok=True)
    engine, sessions = make_database(f"sqlite+aiosqlite:///{folder / (str(uuid4()) + '.sqlite')}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await seed(sessions, now=1800000000)
    settings = Settings(
        _env_file=None,
        enable_demo=True,
        demo_code="semantic-smoke",
        document_retrieval_mode="hybrid",
    )
    app = create_app(settings, sessions=sessions)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        login = await client.post(
            "/v1/demo-sessions", json={"account": "alice", "demo_code": "semantic-smoke"}
        )
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        response = await client.post(
            "/v1/documents/workspaces",
            headers={"Idempotency-Key": str(uuid4())},
            json={
                "name": "real-onnx-smoke",
                "documents": {
                    "network.md": {
                        "content": "# Recovery\n"
                        "Retry disconnected requests after the configured timeout."
                    },
                    "theme.md": {"content": "# Theme\nSet the appearance and background color."},
                },
            },
        )
        assert response.status_code == 201, response.text
        wid = response.json()["id"]
        search = await client.get(
            f"/v1/documents/workspaces/{wid}/search", params={"query": "client reconnect"}
        )
        assert search.status_code == 200 and search.json()[0]["path"] == "network.md", search.text
        response = await client.post(
            f"/v1/documents/workspaces/{wid}/tasks",
            headers={"Idempotency-Key": str(uuid4())},
            json={"text": "client reconnect", "mode": "query"},
        )
        assert response.status_code == 202, response.text
        store = RunStore(sessions)
        lease = await store.claim(response.json()["run_id"], "semantic-smoke", lease_seconds=180)
        state, _, _ = await store.load(lease)
        assert state["config"]["document"]["retrieval_mode"] == "hybrid"
        await DocumentRuntime(store, DocumentDemoGateway()).run(lease)
        run = await store.get("alice", lease.run_id)
        assert run["status"] == "SUCCEEDED" and run["evidence"], run
        report = {
            "http_search": 200,
            "task_submission": 202,
            "run_status": run["status"],
            "first_hit": search.json()[0]["path"],
            "retrieval_mode": "hybrid",
            "encoder": "BGE-base-en-v1.5 FP32 ONNX CPU",
            "generative_api_requests": 0,
            "controller": "deterministic demo; functional smoke, not LLM evaluation",
        }
        Path("docs/evaluation/docchange-semantic-smoke-2026-11-03.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(json.dumps(report))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
