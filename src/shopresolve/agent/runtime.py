"""Single-agent loop over persistent state; no framework-owned hidden execution."""

import asyncio
import copy
import json
import time
from dataclasses import asdict

import httpx
from pydantic import ValidationError

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
                if active_ms >= config["active_budget_ms"]:
                    raise DomainError("ACTIVE_BUDGET", "Active execution budget exhausted")
                pending = state.get("pending")
                if pending is None:
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
                except (ValidationError, DomainError) as exc:
                    if isinstance(exc, DomainError) and exc.code in {
                        "LEASE_LOST",
                        "NO_PROGRESS",
                        "MODEL_BUDGET",
                        "ACTIVE_BUDGET",
                    }:
                        raise
                    state["pending"] = None
                    state["last_error"] = (
                        exc.code if isinstance(exc, DomainError) else "INVALID_MODEL_OUTPUT"
                    )
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
            if item.existing_goal_id:
                goal = by_id[item.existing_goal_id]
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
            goal.update(
                intent=item.intent.value,
                mode=item.mode,
                mentions=item.target_mentions,
                status="UNRESOLVED",
            )
            for slot in item.slot_candidates:
                if slot.name not in {"order_id", "item_id", "quantity", "reason"}:
                    raise DomainError("INVALID_SLOT", "Unsupported slot")
                if (
                    slot.name in {"order_id", "item_id"}
                    and str(slot.value) not in message_map[slot.source_message_id]
                ):
                    raise DomainError("INVALID_SOURCE", "Entity ID was not supplied by the user")
                old = goal["slots"].get(slot.name)
                if slot.name == "order_id" and old and old["value"] != slot.value:
                    goal.pop("order_observation", None)
                    goal["slots"] = {}
                goal["slots"][slot.name] = {
                    "value": slot.value,
                    "source_type": "user",
                    "source_ref": slot.source_message_id,
                    "verification_state": "asserted",
                }
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
                expected_order = goal.get("slots", {}).get("order_id", {}).get("value")
                if call.name in {"get_order", "get_shipment"} and expected_order is not None:
                    if call.arguments.get("order_id") != expected_order:
                        raise DomainError(
                            "TARGET_MISMATCH", "Read target differs from the user's order"
                        )
            results = await asyncio.gather(
                *[self._read(lease, state, step, call) for call in decision.calls]
            )
            # Results are applied in original model order, regardless of completion order.
            for call, result in zip(decision.calls, results, strict=True):
                goal = self.goal(state, call.arguments["goal_id"])
                state["observations"].append(
                    {"call_id": call.call_id, "name": call.name, "result": result}
                )
                made_progress = False
                if result["status"] == "ok":
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
                        goal["evidence_ids"] = [e["id"] for e in evidence]
                        made_progress = bool(new)
                        if evidence and goal["mode"] == "INQUIRE":
                            goal["status"] = "COMPLETED"
                elif result["error_code"] == "NOT_FOUND":
                    # A missing/unauthorized target is not an automatic successful resolution.
                    state["question"] = "未找到可访问的订单，请核对当前账号和订单编号。"
                    state["pending"] = None
                    await self.store.pause(lease, state, "WAITING_USER")
                    return "PAUSE"
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
            for gid in decision.goal_ids:
                self.goal(state, gid)["status"] = "WAITING_INPUT"
            state["question"], state["pending"] = decision.question, None
            await self.store.pause(lease, state, "WAITING_USER")
            return "PAUSE"
        elif isinstance(decision, ProposeAction):
            goal = self.goal(state, decision.goal_id)
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

    async def _read(self, lease, state, step, call):
        identifier, result = await self.store.invocation(
            lease, step, call.call_id, call.name, call.arguments
        )
        if result is None:
            result = await self.tools.execute(lease.owner_id, call.name, call.arguments)
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
            elif goal["status"] == "COMPLETED" and goal.get("order_observation"):
                order = goal["order_observation"]
                lines.append(
                    f"{label}: {order['id']}，状态 {order['status']}，"
                    f"实付 {order['paid_cents']} 分。"
                )
            elif goal["status"] == "COMPLETED" and goal.get("evidence_ids"):
                quotes = [e["quote"] for e in state["evidence"] if e["id"] in goal["evidence_ids"]]
                lines.append(f"{label}: " + "\n".join(quotes))
            else:
                lines.append(f"{label}: {goal['status']} {goal.get('decline_reason', '')}".strip())
        if fallback:
            lines.append(fallback)
        return "\n".join(lines)
