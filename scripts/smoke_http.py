"""Launch our own loopback server, verify actual HTTP, then stop only that child."""

import os
import socket
import subprocess
import sys
import time

import httpx

from shopresolve.config import Settings


def main():
    settings = Settings()
    if not settings.enable_demo or not settings.demo_code:
        raise RuntimeError("Smoke test needs explicitly enabled local demo authentication")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen(
        [
            sys.executable,
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
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=3) as client:
            for _ in range(60):
                if process.poll() is not None:
                    raise RuntimeError("Our smoke-test API process exited before readiness")
                try:
                    if client.get("/health/ready").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("API did not become ready")
            response = client.get("/openapi.json")
            response.raise_for_status()
            assert response.json()["info"]["title"] == "ShopResolve"
            login = client.post(
                "/v1/demo-sessions",
                json={
                    "account": "alice",
                    "demo_code": settings.demo_code,
                },
            )
            login.raise_for_status()
            auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
            orders = client.get("/v1/orders", headers=auth)
            orders.raise_for_status()
            assert len(orders.json()) == 5
            assert client.get("/v1/orders/order-bob", headers=auth).status_code == 404
            print(
                "HTTP smoke passed: readiness, OpenAPI, login, "
                "owner-scoped orders, denied cross-owner"
            )
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
