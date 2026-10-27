import pytest
from sqlalchemy import func, select

from shopresolve.agent.contracts import FinalAnswer, Intent
from shopresolve.agent.control import GoalSnapshot, check_completion
from shopresolve.agent.entities import explicit_choice, narrow_candidates
from shopresolve.agent.evidence import assess
from shopresolve.agent.gateway import DemoGateway, ScriptedGateway
from shopresolve.agent.tools import ToolRouter
from shopresolve.models import Operation, Policy, Proposal, Refund, Run
from shopresolve.retrieval.index import PolicyIndex
from shopresolve.schemas import Decision
from shopresolve.service import digest
from tests.test_runs import SimulatedProcessExit, drive, new_run


def test_explicit_candidate_choice_does_not_interpret_agreement_or_quantity():
    rows = [{"id": "order-a"}, {"id": "order-b"}]
    assert explicit_choice("选第二个", rows) == "order-b"
    for text in ("好的", "退两个", "第二个和第一个", "第九个", "order-a order-b", "不要选order-a"):
        assert explicit_choice(text, rows) is None


def test_candidate_search_is_lexical_and_status_constrained():
    rows = [
        {"id": "a", "status": "PENDING_SHIPMENT", "items": [{"name": "通勤电脑包"}]},
        {"id": "b", "status": "SHIPPED", "items": [{"name": "合成耳机"}]},
    ]
    assert [r["id"] for r in narrow_candidates(rows, "未发货的电脑包")] == ["a"]
    assert narrow_candidates(rows, "未发货的耳机") == []


async def test_unique_authorized_natural_order_reaches_proposal_without_id(commerce):
    store, rid, runtime = await new_run(commerce, "帮我取消未发货订单")
    waiting = await drive(store, rid, runtime)
    assert waiting["status"] == "WAITING_APPROVAL", waiting
    goal = waiting["goals"][0]
    assert goal["slots"]["order_id"]["value"] == "order-pending"
    assert goal["slots"]["order_id"]["verification_state"] == "verified"
    assert "order-bob" not in str(goal.get("entity_candidates"))
    assert goal["slots"]["order_id"]["source_type"] == "tool"


async def test_ambiguous_product_pauses_then_explicit_choice_preserves_quantity(commerce):
    store, rid, runtime = await new_run(commerce, "申请退货耳机 数量1 原因：故障")
    waiting = await drive(store, rid, runtime)
    assert waiting["status"] == "WAITING_USER", waiting
    candidates = waiting["goals"][0]["entity_candidates"]
    assert len(candidates) > 1
    assert all(row["id"] != "order-bob" for row in candidates)
    ordinal = next(i for i, row in enumerate(candidates, 1) if row["id"] == "order-delivered")
    await store.resume(
        "alice",
        rid,
        {
            "text": f"第{ordinal}个",
            "client_message_id": "choice",
            "expected_version": waiting["version"],
            "wait_generation": waiting["resume_generation"],
        },
        "choice",
    )
    proposal = await drive(store, rid, runtime)
    assert proposal["status"] == "WAITING_APPROVAL", proposal
    goal = proposal["goals"][0]
    assert goal["slots"]["order_id"]["value"] == "order-delivered"
    assert goal["slots"]["item_id"]["value"] == "item-delivered"
    assert goal["slots"]["quantity"]["value"] == 1


async def test_candidate_agreement_is_not_order_selection_or_approval(commerce):
    store, rid, runtime = await new_run(commerce, "申请退货耳机")
    waiting = await drive(store, rid, runtime)
    await store.resume(
        "alice",
        rid,
        {
            "text": "好的",
            "client_message_id": "ok",
            "expected_version": waiting["version"],
            "wait_generation": waiting["resume_generation"],
        },
        "ok",
    )
    again = await drive(store, rid, runtime)
    assert again["status"] == "WAITING_USER", again
    async with commerce[1]() as session:
        assert await session.scalar(select(func.count()).select_from(Proposal)) == 0


async def test_unsupported_goal_is_explicit_and_cannot_fake_ticket(commerce):
    store, rid, runtime = await new_run(commerce, "帮我转人工")
    result = await drive(store, rid, runtime)
    assert result["outcome"] == "DECLINED", result
    assert result["stop_reason"] == "GOALS_DISPOSED"
    assert "未创建人工工单" in result["answer"]
    assert result["goals"][0]["decline_reason"] == "UNSUPPORTED_CAPABILITY"


async def test_supported_and_unsupported_goals_have_separate_dispositions(commerce):
    store, rid, runtime = await new_run(commerce, "查询 order-pending；帮我换货")
    result = await drive(store, rid, runtime)
    assert result["outcome"] == "PARTIAL", result
    assert [g["status"] for g in result["goals"]] == ["COMPLETED", "DECLINED"]


def test_completion_cannot_borrow_another_goals_citation():
    answer = FinalAnswer(
        kind="final_answer", text="结果", goal_ids=["g1", "g2"], evidence_ids=["e1"]
    )
    goals = [
        GoalSnapshot(
            "g1",
            Intent.POLICY_QUERY,
            "INQUIRE",
            "COMPLETED",
            evidence_required=True,
            evidence_ids=("e1",),
        ),
        GoalSnapshot(
            "g2",
            Intent.POLICY_QUERY,
            "INQUIRE",
            "COMPLETED",
            evidence_required=True,
            evidence_ids=("e2",),
        ),
    ]
    report = check_completion(
        answer, goals, verified_operations=set(), accessible_evidence={"e1", "e2"}
    )
    assert not report.allowed and "MISSING_EVIDENCE:g2" in report.errors


def test_policy_scope_rejects_unrelated_evidence_and_tampered_quote():
    goal = {"intent": "CANCEL_ORDER", "mentions": ["取消订单的条件是什么"]}
    doc = {
        "id": "e",
        "quote": "物流没有预计送达",
        "content_hash": digest("物流没有预计送达"),
        "policy_sections": ["logistics-eta"],
    }
    assert not assess(goal, [doc])["supported"]
    doc["policy_sections"] = ["cancel-pending"]
    doc["content_hash"] = "tampered"
    assert not assess(goal, [doc])["supported"]


async def test_irrelevant_policy_hits_never_complete_and_rewrites_are_bounded(commerce):
    store, rid, runtime = await new_run(commerce, "取消订单的条件是什么？")
    async with commerce[1]() as session:
        bundle_hash = (await session.get(Policy, "demo-policy-v1")).content_hash

    class IrrelevantRetriever:
        policy_bundle_hash = bundle_hash

        def search(self, *args, **kwargs):
            return [
                {
                    "id": "irrelevant",
                    "quote": "物流",
                    "content_hash": digest("物流"),
                    "policy_sections": ["logistics-eta"],
                }
            ]

    runtime.tools = ToolRouter(commerce[0], IrrelevantRetriever())
    result = await drive(store, rid, runtime)
    assert result["status"] == "WAITING_USER", result
    assert result["goals"][0]["status"] != "COMPLETED"
    assert len(result["goals"][0]["policy_requests"]) == 3


async def test_known_policy_gap_does_not_invent_shipping_fee_rule(commerce):
    store, rid, runtime = await new_run(commerce, "退货运费谁承担，政策是什么？")
    runtime.tools = ToolRouter(commerce[0], PolicyIndex("data/policies/demo.json"))
    result = await drive(store, rid, runtime)
    assert result["status"] == "WAITING_USER", result
    assert result["goals"][0]["policy_assessment"]["unsupported_topic"]


async def test_saved_model_decision_replays_without_new_model_request(commerce):
    store, rid, runtime = await new_run(commerce)

    class CountingDemo(DemoGateway):
        calls = 0

        async def request(self, phase, context):
            self.calls += 1
            return await super().request(phase, context)

    gateway = CountingDemo()
    runtime.gateway = gateway

    async def crash(point, lease):
        state, _, _ = await store.load(lease)
        if point == "after_model_response_commit" and state["pending"]["phase"] == "DECISION":
            raise SimulatedProcessExit()

    runtime.fault_hook = crash
    lease = await store.claim(rid, "crashed")
    with pytest.raises(SimulatedProcessExit):
        await runtime.run(lease)
    calls = gateway.calls
    async with commerce[1].begin() as session:
        (await session.get(Run, rid)).lease_until = 0
    runtime.fault_hook = None
    result = await drive(store, rid, runtime, "recovered")
    assert result["outcome"] == "FULFILLED", result
    assert gateway.calls == calls


async def test_verified_business_result_finishes_without_another_provider_call(commerce):
    store, rid, runtime = await new_run(commerce, "帮我取消 order-pending")
    waiting = await drive(store, rid, runtime)
    await commerce[0].decide(
        "alice",
        waiting["goals"][0]["proposal_id"],
        Decision(decision="approve", expected_version=1),
        "approve",
    )
    runtime.gateway = ScriptedGateway([RuntimeError("provider unavailable")])
    result = await drive(store, rid, runtime)
    assert result["outcome"] == "FULFILLED", result
    assert runtime.gateway.calls == 0
    async with commerce[1]() as session:
        assert await session.scalar(select(func.count()).select_from(Refund)) == 1
        assert await session.scalar(select(func.count()).select_from(Operation)) == 1


async def test_model_cannot_read_an_unresolved_arbitrary_target(commerce):
    store, rid, runtime = await new_run(commerce, "查一下我的耳机订单")
    runtime.gateway = ScriptedGateway(
        [
            lambda ctx: {
                "source_message_ids": [ctx["messages"][-1]["id"]],
                "items": [
                    {"intent": "ORDER_QUERY", "mode": "INQUIRE", "target_mentions": ["耳机"]}
                ],
            },
            {
                "kind": "tool_calls",
                "calls": [
                    {
                        "call_id": "bad",
                        "name": "get_order",
                        "arguments": {"goal_id": "g1", "order_id": "order-pending"},
                    }
                ],
            },
            {
                "kind": "tool_calls",
                "calls": [
                    {
                        "call_id": "bad2",
                        "name": "get_order",
                        "arguments": {"goal_id": "g1", "order_id": "order-pending"},
                    }
                ],
            },
        ]
    )
    result = await drive(store, rid, runtime)
    assert result["stop_reason"] == "MODEL_PROTOCOL", result
    assert not result["goals"][0].get("order_observation")


@pytest.mark.parametrize(
    "message,allowed,reason",
    [
        ("order-pending 可以取消吗？", True, None),
        ("order-shipped 可以取消吗？", False, "NOT_CANCELLABLE"),
        ("order-expired 可以退货吗？", False, "RETURN_WINDOW_CLOSED"),
    ],
)
async def test_specific_order_inquiry_checks_rules_without_creating_action(
    commerce, message, allowed, reason
):
    store, rid, runtime = await new_run(commerce, message)
    result = await drive(store, rid, runtime)
    assert result["outcome"] == "FULFILLED", result
    eligibility = result["goals"][0]["eligibility_observation"]
    action = "REQUEST_RETURN" if "退货" in message else "CANCEL_ORDER"
    facts = [row for row in eligibility["actions"] if row["action"] == action]
    assert facts and all(row["allowed"] == allowed and row["reason"] == reason for row in facts)
    assert "未创建提案" in result["answer"]
    async with commerce[1]() as session:
        assert await session.scalar(select(func.count()).select_from(Proposal)) == 0
        assert await session.scalar(select(func.count()).select_from(Operation)) == 0


async def test_policy_change_between_retrieval_and_proposal_fails_closed(commerce):
    store, rid, runtime = await new_run(commerce, "帮我取消 order-pending")

    class ChangingPolicy(DemoGateway):
        changed = False

        async def request(self, phase, context):
            if (
                phase == "DECISION"
                and not self.changed
                and context["goals"]
                and context["goals"][0].get("policy_assessment", {}).get("supported")
            ):
                self.changed = True
                async with commerce[1].begin() as session:
                    old = await session.get(Policy, "demo-policy-v1")
                    old.active = False
                    session.add(
                        Policy(
                            version="demo-policy-v2",
                            active=True,
                            return_days=old.return_days,
                            excluded_categories=old.excluded_categories,
                            document=old.document,
                            content_hash=old.content_hash,
                        )
                    )
            return await super().request(phase, context)

    runtime.gateway = ChangingPolicy()
    result = await drive(store, rid, runtime)
    assert result["stop_reason"] == "POLICY_EVIDENCE_STALE", result
    async with commerce[1]() as session:
        assert await session.scalar(select(func.count()).select_from(Proposal)) == 0
        assert await session.scalar(select(func.count()).select_from(Refund)) == 0


async def test_refund_arrival_date_is_not_answered_from_generic_cancel_policy(commerce):
    store, rid, runtime = await new_run(commerce, "取消后退款多久到账？")
    result = await drive(store, rid, runtime)
    assert result["status"] == "WAITING_USER", result
    assert result["goals"][0]["policy_assessment"]["unsupported_topic"]
