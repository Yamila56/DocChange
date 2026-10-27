"""API upload -> isolated manager -> real Docker -> owner-scoped result."""

import base64
import json
import os
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx

from shopresolve.config import Settings


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    settings = Settings()
    if not settings.enable_demo or not settings.sandbox_token:
        raise RuntimeError("Local demo and an internal sandbox token are required")
    port, manager_port = free_port(), free_port()
    children = []
    try:
        for application, selected_port in [
            ("shopresolve.sandbox.manager:create_manager", manager_port),
            ("shopresolve.api:create_app", port),
        ]:
            environment = os.environ.copy()
            environment["SHOPRESOLVE_SANDBOX_URL"] = f"http://127.0.0.1:{manager_port}"
            children.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        application,
                        "--factory",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(selected_port),
                        "--log-level",
                        "error",
                    ],
                    env=environment,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            )
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=6) as client:
            for _ in range(100):
                if any(p.poll() is not None for p in children):
                    raise RuntimeError("Our sandbox smoke service exited")
                try:
                    if client.get("/health/ready").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            login = client.post(
                "/v1/demo-sessions", json={"account": "alice", "demo_code": settings.demo_code}
            )
            login.raise_for_status()
            headers = {
                "Authorization": f"Bearer {login.json()['access_token']}",
                "Idempotency-Key": str(uuid4()),
            }
            artifact = client.post(
                "/v1/artifacts?filename=demo.csv",
                content=b"price_cents,quantity\n19900,2\n5000,1\n",
                headers=headers,
            )
            artifact.raise_for_status()
            result = client.post(
                "/v1/analyses", json={"artifact_id": artifact.json()["id"]}, headers=headers
            )
            result.raise_for_status()
            job_id = result.json()["id"]
            for _ in range(100):
                response = client.get(f"/v1/analyses/{job_id}", headers=headers)
                response.raise_for_status()
                payload = response.json()
                if payload["status"] in {"COMPLETED", "FAILED", "TIMED_OUT"}:
                    break
                time.sleep(0.2)
            assert payload["status"] == "COMPLETED", payload
            output = json.loads(base64.b64decode(payload["outputs"][0]["base64"]))
            assert output["total_cents"] == 44800, output
            assert (
                httpx.get(f"http://127.0.0.1:{manager_port}/internal/jobs/{job_id}").status_code
                == 401
            )
            print(
                "Sandbox HTTP smoke passed: upload, internal auth, "
                "real Docker, verified 44800-cent result"
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
