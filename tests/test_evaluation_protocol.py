import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.evaluate_models import EvaluationBudget, summarize
from scripts.evaluate_workflows import evaluate
from shopresolve.errors import DomainError
from shopresolve.retrieval.index import PolicyIndex


def test_pro_usage_is_not_settled_at_flash_price(tmp_path):
    budget = EvaluationBudget(tmp_path / "mixed.sqlite", 10, 5, 0)
    for model in ("deepseek-flash", "deepseek-v4-pro"):
        identifier = budget.reserve(model, 10000, 1500)
        budget.settle(identifier, {"prompt_tokens": 10000, "completion_tokens": 1000})
    by_model = budget.summary()["by_model"]
    assert Decimal(by_model["deepseek-flash"]["estimated_cny"]) == Decimal("0.028")
    assert Decimal(by_model["deepseek-v4-pro"]["estimated_cny"]) == Decimal("0.117")


def test_mixed_budget_preserves_unknown_usage_and_cannot_be_reconfigured(tmp_path):
    path = tmp_path / "bounded.sqlite"
    budget = EvaluationBudget(path, 3, "0.10", 0)
    identifier = budget.reserve("deepseek-v4-pro", 1000, 100)
    budget.settle(identifier, {"prompt_tokens": 100})
    with pytest.raises(DomainError):
        budget.reserve("deepseek-v4-pro", 1000, 100)
    with pytest.raises(ValueError):
        EvaluationBudget(path, 100, 50, 0)


def test_two_trial_reliability_is_not_any_one_success():
    rows = [
        {"model": "m", "case_id": "one", "passed": v, "trial": i}
        for i, v in enumerate([True, False])
    ]
    metrics = summarize(rows)["m"]
    assert metrics["success_rate"] == 0.5
    assert metrics["empirical_two_trial_reliability"] == 0


async def test_changed_amount_grader_rejects_a_stale_expected_refund(tmp_path):
    source = json.loads(Path("evals/data/commerce-evaluation-v2.json").read_text(encoding="utf-8"))
    case = copy.deepcopy(next(c for c in source["cases"] if c["id"] == "f04"))
    case["expected_refunds"] = [["order-pending", 19900, "CNY"]]
    result = await evaluate(case, tmp_path, PolicyIndex("data/policies/demo.json"))
    assert not result["passed"]
    assert result["checks"]["order:order-pending"]
    assert not result["checks"]["exact_refunds"]
