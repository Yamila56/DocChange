"""Transactional run state, checkpoints, leases, outbox, and durable events."""

import copy
import time
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import select, text, update

from shopresolve.errors import DomainError
from shopresolve.models import (
    Checkpoint,
    Conversation,
    Message,
    ModelStep,
    Operation,
    Outbox,
    Proposal,
    Run,
    RunEvent,
    ToolInvocation,
)
from shopresolve.service import CommerceService, digest, retry_local_transaction

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED"}
WAITING = {"WAITING_USER", "WAITING_APPROVAL"}


@dataclass(frozen=True)
class Lease:
    run_id: str
    owner_id: str
    worker_id: str
    token: int


async def db_now(session, fallback=time.time):
    if session.bind.dialect.name == "mysql":
        return int(await session.scalar(text("SELECT UNIX_TIMESTAMP()")))
    return int(fallback())


async def lock_owned_run(session, owner_id, run_id):
    run = await session.scalar(
        select(Run)
        .where(
            Run.id == run_id,
            Run.owner_id == owner_id,
        )
        .with_for_update()
    )
    if run is None:
        raise DomainError("NOT_FOUND", "Resource not found", 404)
    return run


async def require_lease(session, lease: Lease, clock=time.time):
    run = await lock_owned_run(session, lease.owner_id, lease.run_id)
    now = await db_now(session, clock)
    if (
        run.status != "RUNNING"
        or run.fencing_token != lease.token
        or run.lease_owner != lease.worker_id
        or run.lease_until <= now
    ):
        raise DomainError("LEASE_LOST", "Worker no longer owns this run")
    if run.runtime_version != "runtime-v1" or digest(run.state) != run.state_hash:
        raise DomainError("CHECKPOINT_INVALID", "Runtime/checkpoint integrity check failed")
    saved = await session.get(Checkpoint, (run.id, run.version))
    if (
        saved is None
        or saved.schema_version != 1
        or saved.state_hash != run.state_hash
        or digest(saved.state) != saved.state_hash
    ):
        raise DomainError("CHECKPOINT_INVALID", "Persisted checkpoint is missing or corrupt")
    return run


def append_event(session, run, kind, payload, now):
    session.add(
        RunEvent(run_id=run.id, seq=run.next_event_seq, kind=kind, payload=payload, created_at=now)
    )
    run.next_event_seq += 1


def checkpoint(session, run, state):
    run.version += 1
    run.state = copy.deepcopy(state)
    run.state_hash = digest(run.state)
    session.add(
        Checkpoint(
            run_id=run.id,
            version=run.version,
            state=copy.deepcopy(state),
            state_hash=run.state_hash,
            schema_version=1,
        )
    )


def enqueue(session, run):
    session.add(Outbox(id=str(uuid4()), run_id=run.id, generation=run.resume_generation))


def snapshot(run):
    return {
        "id": run.id,
        "conversation_id": run.conversation_id,
        "status": run.status,
        "outcome": run.outcome,
        "stop_reason": run.stop_reason,
        "version": run.version,
        "resume_generation": run.resume_generation,
        "last_event_seq": run.next_event_seq - 1,
        "model_calls": run.model_calls,
        "active_ms": run.active_ms,
        "goals": run.state.get("goals", []),
        "question": run.state.get("question"),
        "results": run.state.get("results", []),
        "answer": run.state.get("answer"),
        "provider": run.state.get("provider"),
        "evidence": run.state.get("evidence", []),
    }


class RunStore:
    def __init__(self, sessions, clock=time.time):
        self.sessions = sessions
        self.clock = clock
        self.commerce = CommerceService(sessions, clock)

    @retry_local_transaction
    async def conversation(self, owner, key):
        async with self.sessions.begin() as session:
            idem = await self.commerce._idem(session, owner, "conversation", key, {})
            if idem.response is not None:
                return idem.response
            conversation = Conversation(
                id=str(uuid4()), owner_id=owner, created_at=int(self.clock()), active_run_id=None
            )
            session.add(conversation)
            idem.response = {"id": conversation.id}
            return idem.response

    @retry_local_transaction
    async def submit(self, owner, conversation_id, body, key, config):
        async with self.sessions.begin() as session:
            idem = await self.commerce._idem(
                session, owner, f"message/{conversation_id}", key, body
            )
            if idem.response is not None:
                return idem.response
            conversation = await session.scalar(
                select(Conversation)
                .where(
                    Conversation.id == conversation_id,
                    Conversation.owner_id == owner,
                )
                .with_for_update()
            )
            if conversation is None:
                raise DomainError("NOT_FOUND", "Resource not found", 404)
            message = await session.scalar(
                select(Message).where(
                    Message.conversation_id == conversation_id,
                    Message.client_message_id == body["client_message_id"],
                )
            )
            if message is not None:
                raise DomainError(
                    "MESSAGE_CONFLICT", "Message ID already used; replay original key"
                )
            if conversation.active_run_id is not None:
                raise DomainError(
                    "RUN_BUSY", "Resume the waiting run or cancel it before a new task"
                )
            message = Message(
                id=str(uuid4()),
                conversation_id=conversation_id,
                client_message_id=body["client_message_id"],
                body=body["text"],
                created_at=int(self.clock()),
            )
            session.add(message)
            now = await db_now(session, self.clock)
            state = {
                "schema_version": 1,
                "phase": "INTENT",
                "goals": [],
                "message_ids": [message.id],
                "messages": [{"id": message.id, "text": message.body}],
                "observations": [],
                "results": [],
                "evidence": [],
                "pending": None,
                "progress": {},
                "provider": config["provider"],
                "config": config,
            }
            run = Run(
                id=str(uuid4()),
                conversation_id=conversation_id,
                owner_id=owner,
                status="QUEUED",
                version=1,
                fencing_token=0,
                lease_until=0,
                resume_generation=0,
                state=state,
                state_hash=digest(state),
                model_calls=0,
                active_ms=0,
                next_step=0,
                next_event_seq=1,
                created_at=now,
                expires_at=now + 86400,
                config_hash=digest(config),
                runtime_version="runtime-v1",
            )
            session.add(run)
            await session.flush()
            conversation.active_run_id = run.id
            session.add(
                Checkpoint(
                    run_id=run.id,
                    version=1,
                    state=state,
                    state_hash=run.state_hash,
                    schema_version=1,
                )
            )
            append_event(session, run, "run.queued", {"provider": config["provider"]}, now)
            enqueue(session, run)
            idem.response = {"run_id": run.id, "message_id": message.id}
            return idem.response

    async def get(self, owner, run_id):
        async with self.sessions() as session:
            run = await session.scalar(select(Run).where(Run.id == run_id, Run.owner_id == owner))
            if run is None:
                raise DomainError("NOT_FOUND", "Resource not found", 404)
            return snapshot(run)

    async def list_runs(self, owner):
        async with self.sessions() as session:
            runs = (
                await session.scalars(
                    select(Run)
                    .where(Run.owner_id == owner)
                    .order_by(Run.created_at.desc())
                    .limit(100)
                )
            ).all()
            return [snapshot(run) for run in runs]

    async def events(self, owner, run_id, after=0, limit=100):
        await self.get(owner, run_id)
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(RunEvent)
                    .where(
                        RunEvent.run_id == run_id,
                        RunEvent.seq > after,
                    )
                    .order_by(RunEvent.seq)
                    .limit(limit)
                )
            ).all()
            return [
                {"seq": r.seq, "kind": r.kind, "payload": r.payload, "created_at": r.created_at}
                for r in rows
            ]

    @retry_local_transaction
    async def claim(self, run_id, worker_id, generation=None, lease_seconds=30):
        async with self.sessions.begin() as session:
            run = await session.scalar(select(Run).where(Run.id == run_id).with_for_update())
            now = await db_now(session, self.clock)
            if run is None or (generation is not None and run.resume_generation != generation):
                return None
            if run.status != "QUEUED" and not (run.status == "RUNNING" and run.lease_until <= now):
                return None
            if run.expires_at <= now:
                await self._finish(session, run, "EXPIRED", "UNRESOLVED", "WAIT_EXPIRED", now)
                return None
            saved = await session.get(Checkpoint, (run.id, run.version))
            if (
                digest(run.state) != run.state_hash
                or run.runtime_version != "runtime-v1"
                or digest(run.state["config"]) != run.config_hash
                or saved is None
                or saved.schema_version != 1
                or saved.state_hash != run.state_hash
                or digest(saved.state) != saved.state_hash
            ):
                await self._finish(session, run, "FAILED", "UNRESOLVED", "CHECKPOINT_INVALID", now)
                return None
            run.status = "RUNNING"
            run.fencing_token += 1
            run.lease_owner = worker_id
            run.lease_until = now + lease_seconds
            append_event(session, run, "run.claimed", {"token": run.fencing_token}, now)
            return Lease(run.id, run.owner_id, worker_id, run.fencing_token)

    async def load(self, lease):
        async with self.sessions.begin() as session:
            run = await require_lease(session, lease, self.clock)
            return copy.deepcopy(run.state), run.model_calls, run.active_ms

    async def heartbeat(self, lease, seconds=30):
        async with self.sessions.begin() as session:
            run = await require_lease(session, lease, self.clock)
            run.lease_until = await db_now(session, self.clock) + seconds

    async def save(self, lease, state, *, kind="step.observed", payload=None, elapsed_ms=0):
        async with self.sessions.begin() as session:
            run = await require_lease(session, lease, self.clock)
            checkpoint(session, run, state)
            run.active_ms += elapsed_ms
            append_event(session, run, kind, payload or {}, await db_now(session, self.clock))

    async def reserve_model(self, lease, phase, context_hash, model_limit):
        async with self.sessions.begin() as session:
            run = await require_lease(session, lease, self.clock)
            if run.model_calls >= model_limit:
                raise DomainError("MODEL_BUDGET", "Model-call budget exhausted")
            seq = run.next_step
            run.next_step += 1
            run.model_calls += 1
            session.add(
                ModelStep(
                    run_id=run.id, seq=seq, phase=phase, status="RESERVED", input_hash=context_hash
                )
            )
            append_event(
                session,
                run,
                "model.started",
                {"step": seq, "phase": phase},
                await db_now(session, self.clock),
            )
            return seq

    async def save_decision(self, lease, state, seq, response, usage=None, elapsed_ms=0):
        async with self.sessions.begin() as session:
            run = await require_lease(session, lease, self.clock)
            step = await session.get(ModelStep, (run.id, seq))
            step.status, step.response, step.usage = "RESPONSE_SAVED", response, usage
            step.elapsed_ms = elapsed_ms
            run.active_ms += elapsed_ms
            state = copy.deepcopy(state)
            state["pending"] = {"step": seq, "response": response, "phase": step.phase}
            checkpoint(session, run, state)
            append_event(
                session,
                run,
                "model.completed",
                {"step": seq, "usage": usage},
                await db_now(session, self.clock),
            )
            return state

    async def model_failed(self, lease, seq, elapsed_ms, error_type):
        async with self.sessions.begin() as session:
            run = await require_lease(session, lease, self.clock)
            step = await session.get(ModelStep, (run.id, seq))
            step.status, step.elapsed_ms = "UNKNOWN", elapsed_ms
            run.active_ms += elapsed_ms
            append_event(
                session,
                run,
                "model.failed",
                {"step": seq, "error_type": error_type, "usage": "unknown"},
                await db_now(session, self.clock),
            )

    async def invocation(self, lease, step, call_id, name, arguments):
        identifier = f"{lease.run_id}:{step}:{call_id}"
        if len(identifier) > 100:
            identifier = digest(identifier)
        async with self.sessions.begin() as session:
            await require_lease(session, lease, self.clock)
            row = await session.get(ToolInvocation, identifier)
            if row:
                if row.args_hash != digest([name, arguments]):
                    raise DomainError("CALL_CONFLICT", "Tool call identity changed")
                return identifier, row.result
            session.add(
                ToolInvocation(
                    id=identifier,
                    run_id=lease.run_id,
                    step_seq=step,
                    name=name,
                    args_hash=digest([name, arguments]),
                    status="IN_FLIGHT",
                )
            )
            return identifier, None

    async def save_tool(self, lease, identifier, result):
        async with self.sessions.begin() as session:
            run = await require_lease(session, lease, self.clock)
            row = await session.get(ToolInvocation, identifier)
            if row is None or row.run_id != run.id:
                raise DomainError("CALL_CONFLICT", "Unknown invocation")
            row.status = "SUCCEEDED" if result.get("status") == "ok" else "FAILED"
            row.result = result
            append_event(
                session,
                run,
                "tool.completed",
                {"name": row.name, "result": result},
                await db_now(session, self.clock),
            )

    async def pause(self, lease, state, status):
        if status not in WAITING:
            raise ValueError("Not a waiting status")
        async with self.sessions.begin() as session:
            run = await require_lease(session, lease, self.clock)
            checkpoint(session, run, state)
            run.status, run.lease_owner, run.lease_until = status, None, 0
            append_event(
                session,
                run,
                "run.waiting",
                {"status": status, "question": state.get("question")},
                await db_now(session, self.clock),
            )

    async def _finish(self, session, run, status, outcome, reason, now):
        # A business transaction can commit just before cancellation/expiry or a
        # worker failure, while its subsequent loop checkpoint has not committed.
        # Reconcile authoritative receipts under the same run lock, never hide them.
        operations = (
            await session.scalars(
                select(Operation)
                .join(Proposal, Operation.proposal_id == Proposal.id)
                .where(Proposal.run_id == run.id, Operation.owner_id == run.owner_id)
            )
        ).all()
        state = copy.deepcopy(run.state)
        known = {r["operation_id"] for r in state.get("results", [])}
        recovered = []
        for operation in operations:
            if operation.id not in known:
                state["results"].append(copy.deepcopy(operation.result))
                recovered.append(operation.id)
                known.add(operation.id)
            for goal in state.get("goals", []):
                if goal.get("proposal_id") == operation.proposal_id:
                    goal["operation_ids"] = [operation.id]
                    goal["status"] = "COMPLETED"
        if status in {"CANCELLED", "EXPIRED"}:
            for goal in state.get("goals", []):
                if goal["status"] not in {"COMPLETED", "DECLINED", "WITHDRAWN"}:
                    goal["status"] = "WITHDRAWN"
            state["pending"], state["question"] = None, None
            state["answer"] = "任务已停止；已提交的业务操作不会回滚。" + "\n".join(
                f"操作 {r['operation_id']}，金额 {r['amount_cents']} 分（模拟业务）。"
                for r in state.get("results", [])
            )
        elif recovered:
            state["answer"] = (state.get("answer") or "") + "\n已回查到业务提交回执。"
        if state != run.state:
            checkpoint(session, run, state)
        if status != "SUCCEEDED" and state.get("results"):
            outcome = "PARTIAL"
        run.status, run.outcome, run.stop_reason = status, outcome, reason
        run.lease_owner, run.lease_until = None, 0
        await session.execute(
            update(Conversation)
            .where(
                Conversation.id == run.conversation_id,
                Conversation.active_run_id == run.id,
            )
            .values(active_run_id=None)
        )
        append_event(
            session,
            run,
            "run.finished",
            {"status": status, "outcome": outcome, "stop_reason": reason},
            now,
        )

    async def finish(self, lease, state, status, outcome, reason):
        async with self.sessions.begin() as session:
            run = await require_lease(session, lease, self.clock)
            checkpoint(session, run, state)
            await self._finish(
                session, run, status, outcome, reason, await db_now(session, self.clock)
            )

    @retry_local_transaction
    async def cancel(self, owner, run_id, key):
        async with self.sessions.begin() as session:
            run = await lock_owned_run(session, owner, run_id)
            idem = await self.commerce._idem(session, owner, f"cancel/{run_id}", key, {})
            if idem.response is not None:
                return idem.response
            if run.status not in TERMINAL:
                await self._finish(
                    session,
                    run,
                    "CANCELLED",
                    "PARTIAL" if run.state.get("results") else "UNRESOLVED",
                    "USER_CANCELLED",
                    await db_now(session, self.clock),
                )
                proposals = (
                    await session.scalars(
                        select(Proposal)
                        .where(
                            Proposal.run_id == run_id, Proposal.status.in_(["PENDING", "APPROVED"])
                        )
                        .with_for_update()
                    )
                ).all()
                for p in proposals:
                    p.status, p.version = "WITHDRAWN", p.version + 1
            idem.response = snapshot(run)
            return idem.response

    @retry_local_transaction
    async def resume(self, owner, run_id, body, key):
        async with self.sessions.begin() as session:
            run = await lock_owned_run(session, owner, run_id)
            idem = await self.commerce._idem(session, owner, f"resume/{run_id}", key, body)
            if idem.response is not None:
                return idem.response
            if (
                run.status not in WAITING
                or run.version != body["expected_version"]
                or run.resume_generation != body["wait_generation"]
            ):
                raise DomainError("RUN_CONFLICT", "Run changed or is not waiting; refresh")
            message = Message(
                id=str(uuid4()),
                conversation_id=run.conversation_id,
                client_message_id=body["client_message_id"],
                body=body["text"],
                created_at=int(self.clock()),
            )
            if await session.scalar(
                select(Message.id).where(
                    Message.conversation_id == run.conversation_id,
                    Message.client_message_id == body["client_message_id"],
                )
            ):
                raise DomainError("MESSAGE_CONFLICT", "Message ID already used")
            session.add(message)
            state = copy.deepcopy(run.state)
            state["messages"].append({"id": message.id, "text": message.body})
            state["message_ids"].append(message.id)
            state["phase"], state["pending"], state["question"] = "INTENT", None, None
            # A revision invalidates approvals before model interpretation. Historical
            # committed operations remain unchanged and available in state/results.
            proposals = (
                await session.scalars(
                    select(Proposal)
                    .where(Proposal.run_id == run.id, Proposal.status.in_(["PENDING", "APPROVED"]))
                    .with_for_update()
                )
            ).all()
            for p in proposals:
                p.status, p.version = "WITHDRAWN", p.version + 1
            for goal in state["goals"]:
                if goal["status"] not in {"COMPLETED", "DECLINED", "WITHDRAWN"}:
                    goal["status"] = "UNRESOLVED"
                    goal.pop("proposal_id", None)
            run.status = "QUEUED"
            run.resume_generation += 1
            checkpoint(session, run, state)
            enqueue(session, run)
            append_event(session, run, "run.resumed", {}, await db_now(session, self.clock))
            idem.response = snapshot(run)
            return idem.response
