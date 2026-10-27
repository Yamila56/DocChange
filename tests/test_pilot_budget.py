import json
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.evaluate_workflows import evaluate
from scripts.pilot_deepseek import BoundedGateway, PilotBudget
from shopresolve.agent.gateway import ModelReply
from shopresolve.errors import DomainError
from shopresolve.retrieval.index import PolicyIndex


def test_attempt_reservation_survives_restart_and_unknown_response(tmp_path):
    ledger = tmp_path / "attempts.sqlite"
    PilotBudget(ledger, 2, 1, "0.4").reserve()
    PilotBudget(ledger, 2, 1, "0.4").reserve()
    with pytest.raises(DomainError, match="budget exhausted"):
        PilotBudget(ledger, 2, 1, "0.4").reserve()


def test_cost_limit_prevents_next_attempt_without_refunding(tmp_path):
    budget = PilotBudget(tmp_path / "attempts.sqlite", 20, Decimal("0.79"), "0.4")
    budget.reserve()
    with pytest.raises(DomainError):
        budget.reserve()


async def test_budget_rejection_happens_before_provider_request(tmp_path):
    class Fake:
        calls = 0

        async def request(self, phase, context):
            self.calls += 1
            return ModelReply({}, None)

    fake = Fake()
    gateway = BoundedGateway(fake, PilotBudget(tmp_path / "attempts.sqlite", 1, 1, "0.4"))
    await gateway.request("INTENT", {})
    with pytest.raises(DomainError):
        await gateway.request("INTENT", {})
    assert fake.calls == 1
    assert gateway.usage == [None]


async def test_pilot_cases_pass_objective_grader_without_paid_model(tmp_path):
    source = Path("evals/data/deepseek-pilot-v1.json")
    for case in json.loads(source.read_text(encoding="utf-8"))["cases"]:
        directory = tmp_path / case["id"]
        directory.mkdir()
        result = await evaluate(case, directory, PolicyIndex("data/policies/demo.json"))
        assert result["passed"], result["checks"]
        assert result["checks"]["exact_refunds"]
