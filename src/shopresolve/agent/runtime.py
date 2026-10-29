"""Single-agent loop over persistent state; no framework-owned hidden execution."""

import asyncio
import copy
import json
import time
from dataclasses import asdict

import httpx
from pydantic import ValidationError

from shopresolve.agent.capabilities import SUPPORTED_INTENTS
from shopresolve.agent.context import ContextBuilder
from shopresolve.agent.contracts import (
    AskUser,
    FinalAnswer,
    Intent,
    IntentFrame,
    ProposeAction,
    ToolCalls,
    decision_adapter,
)
from shopresolve.agent.control import (
    GoalSnapshot,
    ProgressMonitor,
    check_completion,
    validate_intent_sources,
)
from shopresolve.agent.entities import explicit_choice
from shopresolve.agent.evidence import assess, policy_goal_complete
from shopresolve.errors import DomainError
from shopresolve.schemas import PrepareAction
from shopresolve.service import digest

DISPOSED = {"COMPLETED", "DECLINED", "WITHDRAWN", "FAILED", "ESCALATED"}


class AgentRuntime:
    def __init__(self, store, gateway, tools, *, fault_hook=None):
        self.store, self.gateway, self.tools = store, gateway, tools
        self.context = ContextBuilder()
        self.fault_hook = fault_hook

    async def fault(self, point, lease):
        if self.fault_hook:
            await self.fault_hook(point, lease)

    async def run(self, lease):
        state = None
        try:
            while True:
                started = time.monotonic()
                state, calls, active_ms = await self.store.load(lease)
                state = await self._settle_approvals(lease, state)
                unresolved = [g for g in state["goals"] if g["status"] not in DISPOSED]
                if (
                    unresolved
                    and all(g["status"] == "WAITING_APPROVAL" for g in unresolved)
                    and state["phase"] != "INTENT"
                ):
                    await self.store.pause(lease, state, "WAITING_APPROVAL")
                    return
                config = state["config"]
                # Verified, terminal dispositions need no extra paid request to narrate them.
                if (
                    state["phase"] != "INTENT"
                    and state["goals"]
                    and all(g["status"] in DISPOSED for g in state["goals"])
                    and state.get("pending") is None
                ):
                    await self._act(
                        lease,
                        state,
                        -1,
                        FinalAnswer(
                            kind="final_answer",
                            text="服务端核验结果",
                            goal_ids=[g["id"] for g in state["goals"]],
                            evidence_ids=list(
                                dict.fromkeys(
                                    eid for g in state["goals"] for eid in g.get("evidence_ids", [])
                                )
                            ),
                        ),
                    )
                    return
                if active_ms >= config["active_budget_ms"]:
                    raise DomainError("ACTIVE_BUDGET", "Active execution budget exhausted")
                pending = state.get("pending")
                if pending is None:
                    if self.context.refresh_summary(state):
                        await self.store.save(lease, state, kind="memory.summary_updated")
                    context = self.context.build(state, self.tools.descriptions())
                    seq = await self.store.reserve_model(
                        lease, state["phase"], digest(context), config["model_limit"]
                    )
                    request_started = time.monotonic()
                    try:
                        remaining_seconds = max(
                            0.001, (config["active_budget_ms"] - active_ms) / 1000
                        )
                        async with asyncio.timeout(
                            min(config["model_timeout_seconds"], remaining_seconds)
                        ):
                            reply = await self.gateway.request(state["phase"], context)
                    except Exception as exc:
                        await self.store.model_failed(
                            lease,
                            seq,
                            int((time.monotonic() - request_started) * 1000),
                            type(exc).__name__,
                        )
                        raise
                    state = await self.store.save_decision(
                        lease,
                        state,
                        seq,
                        reply.output,
                        reply.usage,
                        elapsed_ms=int((time.monotonic() - request_started) * 1000),
                    )
                    started = time.monotonic()
                    await self.fault("after_model_response_commit", lease)
                    pending = state["pending"]
                try:
                    if pending["phase"] == "INTENT":
                        frame = IntentFrame.model_validate_json(json.dumps(pending["response"]))
                        validate_intent_sources(
                            frame, set(state["message_ids"]), {g["id"] for g in state["goals"]}
                        )
                        candidate = copy.deepcopy(state)
                        self._apply_intent(candidate, frame)
                        state = candidate
                        state["phase"] = "DECISION"
                    else:
                        decision = decision_adapter.validate_json(json.dumps(pending["response"]))
                        disposition = await self._act(lease, state, pending["step"], decision)
                        if disposition == "PAUSE":
                            return
                        if disposition == "FINISH":
                            return
                    state["pending"] = None
                    state["protocol_errors"] = 0
                    state.pop("last_error_detail", None)
                except (ValidationError, DomainError) as exc:
                    if isinstance(exc, DomainError) and exc.code in {
                        "LEASE_LOST",
                        "NO_PROGRESS",
                        "MODEL_BUDGET",
                        "ACTIVE_BUDGET",
                        "RETRIEVAL_BUDGET",
                        "POLICY_EVIDENCE_STALE",
                    }:
                        raise
                    state["pending"] = None
                    state["last_error"] = (
                        exc.code if isinstance(exc, DomainError) else "INVALID_MODEL_OUTPUT"
                    )
                    state["last_error_detail"] = (
                        exc.message
                        if isinstance(exc, DomainError)
                        else "; ".join(
                            f"{'.'.join(map(str, error['loc']))}: {error['msg']}"
                            for error in exc.errors(include_input=False, include_url=False)
                        )
                    )[:1000]
                    state["protocol_errors"] = state.get("protocol_errors", 0) + 1
                    if state["protocol_errors"] >= 2:
                        raise DomainError(
                            "MODEL_PROTOCOL", "Model repeatedly violated its contract"
                        ) from exc
                elapsed = int((time.monotonic() - started) * 1000)
                await self.store.save(lease, state, elapsed_ms=elapsed)
        except DomainError as exc:
            if exc.code == "LEASE_LOST":
                return  # The current worker must not write a terminal state.
            if state is not None:
                state["pending"] = None
                state["answer"] = self.render(state, fallback=exc.message)
                try:
                    await self.store.finish(
                        lease,
                        state,
                        "FAILED",
                        "PARTIAL" if state["results"] else "UNRESOLVED",
                        exc.code,
                    )
                except DomainError as finish_error:
                    if finish_error.code != "LEASE_LOST":
                        raise
        except (TimeoutError, OSError, RuntimeError, ValueError, httpx.HTTPError, KeyError) as exc:
            if state is not None:
                state["answer"] = self.render(state, fallback="模型服务或执行依赖暂不可用。")
                try:
                    await self.store.finish(
                        lease,
                        state,
                        "FAILED",
                        "PARTIAL" if state["results"] else "UNRESOLVED",
                        type(exc).__name__,
                    )
                except DomainError as finish_error:
                    if finish_error.code != "LEASE_LOST":
                        raise

    def _apply_intent(self, state, frame):
        by_id = {g["id"]: g for g in state["goals"]}
        message_map = {m["id"]: m["text"] for m in state["messages"]}
        for item in frame.items:
            if any(
                not any(mention in message_map[mid] for mid in frame.source_message_ids)
                for mention in item.target_mentions
            ):
                raise DomainError("INVALID_SOURCE", "Target mentions must quote user messages")
            previous_target = None
            if item.existing_goal_id:
                goal = by_id[item.existing_goal_id]
                previous_target = (
                    goal["intent"],
                    goal["mode"],
                    goal.get("slots", {}).get("order_id", {}).get("value"),
                    goal.get("slots", {}).get("item_id", {}).get("value"),
                )
                if goal["status"] == "COMPLETED":
                    raise DomainError("GOAL_COMMITTED", "Committed actions cannot be rewritten")
                goal["revision"] += 1
                if item.relation_to_goal == "withdraw":
                    goal["status"] = "WITHDRAWN"
                    continue
            else:
                if len(state["goals"]) >= 8:
                    raise DomainError("GOAL_LIMIT", "Please split the task into smaller requests")
                goal = {
                    "id": f"g{len(state['goals']) + 1}",
                    "revision": 1,
                    "slots": {},
                    "operation_ids": [],
                    "evidence_ids": [],
                }
                state["goals"].append(goal)
                by_id[goal["id"]] = goal
            if item.relation_to_goal == "replace":
                goal["slots"] = {}
                goal.pop("order_observation", None)
                goal.pop("entity_candidates", None)
            mentions = list(
                dict.fromkeys(
                    (goal.get("mentions", []) if item.relation_to_goal == "refine" else [])
                    + item.target_mentions
                )
            )[-10:]
            goal.update(
                intent=item.intent.value,
                mode=item.mode,
                mentions=mentions,
                status="UNRESOLVED",
            )
            choice = explicit_choice(
                state["messages"][-1]["text"], goal.get("entity_candidates", [])
            )
            if choice:
                goal["slots"]["order_id"] = {
                    "value": choice,
                    "source_type": "user",
                    "source_ref": state["messages"][-1]["id"],
                    "verification_state": "asserted",
                }
                goal.pop("order_observation", None)
            for slot in item.slot_candidates:
                if slot.name not in {"order_id", "item_id", "quantity", "reason"}:
                    raise DomainError("INVALID_SLOT", "Unsupported slot")
                if (
                    slot.name in {"order_id", "item_id"}
                    and str(slot.value) not in message_map[slot.source_message_id]
                    and not (slot.name == "order_id" and slot.value == choice)
                ):
                    raise DomainError("INVALID_SOURCE", "Entity ID was not supplied by the user")
                old = goal["slots"].get(slot.name)
                if slot.name == "order_id" and old and old["value"] != slot.value:
                    goal.pop("order_observation", None)
                    goal["slots"] = {}
                if (
                    slot.name in {"order_id", "item_id"}
                    and old
                    and old["value"] == slot.value
                    and old.get("verification_state") == "verified"
                ):
                    # Restating an unchanged, tool-verified ID is not a new target.
                    old["asserted_again_by"] = slot.source_message_id
                    continue
                goal["slots"][slot.name] = {
                    "value": slot.value,
                    "source_type": "user",
                    "source_ref": slot.source_message_id,
                    "verification_state": "asserted",
                    "observed_at": int(self.tools.service.clock()),
                }
            current_target = (
                goal["intent"],
                goal["mode"],
                goal.get("slots", {}).get("order_id", {}).get("value"),
                goal.get("slots", {}).get("item_id", {}).get("value"),
            )
            if item.relation_to_goal == "refine" and previous_target == current_target:
                assessment = assess(goal, list(goal.get("policy_evidence", {}).values()))
                goal["policy_assessment"] = assessment
                goal["evidence_ids"] = assessment["evidence_ids"]
            else:
                goal["evidence_ids"] = []
                goal["policy_evidence"] = {}
                goal.pop("policy_assessment", None)
                goal.pop("eligibility_observation", None)
            if item.intent.value not in SUPPORTED_INTENTS:
                goal["status"], goal["decline_reason"] = "DECLINED", "UNSUPPORTED_CAPABILITY"
                goal["capability_notice"] = (
                    "当前不支持该业务，未创建人工工单、未执行换货或商品推荐。"
                )
        state["intent_frame"] = frame.model_dump(mode="json")

    @staticmethod
    def goal(state, goal_id):
        found = next((g for g in state["goals"] if g["id"] == goal_id), None)
        if found is None:
            raise DomainError("INVALID_GOAL", "Unknown task goal")
        return found

    async def _act(self, lease, state, step, decision):
        if isinstance(decision, ToolCalls):
            for call in decision.calls:
                goal = self.goal(state, call.arguments.get("goal_id"))
                if goal["status"] in DISPOSED | {"WAITING_APPROVAL"}:
                    raise DomainError(
                        "GOAL_CLOSED", "Cannot issue new tools for a disposed/waiting goal"
                    )
                if call.name == "resolve_order":
                    if call.arguments.get("mention") not in goal.get("mentions", []):
                        raise DomainError(
                            "INVALID_SOURCE", "Resolve only a verbatim target mention"
                        )
                if call.name == "search_policy":
                    requests = goal.setdefault("policy_requests", [])
                    identifier = f"{step}:{call.call_id}"
                    if identifier not in requests:
                        if len(requests) >= 3:
                            raise DomainError(
                                "RETRIEVAL_BUDGET", "At most two policy query rewrites"
                            )
                        requests.append(identifier)
                expected_order = goal.get("slots", {}).get("order_id", {}).get("value")
                if call.name in {"get_order", "get_shipment", "calculate_eligibility"}:
                    if expected_order is None or call.arguments.get("order_id") != expected_order:
                        raise DomainError(
                            "TARGET_MISMATCH", "Read target differs from the user's order"
                        )
            # Persist retrieval reservations before IO; replaying a saved decision uses same IDs.
            await self.store.save(lease, state)
            persistence_lock = asyncio.Lock()
            results = await asyncio.gather(
                *[self._read(lease, state, step, call, persistence_lock) for call in decision.calls]
            )
            # Results are applied in original model order, regardless of completion order.
            for call, result in zip(decision.calls, results, strict=True):
                goal = self.goal(state, call.arguments["goal_id"])
                state["observations"].append(
                    {"call_id": call.call_id, "name": call.name, "result": result}
                )
                made_progress = False
                if result["status"] == "ok":
                    if call.name == "calculate_eligibility":
                        made_progress = goal.get("eligibility_observation") != result["data"]
                        goal["eligibility_observation"] = result["data"]
                        if goal["mode"] == "INQUIRE" and policy_goal_complete(goal):
                            goal["status"] = "COMPLETED"
                    if call.name == "resolve_order":
                        candidates = result["data"]["candidates"]
                        previous = goal.get("entity_candidates")
                        goal["entity_candidates"] = candidates
                        goal["entity_source_ref"] = call.call_id
                        made_progress = previous != candidates
                        if len(candidates) == 1 and not result["data"]["truncated"]:
                            goal["slots"]["order_id"] = {
                                "value": candidates[0]["id"],
                                "source_type": "tool",
                                "source_ref": call.call_id,
                                "verification_state": "verified",
                            }
                        else:
                            goal["status"] = "WAITING_INPUT"
                            labels = [
                                f"第{i}个：{row['id']}，{row['status']}，"
                                + "/".join(item["name"] for item in row["items"])
                                for i, row in enumerate(candidates, 1)
                            ]
                            state["question"] = (
                                "请明确选择订单（订单编号或第几个）；不能唯一确定。\n"
                                + "\n".join(labels)
                            )
                            state["pending"] = None
                            await self.store.pause(lease, state, "WAITING_USER")
                            return "PAUSE"
                    if call.name in {"get_order", "get_shipment"}:
                        data = result["data"]
                        previous = goal.get("order_observation")
                        goal["order_observation"] = data
                        goal["slots"]["order_id"] = {
                            "value": data["id"],
                            "source_type": "tool",
                            "source_ref": call.call_id,
                            "verification_state": "verified",
                        }
                        made_progress = previous != data
                        if (
                            goal["intent"] == "RETURN_REQUEST"
                            and "item_id" not in goal["slots"]
                            and len(data["items"]) == 1
                        ):
                            goal["slots"]["item_id"] = {
                                "value": data["items"][0]["id"],
                                "source_type": "tool",
                                "source_ref": call.call_id,
                                "verification_state": "verified",
                            }
                        if goal["mode"] == "INQUIRE" and goal["intent"] in {
                            "ORDER_QUERY",
                            "SHIPMENT_QUERY",
                        }:
                            goal["status"] = "COMPLETED"
                    if call.name == "search_policy":
                        evidence = result["data"]
                        known = {e["id"] for e in state["evidence"]}
                        new = [e for e in evidence if e["id"] not in known]
                        state["evidence"].extend(new)
                        made_progress = bool(new)
                        # Only evidence observed for THIS goal may discharge its obligation.
                        goal_evidence = goal.setdefault("policy_evidence", {})
                        for e in evidence:
                            goal_evidence[e["id"]] = e
                        assessment = assess(goal, list(goal_evidence.values()))
                        goal["policy_assessment"] = assessment
                        goal["evidence_ids"] = assessment["evidence_ids"]
                        if goal["mode"] == "INQUIRE" and policy_goal_complete(goal):
                            goal["status"] = "COMPLETED"
                        elif (
                            assessment["unsupported_topic"]
                            or not assessment["required_sections"]
                            or (
                                len(goal.get("policy_requests", [])) >= 3
                                and not assessment["supported"]
                            )
                        ):
                            goal["status"] = "WAITING_INPUT"
                            state["question"] = (
                                "现有政策证据不足以回答该问题，请明确咨询范围；尚未转人工。"
                            )
                            state["pending"] = None
                            await self.store.pause(lease, state, "WAITING_USER")
                            return "PAUSE"
                elif result["error_code"] == "NOT_FOUND":
                    # A missing/unauthorized target is not an automatic successful resolution.
                    state["question"] = "未找到可访问的订单，请核对当前账号和订单编号。"
                    state["pending"] = None
                    await self.store.pause(lease, state, "WAITING_USER")
                    return "PAUSE"
                if result["status"] == "ok":
                    self._settle_rule_denial(goal)
                monitor = ProgressMonitor(**state.get("progress", {}))
                can_continue = monitor.observe(
                    call.name,
                    call.arguments,
                    goal["revision"],
                    digest(result),
                    made_progress=made_progress,
                )
                state["progress"] = asdict(monitor)
                if not can_continue:
                    raise DomainError("NO_PROGRESS", "Repeated tool calls produced no progress")
        elif isinstance(decision, AskUser):
            question_goals = [self.goal(state, gid) for gid in decision.goal_ids]
            if any(goal["status"] in DISPOSED | {"WAITING_APPROVAL"} for goal in question_goals):
                raise DomainError("GOAL_CLOSED", "Cannot rewrite a disposed or approval goal")
            for goal in question_goals:
                goal["status"] = "WAITING_INPUT"
            state["question"], state["pending"] = decision.question, None
            await self.store.pause(lease, state, "WAITING_USER")
            return "PAUSE"
        elif isinstance(decision, ProposeAction):
            goal = self.goal(state, decision.goal_id)
            if goal["status"] in DISPOSED | {"WAITING_APPROVAL"}:
                raise DomainError("GOAL_CLOSED", "Proposal already pending or goal disposed")
            required_intent = {"CANCEL_ORDER": "CANCEL_ORDER", "REQUEST_RETURN": "RETURN_REQUEST"}
            if (
                goal["mode"] != "REQUEST_ACTION"
                or goal["intent"] != required_intent[decision.action]
            ):
                raise DomainError(
                    "INTENT_MISMATCH", "Inquiry does not authorize an action proposal"
                )
            slots = goal["slots"]
            if (
                not goal.get("order_observation")
                or slots.get("order_id", {}).get("verification_state") != "verified"
            ):
                raise DomainError("UNVERIFIED_TARGET", "Read the authorized order first")
            assessment = goal.get("policy_assessment", {})
            if not assessment.get("supported") or len(assessment.get("versions", [])) != 1:
                raise DomainError(
                    "POLICY_EVIDENCE_REQUIRED", "Retrieve applicable policy before proposing"
                )
            request = PrepareAction(**decision.model_dump(exclude={"kind", "goal_id"}))
            for key, value in request.model_dump(exclude={"action"}).items():
                if value is not None and slots.get(key, {}).get("value") != value:
                    raise DomainError(
                        "SLOT_MISMATCH", "Proposed arguments were not resolved from user input"
                    )
            try:
                proposal = await self.tools.service.prepare(
                    lease.owner_id,
                    request,
                    f"{lease.run_id}/{step}/{decision.goal_id}",
                    lease=lease,
                    expected_policy_version=assessment["versions"][0],
                )
                goal["proposal_id"], goal["status"] = proposal["id"], "WAITING_APPROVAL"
                state["question"] = "请检查操作对象、金额和影响，使用确认卡批准或拒绝。"
            except DomainError as exc:
                if exc.code in {
                    "NOT_CANCELLABLE",
                    "NOT_RETURNABLE",
                    "RETURN_WINDOW_CLOSED",
                    "CATEGORY_EXCLUDED",
                    "QUANTITY_EXCEEDED",
                    "AMOUNT_EXCEEDED",
                }:
                    goal["status"], goal["decline_reason"] = "DECLINED", exc.code
                else:
                    raise
        elif isinstance(decision, FinalAnswer):
            goals = [
                GoalSnapshot(
                    g["id"],
                    Intent(g["intent"]),
                    g["mode"],
                    g["status"],
                    tuple(g["operation_ids"]),
                    g["mode"] == "INQUIRE"
                    and g["intent"] in {"POLICY_QUERY", "RETURN_REQUEST", "CANCEL_ORDER"},
                    tuple(g.get("evidence_ids", [])),
                )
                for g in state["goals"]
            ]
            report = check_completion(
                decision,
                goals,
                verified_operations={r["operation_id"] for r in state["results"]},
                accessible_evidence={e["id"] for e in state["evidence"]},
            )
            if not report.allowed:
                raise DomainError("PREMATURE_FINAL", ", ".join(report.errors))
            state["answer"], state["pending"] = self.render(state), None
            await self.store.finish(lease, state, "SUCCEEDED", report.outcome, "GOALS_DISPOSED")
            return "FINISH"
        return None

    @staticmethod
    def _settle_rule_denial(goal):
        """Close only a rule-proven denial with matching order and policy evidence."""
        if goal["mode"] != "REQUEST_ACTION" or goal["status"] in DISPOSED:
            return
        order = goal.get("order_observation", {})
        eligibility = goal.get("eligibility_observation", {})
        assessment = goal.get("policy_assessment", {})
        if not (
            order
            and eligibility
            and not assessment.get("unsupported_topic")
            and eligibility.get("order_id") == order["id"]
            and eligibility.get("order_version") == order["version"]
            and assessment.get("versions") == [eligibility.get("policy_version")]
        ):
            return
        action = {"CANCEL_ORDER": "CANCEL_ORDER", "RETURN_REQUEST": "REQUEST_RETURN"}.get(
            goal["intent"]
        )
        item_id = goal.get("slots", {}).get("item_id", {}).get("value")
        for option in eligibility.get("actions", []):
            if option["action"] != action or (
                action == "REQUEST_RETURN" and option.get("item_id") != item_id
            ):
                continue
            if not option["allowed"] and option.get("reason") in {
                "NOT_CANCELLABLE",
                "NOT_RETURNABLE",
                "RETURN_WINDOW_CLOSED",
                "CATEGORY_EXCLUDED",
            }:
                section = {
                    "NOT_CANCELLABLE": "cancel-pending",
                    "NOT_RETURNABLE": "return-undelivered",
                    "RETURN_WINDOW_CLOSED": "return-window",
                    "CATEGORY_EXCLUDED": "return-excluded",
                }[option["reason"]]
                decisive = [
                    e["id"]
                    for e in goal.get("policy_evidence", {}).values()
                    if section in e.get("policy_sections", [])
                    and e.get("version") == eligibility["policy_version"]
                    and e.get("content_hash") == digest(e.get("quote", ""))
                ]
                if not decisive:
                    return
                # One rule-proven necessary-condition failure is sufficient for denial.
                # Positive proposals still require the complete policy assessment.
                goal["evidence_ids"] = decisive
                goal["status"], goal["decline_reason"] = "DECLINED", option["reason"]
                return

    async def _read(self, lease, state, step, call, persistence_lock):
        # Tool IO stays parallel; journal writes within one batch are serialized.
        # Cross-worker authority remains the database lease/fence and row locks.
        async with persistence_lock:
            identifier, result = await self.store.invocation(
                lease, step, call.call_id, call.name, call.arguments
            )
        if result is None:
            result = await self.tools.execute(lease.owner_id, call.name, call.arguments)
            async with persistence_lock:
                await self.store.save_tool(lease, identifier, result)
        return result

    async def _settle_approvals(self, lease, state):
        changed = False
        for goal in state["goals"]:
            if goal["status"] != "WAITING_APPROVAL":
                continue
            proposal = await self.tools.service.get_proposal(lease.owner_id, goal["proposal_id"])
            if proposal["status"] in {"APPROVED", "EXECUTED"}:
                result = await self.tools.service.execute(
                    lease.owner_id, proposal["id"], lease=lease
                )
                await self.fault("after_tool_commit", lease)
                if result["status"] == "COMPLETED":
                    if result["operation_id"] not in {r["operation_id"] for r in state["results"]}:
                        state["results"].append(result)
                    goal["operation_ids"], goal["status"] = [result["operation_id"]], "COMPLETED"
                else:
                    goal["status"], goal["decline_reason"] = "DECLINED", "STALE_PROPOSAL"
                changed = True
            elif proposal["status"] in {"REJECTED", "WITHDRAWN"}:
                goal["status"] = "WITHDRAWN"
                changed = True
            elif proposal["status"] in {"STALE", "EXPIRED"}:
                goal["status"], goal["decline_reason"] = "DECLINED", proposal["status"]
                changed = True
        if changed:
            await self.store.save(
                lease, state, kind="operations.verified", payload={"results": state["results"]}
            )
        return state

    @staticmethod
    def render(state, fallback=None):
        # Never present an unverified model claim of a refund as a business fact.
        lines = []
        for goal in state["goals"]:
            label = f"{goal['id']} [{goal['intent']}]"
            if goal["status"] == "COMPLETED" and goal.get("operation_ids"):
                result = next(
                    r for r in state["results"] if r["operation_id"] in goal["operation_ids"]
                )
                description = (
                    "已创建退货申请，尚未发放退款"
                    if result["kind"] == "RETURN_REQUESTED"
                    else "已取消订单并登记模拟退款（不涉及真实资金）"
                )
                lines.append(f"{label}: {description}；金额 {result['amount_cents']} 分。")
            elif (
                goal["status"] == "COMPLETED"
                and goal.get("order_observation")
                and goal["intent"] in {"ORDER_QUERY", "SHIPMENT_QUERY"}
            ):
                order = goal["order_observation"]
                lines.append(
                    f"{label}: {order['id']}，状态 {order['status']}，"
                    f"实付 {order['paid_cents']} 分。"
                )
            elif goal["status"] == "COMPLETED" and goal.get("evidence_ids"):
                quotes = [e["quote"] for e in state["evidence"] if e["id"] in goal["evidence_ids"]]
                if goal.get("eligibility_observation"):
                    checks = goal["eligibility_observation"]
                    target_action = (
                        "CANCEL_ORDER" if goal["intent"] == "CANCEL_ORDER" else "REQUEST_RETURN"
                    )
                    facts = [
                        f"{a['item_id'] or '订单'}："
                        + ("符合当前规则" if a["allowed"] else f"不符合（{a['reason']}）")
                        for a in checks["actions"]
                        if a["action"] == target_action
                    ]
                    lines.append(
                        f"{label}: {checks['order_id']} 当前只读资格核验："
                        + "；".join(facts)
                        + "；未创建提案、未办理业务。"
                    )
                lines.append(f"{label}: " + "\n".join(quotes))
            else:
                lines.append(
                    f"{label}: {goal['status']} {goal.get('decline_reason', '')} "
                    f"{goal.get('capability_notice', '')}".strip()
                )
        if fallback:
            lines.append(fallback)
        return "\n".join(lines)
