import json
from decimal import Decimal

import httpx

from scripts.evaluate_models import EvaluationBudget
from shopresolve.documents.model_profile import PRO_MODEL, cache_hit_rate, pro_settings
from shopresolve.documents.repository import RepositoryGateway
from shopresolve.documents.runtime import DocumentGateway


async def test_pro_gateways_enable_reasoning_and_keep_private_reasoning_out_of_reply():
    for cls, context in (
        (DocumentGateway, {"task": {}, "observations": []}),
        (RepositoryGateway, {"task_prompt": "Locate authentication guidance", "observations": []}),
    ):
        requests = []

        def response(request, requests=requests):
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": '{"kind":"search","query":"authentication"}',
                                "reasoning_content": "private-deliberation",
                            },
                        }
                    ],
                    "model": PRO_MODEL,
                    "usage": {"prompt_tokens": 100, "completion_tokens": 200},
                },
            )

        gateway = cls(
            pro_settings(_env_file=None, model_api_key="test-only-key"),
            transport=httpx.MockTransport(response),
        )
        try:
            reply = await gateway.request("DOCUMENT", context)
            assert requests[0]["model"] == PRO_MODEL
            assert requests[0]["thinking"] == {"type": "enabled"}
            assert requests[0]["reasoning_effort"] == "high"
            assert requests[0]["max_tokens"] == 8192
            assert "temperature" not in requests[0]
            assert "private-deliberation" not in str(reply)
            assert reply.usage["completion_tokens"] == 200
        finally:
            await gateway.aclose()


def test_pro_peak_cache_rate_and_usage_settlement(tmp_path):
    assert cache_hit_rate(PRO_MODEL) == 0.30
    budget = EvaluationBudget(tmp_path / "pro.sqlite", 2, 1, 0, cache_hit_rate=0.30)
    identifier = budget.reserve(PRO_MODEL, 1000, 8192)
    budget.settle(
        identifier,
        {"prompt_tokens": 1000, "completion_tokens": 100, "prompt_cache_hit_tokens": 800},
    )
    assert Decimal(budget.summary()["estimated_total_with_carry_cny"]) == Decimal("0.00474")
