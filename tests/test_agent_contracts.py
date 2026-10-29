import json
from dataclasses import asdict

import pytest
from pydantic import ValidationError

from shopresolve.agent.contracts import FinalAnswer, Intent, IntentFrame, decision_adapter
from shopresolve.agent.control import (
    AuthorizedEntity,
    GoalSnapshot,
    ProgressMonitor,
    check_completion,
    resolve_entity,
    validate_intent_sources,
)
from shopresolve.agent.entities import narrow_candidates
from shopresolve.errors import DomainError


def frame(mode="INQUIRE"):
    return IntentFrame.model_validate_json(
        '{"source_message_ids":["m1"],"items":[{"intent":"RETURN_REQUEST",'
        f'"mode":"{mode}","target_mentions":["耳机"]' + "}]}"
    )


def test_intent_mode_is_not_an_execution_authorization():
    assert frame().items[0].mode == "INQUIRE"
    assert frame("REQUEST_ACTION").items[0].mode == "REQUEST_ACTION"
    with pytest.raises(ValidationError):
        IntentFrame.model_validate_json(
            '{"owner_id":"bob","source_message_ids":["m1"],'
            '"items":[{"intent":"CANCEL_ORDER","mode":"REQUEST_ACTION"}]}'
        )


def test_intent_sources_must_belong_to_current_conversation():
    with pytest.raises(DomainError, match="unavailable messages"):
        validate_intent_sources(frame(), {"someone-elses-message"}, set())
    validate_intent_sources(frame(), {"m1"}, set())


def test_unregistered_slot_from_live_protocol_failure_is_rejected():
    value = {
        "source_message_ids": ["m1"],
        "items": [
            {
                "intent": "ORDER_QUERY",
                "mode": "INQUIRE",
                "slot_candidates": [
                    {"name": "order_reference", "value": "order-pending", "source_message_id": "m1"}
                ],
            }
        ],
    }
    with pytest.raises(ValidationError):
        IntentFrame.model_validate_json(json.dumps(value))
    value["items"][0]["slot_candidates"][0]["name"] = "order_id"
    assert IntentFrame.model_validate_json(json.dumps(value)).items[0].slot_candidates


def test_entity_ambiguity_does_not_guess():
    a = AuthorizedEntity("a", "耳机 A", "read1")
    b = AuthorizedEntity("b", "耳机 B", "read1")
    assert resolve_entity([a, a]) == a
    for candidates in ([], [a, b]):
        with pytest.raises(DomainError):
            resolve_entity(candidates)


def test_explicit_order_mentions_narrow_only_authorized_candidates():
    rows = [
        {"id": oid, "status": "PENDING_SHIPMENT", "items": [{"name": "耳机"}]}
        for oid in ("order-pending", "order-pending-extra", "order-other")
    ]
    assert narrow_candidates(rows, "取消order-pending") == rows[:1]
    assert narrow_candidates(rows, "order-pending和order-other") == [rows[0], rows[2]]
    assert narrow_candidates(rows, "order-unknown") == []
    assert narrow_candidates(rows, "不要取消order-pending") == rows


def test_partial_tool_json_never_becomes_a_decision():
    with pytest.raises(ValidationError):
        decision_adapter.validate_json('{"kind":"tool_calls","calls":[{"name":')


def test_cancel_proposal_cannot_contain_a_generated_return_reason():
    proposal = {
        "kind": "propose_action",
        "goal_id": "g1",
        "action": "CANCEL_ORDER",
        "order_id": "order-pending",
        "reason": "User wants a cancellation",
    }
    with pytest.raises(ValidationError):
        decision_adapter.validate_json(json.dumps(proposal))
    proposal["reason"] = None
    assert decision_adapter.validate_json(json.dumps(proposal)).action == "CANCEL_ORDER"


def test_duplicate_tool_call_ids_rejected():
    with pytest.raises(ValidationError):
        decision_adapter.validate_python(
            {
                "kind": "tool_calls",
                "calls": [
                    {"call_id": "c1", "name": "get_order", "arguments": {}},
                    {"call_id": "c1", "name": "get_order", "arguments": {}},
                ],
            }
        )


def test_premature_final_answer_is_rejected():
    answer = FinalAnswer(kind="final_answer", text="已经退款", goal_ids=["g1"])
    goal = GoalSnapshot("g1", Intent.CANCEL_ORDER, "REQUEST_ACTION", "WAITING_APPROVAL")
    assert not check_completion(
        answer, [goal], verified_operations=set(), accessible_evidence=set()
    ).allowed


def test_write_completion_requires_verified_receipt():
    answer = FinalAnswer(kind="final_answer", text="完成", goal_ids=["g1"])
    goal = GoalSnapshot("g1", Intent.CANCEL_ORDER, "REQUEST_ACTION", "COMPLETED", ("op1",))
    denied = check_completion(answer, [goal], verified_operations=set(), accessible_evidence=set())
    assert not denied.allowed
    allowed = check_completion(
        answer, [goal], verified_operations={"op1"}, accessible_evidence=set()
    )
    assert allowed.allowed and allowed.outcome == "FULFILLED"


def test_all_goals_must_be_included_and_waits_do_not_complete():
    answer = FinalAnswer(kind="final_answer", text="完成", goal_ids=["g1"])
    goals = [
        GoalSnapshot("g1", Intent.ORDER_QUERY, "INQUIRE", "COMPLETED"),
        GoalSnapshot("g2", Intent.RETURN_REQUEST, "REQUEST_ACTION", "WAITING_APPROVAL"),
    ]
    assert not check_completion(
        answer, goals, verified_operations=set(), accessible_evidence=set()
    ).allowed


def test_legal_decline_is_not_fulfilled_outcome():
    answer = FinalAnswer(
        kind="final_answer", text="已发货，不能取消", goal_ids=["g1"], evidence_ids=["e1"]
    )
    goal = GoalSnapshot(
        "g1",
        Intent.CANCEL_ORDER,
        "REQUEST_ACTION",
        "DECLINED",
        evidence_required=True,
        evidence_ids=("e1",),
    )
    report = check_completion(answer, [goal], verified_operations=set(), accessible_evidence={"e1"})
    assert report.allowed and report.outcome == "DECLINED"


def test_loop_repeat_counter_survives_snapshot_and_allows_one_recovery():
    monitor = ProgressMonitor()
    assert monitor.observe("search", {"query": "退货"}, 1, "index1", made_progress=False)
    restored = ProgressMonitor(**asdict(monitor))
    assert not restored.observe("search", {"query": "退货"}, 1, "index1", made_progress=False)
    assert restored.allow_recovery()
    assert not restored.allow_recovery()
    assert restored.observe("read", {}, 2, "new-evidence", made_progress=True)
