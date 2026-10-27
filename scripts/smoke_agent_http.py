"""Real API + dispatcher + worker processes, read-only task, finite SSE replay."""

import json
import os
import socket
import subprocess
import sys
import time

import httpx

from shopresolve.config import Settings


def main():
    settings = Settings()
    if settings.provider != "demo" or not settings.enable_demo:
        raise RuntimeError("Smoke script only runs the explicitly enabled no-key demo provider")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    children = []
    try:
        commands = [
            [
                "-m",
                "uvicorn",
                "shopresolve.api:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "error",
            ],
            ["-m", "shopresolve.cli", "worker"],
            ["-m", "shopresolve.cli", "dispatcher"],
        ]
        for arguments in commands:
            children.append(
                subprocess.Popen(
                    [sys.executable, *arguments],
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            )
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=3) as client:
            for _ in range(100):
                if any(p.poll() is not None for p in children):
                    raise RuntimeError("One of our service processes exited")
                try:
                    if client.get("/health/ready").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("API readiness timeout")
            login = client.post(
                "/v1/demo-sessions", json={"account": "alice", "demo_code": settings.demo_code}
            )
            login.raise_for_status()
            auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
            c = client.post(
                "/v1/conversations", json={}, headers={**auth, "Idempotency-Key": str(port)}
            )
            c.raise_for_status()
            response = client.post(
                f"/v1/conversations/{c.json()['id']}/messages",
                json={"text": "查询 order-pending", "client_message_id": "http-smoke"},
                headers={**auth, "Idempotency-Key": "http-smoke"},
            )
            response.raise_for_status()
            rid = response.json()["run_id"]
            for _ in range(100):
                final = client.get(f"/v1/runs/{rid}", headers=auth).json()
                if final["status"] in {"SUCCEEDED", "FAILED"}:
                    break
                time.sleep(0.2)
            assert final["status"] == "SUCCEEDED", final
            assert final["outcome"] == "FULFILLED", final
            stream = client.get(f"/v1/runs/{rid}/events", headers=auth)
            stream.raise_for_status()
            events = [
                json.loads(line[6:])
                for line in stream.text.splitlines()
                if line.startswith("data: ")
            ]
            assert events and any(e["kind"] == "tool.completed" for e in events)
            cursor = events[len(events) // 2]["seq"]
            replay = client.get(
                f"/v1/runs/{rid}/events", headers={**auth, "Last-Event-ID": str(cursor)}
            )
            later = [
                json.loads(line[6:])
                for line in replay.text.splitlines()
                if line.startswith("data: ")
            ]
            assert later and all(e["seq"] > cursor for e in later)
            assert client.get("/").status_code == 200
            print(
                "Agent HTTP smoke passed: API/outbox/Redis/worker/"
                "order verification/SSE replay/workbench"
            )
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


if __name__ == "__main__":
    main()
