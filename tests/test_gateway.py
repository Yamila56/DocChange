import asyncio
import json

import httpx
import pytest

from shopresolve.agent.gateway import CompatibleJsonGateway
from shopresolve.cli import serve_until_stopped
from shopresolve.config import Settings


def settings(**overrides):
    return Settings(
        _env_file=None,
        **(
            {
                "provider": "deepseek",
                "model": "deepseek-flash",
                "model_base_url": "https://api.deepseek.com",
                "model_api_key": "test-only-not-a-real-key",
                "allow_paid_model": True,
            }
            | overrides
        ),
    )


def completion(content='{"items":[]}', finish="stop"):
    return {
        "choices": [
            {
                "finish_reason": finish,
                "message": {
                    "content": content,
                    "reasoning_content": "must-not-be-persisted",
                },
            }
        ],
        "usage": {"prompt_tokens": 80, "completion_tokens": 20, "total_tokens": 100},
    }


async def test_deepseek_bounded_non_thinking_json_and_connection_lifecycle():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=completion())

    gateway = CompatibleJsonGateway(settings(), transport=httpx.MockTransport(respond))
    try:
        for _ in range(2):
            reply = await gateway.request("INTENT", {"messages": []})
            assert reply.output == {"items": []}
            assert reply.usage["total_tokens"] == 100
            assert "must-not-be-persisted" not in str(reply)
        payload = json.loads(requests[0].content)
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["max_tokens"] == 1500
        assert payload["response_format"] == {"type": "json_object"}
        assert requests[0].url == "https://api.deepseek.com/chat/completions"
        assert not gateway.client.is_closed
    finally:
        await gateway.aclose()
    assert gateway.client.is_closed


@pytest.mark.parametrize(
    "content,finish",
    [
        ("", "stop"),
        (None, "stop"),
        ('{"kind":', "length"),
        ('{"kind":"final_answer"}', "length"),
        ("[]", "stop"),
        ("not json", "stop"),
    ],
)
async def test_incomplete_or_invalid_provider_reply_never_becomes_decision(content, finish):
    gateway = CompatibleJsonGateway(
        settings(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=completion(content, finish))
        ),
    )
    try:
        with pytest.raises(ValueError):
            await gateway.request("DECISION", {})
    finally:
        await gateway.aclose()


async def test_oversize_context_does_not_send_request():
    sent = []
    gateway = CompatibleJsonGateway(
        settings(), transport=httpx.MockTransport(lambda request: sent.append(request))
    )
    try:
        with pytest.raises(ValueError, match="byte limit"):
            await gateway.request("INTENT", {"text": "x" * 60000})
        assert not sent
    finally:
        await gateway.aclose()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://api.deepseek.com",
        "https://attacker.invalid",
        "https://api.deepseek.com?key=a",
        "https://user:password@api.deepseek.com",
    ],
)
def test_deepseek_endpoint_is_pinned(endpoint):
    with pytest.raises(ValueError):
        CompatibleJsonGateway(settings(model_base_url=endpoint))


def test_remote_model_requires_opt_in():
    with pytest.raises(RuntimeError, match="disabled"):
        CompatibleJsonGateway(settings(allow_paid_model=False))


async def test_shutdown_cancels_hung_service_with_finite_grace():
    stop, started, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def service():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    running = asyncio.create_task(serve_until_stopped(service(), stop, 0.01))
    await started.wait()
    stop.set()
    await asyncio.wait_for(running, timeout=1)
    assert cancelled.is_set()


async def test_shutdown_allows_current_step_to_finish():
    stop = asyncio.Event()

    async def service():
        await stop.wait()

    stop.set()
    await serve_until_stopped(service(), stop, 0.1)
