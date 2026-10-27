import pytest
from httpx import ASGITransport, AsyncClient

from shopresolve.api import create_app
from shopresolve.config import Settings


@pytest.fixture
async def client(commerce):
    _, sessions, clock = commerce
    app = create_app(
        Settings(enable_demo=True, demo_code="test-only-code"), sessions=sessions, clock=clock
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def headers(client, account="alice"):
    result = await client.post(
        "/v1/demo-sessions",
        json={
            "account": account,
            "demo_code": "test-only-code",
        },
    )
    assert result.status_code == 200
    return {"Authorization": f"Bearer {result.json()['access_token']}"}


async def test_health_and_auth(client):
    assert (await client.get("/health/live")).status_code == 200
    assert (await client.get("/health/ready")).status_code == 200
    assert (await client.get("/v1/orders")).status_code == 401
    assert (
        await client.post(
            "/v1/demo-sessions",
            json={
                "account": "alice",
                "demo_code": "wrong",
            },
        )
    ).status_code == 401


async def test_api_proposal_approval_and_no_public_execution(client):
    auth = await headers(client)
    body = {"action": "CANCEL_ORDER", "order_id": "order-pending"}
    assert (await client.post("/v1/proposals", json=body, headers=auth)).status_code == 422
    auth["Idempotency-Key"] = "prepare-1"
    first = await client.post("/v1/proposals", json=body, headers=auth)
    assert first.status_code == 201, first.text
    assert (await client.post("/v1/proposals", json=body, headers=auth)).json() == first.json()
    pid = first.json()["id"]
    result = await client.post(
        f"/v1/proposals/{pid}/decision",
        headers=auth,
        json={"decision": "approve", "expected_version": 1},
    )
    assert result.status_code == 200
    assert result.json()["status"] == "APPROVED"
    assert (await client.post(f"/v1/proposals/{pid}/execute", headers=auth)).status_code == 404


async def test_owner_injection_and_cross_owner_access(client):
    auth = await headers(client)
    auth["Idempotency-Key"] = "key"
    response = await client.post(
        "/v1/proposals",
        headers=auth,
        json={
            "action": "CANCEL_ORDER",
            "order_id": "order-pending",
            "owner_id": "bob",
        },
    )
    assert response.status_code == 422
    assert (await client.get("/v1/orders/order-bob", headers=auth)).status_code == 404


async def test_expired_session(client, commerce):
    auth = await headers(client)
    commerce[2].value += 3601
    assert (await client.get("/v1/orders", headers=auth)).status_code == 401


async def test_openapi_and_trace_id(client):
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    assert "/v1/proposals" in response.json()["paths"]
    response = await client.get("/v1/orders")
    assert response.json()["trace_id"] == response.headers["x-trace-id"]
