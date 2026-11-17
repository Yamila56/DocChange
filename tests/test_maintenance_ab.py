import json
import sqlite3

import pytest

from scripts.analyze_maintenance_ab import (
    failure_classification,
    metrics,
    paired_bootstrap,
    paired_counts,
    task_measurements,
)


def row(**overrides):
    return {
        "status": "completed",
        "decision": "patch",
        "expected_outcome": "patch",
        "decision_correct": True,
        "overall": 50,
        "mergeable": False,
        "blocking_p0": ["C1"],
        "model_decisions": 4,
        "api_receipt_records": 4,
        "operational_tool_calls": 3,
        "execution_error": "",
        "execution_error_type": None,
        "phase": "FINALIZE",
        "review": {"ready": True},
        "discovery": {},
        **overrides,
    }


def test_completion_is_not_correctness_and_failures_stay_in_quality_denominator():
    result = metrics(
        [
            row(),
            row(
                status="error",
                decision=None,
                decision_correct=False,
                overall=0,
                model_decisions=2,
                api_receipt_records=2,
            ),
            row(expected_outcome="abstain", decision="abstain", overall=100, mergeable=False),
        ]
    )
    assert result["completion_rate"] == 2 / 3
    assert result["patch_recall"] == 0.5
    assert result["no_change_recall"] == 1
    assert result["documentation_quality"] == 25
    assert result["p0_clean_delivery_rate"] == 0
    assert result["correct_decisions_per_100_model_decisions"] == 20


def test_pairs_require_identical_ids_and_preserve_discordance():
    a = {"a": row(), "b": row(decision_correct=False)}
    b = {"a": row(decision_correct=False), "b": row()}
    assert paired_counts(a, b) == {
        "both_correct": 0,
        "old_only_correct": 1,
        "new_only_correct": 1,
        "both_wrong": 0,
    }
    with pytest.raises(ValueError):
        paired_counts(a, {"a": b["a"]})
    assert paired_bootstrap(a, b, samples=100) == paired_bootstrap(a, b, samples=100)


def test_rejection_is_not_automatically_classified_as_wrong():
    assessment = failure_classification(
        row(
            status="error",
            phase="VERIFY",
            review={"ready": False},
            execution_error="Structured review remains unresolved",
        )
    )
    assert assessment["primary"] == "F" and not assessment["false_rejection_proven"]
    assert "E" not in assessment["secondary"]


def test_official_p0_failure_after_ready_review_is_visible():
    assessment = failure_classification(row())
    assert assessment["primary"] == "D" and "E" in assessment["secondary"]


def test_tool_counts_exclude_plan_and_review_roles_and_support_zero_api_calls(tmp_path):
    folder = tmp_path / "tasks/case"
    folder.mkdir(parents=True)
    (folder / "result.json").write_text(json.dumps({"status": "error"}), encoding="utf-8")
    with sqlite3.connect(folder / "runs.sqlite") as db:
        db.execute("create table runs (state text, model_calls integer)")
        db.execute("insert into runs values ('{}',0)")
        db.execute("create table tool_invocations (name text,result text)")
        for name in ["plan", "review", "abstain", "search", "read_many", "propose"]:
            db.execute("insert into tool_invocations values (?,?)", (name, "{}"))
    result = task_measurements(tmp_path, "case", row())
    assert result["operational_tool_calls"] == 3
    assert result["model_decisions"] == 0 and result["api_receipt_records"] == 0
