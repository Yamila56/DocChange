import sqlite3
from datetime import datetime
from decimal import Decimal

import httpx
import pytest

from scripts import resume_offpeak_dogbench as billing
from shopresolve.documents.model_profile import pro_settings
from shopresolve.errors import DomainError


@pytest.mark.parametrize(
    "day,hour,expected",
    [
        (3, 9, False),
        (3, 11, False),
        (3, 12, True),
        (3, 14, False),
        (3, 17, False),
        (3, 18, True),
        (3, 20, True),
        (7, 10, True),
    ],
)
def test_official_weekday_and_weekend_price_windows(day, hour, expected):
    stamp = datetime(2026, 11, day, hour, tzinfo=billing.BEIJING).timestamp()
    assert billing.is_offpeak(stamp) is expected


def test_half_price_keeps_unknown_reserves_and_attempt_limits(tmp_path, monkeypatch):
    stamp = datetime(2026, 11, 3, 20, tzinfo=billing.BEIJING).timestamp()
    monkeypatch.setattr(billing.time, "time", lambda: stamp)
    path = tmp_path / "budget.sqlite"
    budget = billing.OffpeakBudget(path, 2, 2, 0, cache_hit_rate=0.30)
    first = budget.reserve("deepseek-v4-pro", 1000, 256)
    budget.settle(
        first, {"prompt_tokens": 1000, "completion_tokens": 100, "prompt_cache_hit_tokens": 500}
    )
    budget.reserve("deepseek-v4-pro", 1000, 256)
    result = budget.summary()
    assert Decimal(result["known_usage_cost_cny"]) == Decimal("0.003675")
    assert Decimal(result["unknown_usage_reserve_cny"]) == Decimal("0.04482")
    assert Decimal(result["estimated_total_with_carry_cny"]) == Decimal("0.048495")
    assert result["by_model"]["deepseek-v4-pro"]["unknown_usage"] == 1
    restarted = billing.OffpeakBudget(path, 2, 2, 0, cache_hit_rate=0.30)
    with pytest.raises(DomainError):
        restarted.reserve("deepseek-v4-pro", 1000, 256)


def test_request_crossing_into_peak_is_not_sent(tmp_path, monkeypatch):
    stamp = datetime(2026, 11, 3, 8, 59, tzinfo=billing.BEIJING).timestamp()
    monkeypatch.setattr(billing.time, "time", lambda: stamp)
    path = tmp_path / "budget.sqlite"
    budget = billing.OffpeakBudget(path, 10, 2, 0)
    with pytest.raises(DomainError, match="window"):
        budget.reserve("deepseek-v4-pro", 1000, 256)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_truncated_reply_retains_provider_usage_for_billing():
    usage = {"prompt_tokens": 1000, "completion_tokens": 100}
    gateway = billing.UsageCapturingGateway(
        pro_settings(_env_file=None, model_api_key="unit-test-only"),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "model": "deepseek-v4-pro",
                    "usage": usage,
                    "choices": [{"finish_reason": "length", "message": {"content": '{"kind":'}}],
                },
            )
        ),
    )
    try:
        with pytest.raises(ValueError) as error:
            await gateway.request(
                "DOCUMENT_REPOSITORY", {"task_prompt": "Update docs", "observations": []}
            )
        assert error.value.usage == usage
    finally:
        await gateway.aclose()
