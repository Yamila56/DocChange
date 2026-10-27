import json
from pathlib import Path

import pytest

from scripts.evaluate_workflows import evaluate
from shopresolve.retrieval.index import PolicyIndex

CASES = json.loads(Path("evals/data/workflows-v1.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
async def test_existing_workflow_dataset_remains_a_regression_gate(case, tmp_path):
    result = await evaluate(case, tmp_path, PolicyIndex("data/policies/demo.json"))
    assert result["passed"], result["checks"]
