"""M1 deterministic commerce layer. Agent/lease integration comes in M3.

This service owns its transactions. No model or network calls occur inside them.
"""

import asyncio
import hashlib
import json
import random
import time
from collections.abc import Callable
from functools import wraps
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError

from shopresolve.errors import DomainError
from shopresolve.models import (
    Approval,
    IdempotencyRecord,
    Operation,
    Order,
    OrderItem,
    Policy,
    Proposal,
    Refund,
    ReturnRequest,
)
from shopresolve.schemas import Decision, PrepareAction


def retry_local_transaction(method):
    """Retry ONLY rolled-back local DB lock failures, with the same logical request.

    Do not use this decorator for remote writes or connection/commit-unknown errors.
    The method's transaction context must fully exit (and rollback) before retry.
    """

    @wraps(method)
    async def wrapped(*args, **kwargs):
        for attempt in range(3):
            try:
                return await method(*args, **kwargs)
            except OperationalError as exc:
                code = exc.orig.args[0] if exc.orig.args else None
                if code not in {1205, 1213} or attempt == 2:
                    raise
                await asyncio.sleep(0.02 * (2**attempt) + random.uniform(0, 0.02))
        raise AssertionError("unreachable")

    return wrapped


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def action_payload(p: Proposal) -> dict:
    return {
        k: getattr(p, k)
        for k in (
            "operation_id",
            "owner_id",
            "order_id",
            "item_id",
            "action",
            "quantity",
            "reason",
            "amount_cents",
            "currency",
            "order_version",
            "policy_version",
            "expires_at",
        )
    }


def proposal_view(p: Proposal) -> dict:
    return {
        "id": p.id,
        **action_payload(p),
        "status": p.status,
        "version": p.version,
        "args_hash": p.args_hash,
    }


class CommerceService:
    def __init__(self, sessions, clock: Callable[[], float] = time.time):
        self.sessions = sessions
        self.clock = clock

    async def _idem(self, session, owner, route, key, body):
        # Hash the opaque key so MySQL's default case-insensitive collation cannot
        # merge distinct client keys such as 'Request-A' and 'request-a'.
        key = hashlib.sha256(key.encode()).hexdigest()
        values = dict(owner_id=owner, route=route, key=key, body_hash=digest(body), response=None)
        # Insert-or-no-op locks the same unique key even when no record existed yet.
        # SELECT FOR UPDATE alone would not serialize missing-row requests reliably.
        if session.bind.dialect.name == "mysql":
            statement = mysql_insert(IdempotencyRecord).values(**values)
            statement = statement.on_duplicate_key_update(key=statement.inserted.key)
        else:
            statement = sqlite_insert(IdempotencyRecord).values(**values).on_conflict_do_nothing()
        await session.execute(statement)
        record = await session.scalar(
            select(IdempotencyRecord)
            .where(
                IdempotencyRecord.owner_id == owner,
                IdempotencyRecord.route == route,
                IdempotencyRecord.key == key,
            )
            .with_for_update()
        )
        if record.body_hash != values["body_hash"]:
            raise DomainError(
                "IDEMPOTENCY_CONFLICT", "Key was already used for a different request"
            )
        return record

    async def _order(self, session, owner, order_id, lock=False):
        query = select(Order).where(Order.id == order_id, Order.owner_id == owner)
        order = await session.scalar(query.with_for_update() if lock else query)
        if order is None:
            raise DomainError("NOT_FOUND", "Resource not found", 404)
        return order

    async def get_order(self, owner, order_id):
        async with self.sessions() as session:
            order = await self._order(session, owner, order_id)
            items = (
                await session.scalars(
                    select(OrderItem).where(OrderItem.order_id == order_id).order_by(OrderItem.id)
                )
            ).all()
            return {
                "id": order.id,
                "status": order.status,
                "version": order.version,
                "paid_cents": order.paid_cents,
                "refunded_cents": order.refunded_cents,
                "currency": order.currency,
                "delivered_at": order.delivered_at,
                "items": [
                    {
                        "id": i.id,
                        "name": i.name,
                        "quantity": i.quantity,
                        "unit_paid_cents": i.unit_paid_cents,
                        "reserved_return_quantity": i.reserved_return_quantity,
                    }
                    for i in items
                ],
            }

    async def list_orders(self, owner):
        async with self.sessions() as session:
            return [
                {"id": o.id, "status": o.status, "paid_cents": o.paid_cents, "currency": o.currency}
                for o in (
                    await session.scalars(
                        select(Order).where(Order.owner_id == owner).order_by(Order.id).limit(100)
                    )
                ).all()
            ]

    async def _eligibility(self, session, order, request, policy):
        if (
            not policy.active
            or digest(
                {
                    "return_days": policy.return_days,
                    "excluded_categories": policy.excluded_categories,
                    "document": policy.document,
                }
            )
            != policy.content_hash
        ):
            raise DomainError("POLICY_UNAVAILABLE", "Policy bundle is inactive or inconsistent")
        if request.action == "CANCEL_ORDER":
            if order.status != "PENDING_SHIPMENT":
                raise DomainError("NOT_CANCELLABLE", "Only unshipped orders can be cancelled")
            return order.paid_cents - order.refunded_cents, None
        if order.status != "DELIVERED" or order.delivered_at is None:
            raise DomainError("NOT_RETURNABLE", "Return requires a delivered order")
        now = int(self.clock())
        if order.delivered_at > now or now - order.delivered_at > policy.return_days * 86400:
            raise DomainError("RETURN_WINDOW_CLOSED", "Order is outside the return window")
        item = await session.scalar(
            select(OrderItem)
            .where(OrderItem.id == request.item_id, OrderItem.order_id == order.id)
            .with_for_update()
        )
        if item is None:
            raise DomainError("NOT_FOUND", "Resource not found", 404)
        if item.category in policy.excluded_categories:
            raise DomainError("CATEGORY_EXCLUDED", "Category needs manual review")
        if request.quantity > item.quantity - item.reserved_return_quantity:
            raise DomainError("QUANTITY_EXCEEDED", "Requested quantity is not available for return")
        amount = item.unit_paid_cents * request.quantity
        if amount > order.paid_cents - order.refunded_cents:
            raise DomainError("AMOUNT_EXCEEDED", "Amount exceeds remaining payment")
        return amount, item

    @retry_local_transaction
    async def prepare(self, owner, request: PrepareAction, key, *, lease=None):
        async with self.sessions.begin() as session:
            if lease is not None:
                from shopresolve.runstore import require_lease

                await require_lease(session, lease, self.clock)
                if lease.owner_id != owner:
                    raise DomainError("NOT_FOUND", "Resource not found", 404)
            idem = await self._idem(session, owner, "prepare", key, request.model_dump())
            if idem.response is not None:
                return idem.response
            order = await self._order(session, owner, request.order_id, lock=True)
            policies = (await session.scalars(select(Policy).where(Policy.active.is_(True)))).all()
            if len(policies) != 1:
                raise DomainError(
                    "POLICY_UNAVAILABLE", "Exactly one active demo policy is required"
                )
            policy = policies[0]
            amount, _ = await self._eligibility(session, order, request, policy)
            proposal = Proposal(
                id=str(uuid4()),
                run_id=lease.run_id if lease else None,
                operation_id=str(uuid4()),
                owner_id=owner,
                **request.model_dump(),
                amount_cents=amount,
                currency=order.currency,
                order_version=order.version,
                policy_version=policy.version,
                expires_at=int(self.clock()) + 900,
                status="PENDING",
                version=1,
                args_hash="",
            )
            proposal.args_hash = digest(action_payload(proposal))
            session.add(proposal)
            idem.response = proposal_view(proposal)
            return idem.response

    async def _proposal(self, session, owner, proposal_id, lock=False):
        query = select(Proposal).where(Proposal.id == proposal_id, Proposal.owner_id == owner)
        proposal = await session.scalar(query.with_for_update() if lock else query)
        if proposal is None:
            raise DomainError("NOT_FOUND", "Resource not found", 404)
        return proposal

    async def get_proposal(self, owner, proposal_id):
        async with self.sessions() as session:
            return proposal_view(await self._proposal(session, owner, proposal_id))

    @retry_local_transaction
    async def decide(self, owner, proposal_id, decision: Decision, key):
        from shopresolve.runstore import (
            append_event,
            checkpoint,
            db_now,
            enqueue,
            lock_owned_run,
        )

        async with self.sessions() as read_session:
            reference = await self._proposal(read_session, owner, proposal_id)
            run_id = reference.run_id
        async with self.sessions.begin() as session:
            run = await lock_owned_run(session, owner, run_id) if run_id else None
            idem = await self._idem(
                session, owner, f"decision/{proposal_id}", key, decision.model_dump()
            )
            if idem.response is not None:
                return idem.response
            if run is not None and run.status not in {"WAITING_APPROVAL", "QUEUED"}:
                raise DomainError("RUN_CONFLICT", "Run is no longer waiting for this approval")
            p = await self._proposal(session, owner, proposal_id, lock=True)
            if p.version != decision.expected_version or p.status != "PENDING":
                raise DomainError("PROPOSAL_CONFLICT", "Proposal already changed; refresh it")
            if p.expires_at <= int(self.clock()):
                p.status = "EXPIRED"
            else:
                p.status = "APPROVED" if decision.decision == "approve" else "REJECTED"
                session.add(
                    Approval(
                        proposal_id=p.id,
                        decided_by=owner,
                        decision=decision.decision,
                        args_hash=p.args_hash,
                        decided_at=int(self.clock()),
                    )
                )
            p.version += 1
            if run is not None:
                run.status = "QUEUED"
                run.resume_generation += 1
                checkpoint(session, run, run.state)
                enqueue(session, run)
                append_event(
                    session,
                    run,
                    "approval.decided",
                    {"proposal_id": p.id, "status": p.status},
                    await db_now(session, self.clock),
                )
            idem.response = proposal_view(p)
            return idem.response

    @retry_local_transaction
    async def execute(self, owner, proposal_id, *, lease=None):
        """Internal service API only. No public execution endpoint in M1.

        M3 must wrap this with run/fence checks before connecting Agent writes.
        """
        async with self.sessions.begin() as session:
            if lease is not None:
                from shopresolve.runstore import require_lease

                await require_lease(session, lease, self.clock)
                if lease.owner_id != owner:
                    raise DomainError("NOT_FOUND", "Resource not found", 404)
            p = await self._proposal(session, owner, proposal_id, lock=True)
            if p.run_id is not None and (lease is None or lease.run_id != p.run_id):
                raise DomainError("LEASE_REQUIRED", "Run-bound actions require their worker lease")
            # Serialize actions on the concrete order BEFORE locking a missing receipt.
            # Otherwise different operation IDs may hold overlapping gap locks and
            # deadlock while another transaction holds/waits for this order row.
            order = await self._order(session, owner, p.order_id, lock=True)
            # A locking read observes a concurrently committed receipt in MySQL RR.
            existing = await session.scalar(
                select(Operation).where(Operation.id == p.operation_id).with_for_update()
            )
            if existing is not None:
                if existing.request_hash != p.args_hash:
                    raise DomainError("INTEGRITY_ERROR", "Operation hash mismatch")
                return existing.result
            if p.status != "APPROVED":
                raise DomainError("APPROVAL_REQUIRED", "This exact action has not been approved")
            approval = await session.get(Approval, p.id)
            if (
                approval is None
                or approval.decision != "approve"
                or approval.decided_by != owner
                or approval.args_hash != p.args_hash
                or digest(action_payload(p)) != p.args_hash
            ):
                raise DomainError("INTEGRITY_ERROR", "Approval does not match the action")
            policy = await session.scalar(
                select(Policy).where(Policy.version == p.policy_version).with_for_update()
            )
            if (
                p.expires_at <= int(self.clock())
                or order.version != p.order_version
                or policy is None
                or not policy.active
            ):
                p.status = "STALE"
                p.version += 1
                return {
                    "status": "STALE",
                    "proposal_id": p.id,
                    "message": "State changed; create and approve a new proposal",
                }
            request = PrepareAction(
                action=p.action,
                order_id=p.order_id,
                item_id=p.item_id,
                quantity=p.quantity,
                reason=p.reason,
            )
            amount, item = await self._eligibility(session, order, request, policy)
            if amount != p.amount_cents:
                raise DomainError("AMOUNT_CHANGED", "Recomputed amount does not match approval")
            if p.action == "CANCEL_ORDER":
                order.status = "CANCELLED"
                order.refunded_cents += amount
                result = {
                    "status": "COMPLETED",
                    "kind": "SIMULATED_CANCELLATION",
                    "order_status": "CANCELLED",
                }
            else:
                item.reserved_return_quantity += p.quantity
                result = {
                    "status": "COMPLETED",
                    "kind": "RETURN_REQUESTED",
                    "return_status": "REQUESTED",
                    "refund_status": "NOT_ISSUED",
                }
            order.version += 1
            result |= {
                "operation_id": p.operation_id,
                "proposal_id": p.id,
                "order_id": order.id,
                "amount_cents": amount,
                "currency": order.currency,
            }
            session.add(
                Operation(
                    id=p.operation_id,
                    proposal_id=p.id,
                    owner_id=owner,
                    request_hash=p.args_hash,
                    result=result,
                    committed_at=int(self.clock()),
                )
            )
            # Flush the parent receipt before dependent ledger records.
            await session.flush()
            if p.action == "CANCEL_ORDER":
                session.add(
                    Refund(
                        operation_id=p.operation_id,
                        order_id=order.id,
                        amount_cents=amount,
                        currency=order.currency,
                    )
                )
            else:
                session.add(
                    ReturnRequest(
                        operation_id=p.operation_id,
                        item_id=p.item_id,
                        quantity=p.quantity,
                        status="REQUESTED",
                    )
                )
            p.status = "EXECUTED"
            p.version += 1
            return result

    async def get_operation(self, owner, operation_id):
        async with self.sessions() as session:
            operation = await session.scalar(
                select(Operation).where(Operation.id == operation_id, Operation.owner_id == owner)
            )
            if operation is None:
                raise DomainError("NOT_FOUND", "Resource not found", 404)
            return operation.result
