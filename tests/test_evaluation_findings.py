from shopresolve.agent.gateway import DemoGateway, ScriptedGateway
from shopresolve.models import Run
from tests.test_runs import drive, new_run


def read_batch(order_id):
    return {
        "kind": "tool_calls",
        "calls": [
            {
                "call_id": "order",
                "name": "get_order",
                "arguments": {"goal_id": "g1", "order_id": order_id},
            },
            {
                "call_id": "eligibility",
                "name": "calculate_eligibility",
                "arguments": {"goal_id": "g1", "order_id": order_id},
            },
            {
                "call_id": "policy",
                "name": "search_policy",
                "arguments": {"goal_id": "g1", "query": "退货七天 品类例外 申请不等于退款 数量"},
            },
        ],
    }


async def test_expired_return_can_close_from_authoritative_rule_denial(commerce):
    store, rid, runtime = await new_run(
        commerce, "申请退货 order-expired item-expired 数量1 原因：故障"
    )
    runtime.gateway = ScriptedGateway([DemoGateway()._intents, read_batch("order-expired")])
    original_search = runtime.tools.retriever.search
    runtime.tools.retriever.search = lambda *args, **kwargs: [
        e for e in original_search(*args, **kwargs) if e["parent_id"] == "return-window"
    ]
    result = await drive(store, rid, runtime)
    assert result["status"] == "SUCCEEDED", result
    assert result["outcome"] == "DECLINED"
    assert result["goals"][0]["decline_reason"] == "RETURN_WINDOW_CLOSED"
    assert result["results"] == []
    assert result["model_calls"] == 2


async def test_quantity_refinement_keeps_same_verified_target_and_rechecks_evidence(commerce):
    store, rid, runtime = await new_run(
        commerce, "办理退货 order-delivered item-delivered 原因：故障"
    )
    runtime.gateway = ScriptedGateway(
        [
            DemoGateway()._intents,
            read_batch("order-delivered"),
            {
                "kind": "ask_user",
                "question": "退几件？",
                "goal_ids": ["g1"],
                "missing_slots": ["quantity"],
            },
        ]
    )
    waiting = await drive(store, rid, runtime)
    assert waiting["status"] == "WAITING_USER", waiting
    await store.resume(
        "alice",
        rid,
        {
            "text": "数量1",
            "client_message_id": "qty",
            "expected_version": waiting["version"],
            "wait_generation": waiting["resume_generation"],
        },
        "qty",
    )

    def refined(context):
        old, new = context["messages"][0]["id"], context["messages"][-1]["id"]
        return {
            "source_message_ids": [old, new],
            "items": [
                {
                    "intent": "RETURN_REQUEST",
                    "mode": "REQUEST_ACTION",
                    "relation_to_goal": "refine",
                    "existing_goal_id": "g1",
                    "target_mentions": ["数量1"],
                    "slot_candidates": [
                        {"name": "order_id", "value": "order-delivered", "source_message_id": old},
                        {"name": "item_id", "value": "item-delivered", "source_message_id": old},
                        {"name": "quantity", "value": 1, "source_message_id": new},
                    ],
                }
            ],
        }

    runtime.gateway = ScriptedGateway(
        [
            refined,
            {
                "kind": "propose_action",
                "goal_id": "g1",
                "action": "REQUEST_RETURN",
                "order_id": "order-delivered",
                "item_id": "item-delivered",
                "quantity": 1,
                "reason": "故障",
            },
        ]
    )
    result = await drive(store, rid, runtime)
    assert result["status"] == "WAITING_APPROVAL", result
    assert result["goals"][0]["slots"]["order_id"]["verification_state"] == "verified"
    async with commerce[1]() as session:
        state = (await session.get(Run, rid)).state
    assert state["goals"][0]["policy_assessment"]["supported"]
