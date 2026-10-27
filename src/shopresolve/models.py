from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("paid_cents >= 0 AND refunded_cents >= 0"),
        CheckConstraint("refunded_cents <= paid_cents"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(24))
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    paid_cents: Mapped[int] = mapped_column(Integer)
    refunded_cents: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    delivered_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0 AND unit_paid_cents >= 0"),
        CheckConstraint("reserved_return_quantity >= 0 AND reserved_return_quantity <= quantity"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(32))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_paid_cents: Mapped[int] = mapped_column(Integer)
    reserved_return_quantity: Mapped[int] = mapped_column(Integer, default=0)


class Policy(Base):
    __tablename__ = "policies"
    version: Mapped[str] = mapped_column(String(36), primary_key=True)
    active: Mapped[bool]
    return_days: Mapped[int] = mapped_column(Integer)
    excluded_categories: Mapped[list[str]] = mapped_column(JSON)
    document: Mapped[str] = mapped_column(String(2000))
    content_hash: Mapped[str] = mapped_column(String(64))


class Proposal(Base):
    __tablename__ = "proposals"
    __table_args__ = (UniqueConstraint("operation_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True, index=True)
    operation_id: Mapped[str] = mapped_column(String(36))
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    item_id: Mapped[str | None] = mapped_column(ForeignKey("order_items.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(24))
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    order_version: Mapped[int] = mapped_column(Integer)
    policy_version: Mapped[str] = mapped_column(ForeignKey("policies.version"))
    args_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="PENDING")
    version: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[int] = mapped_column(BigInteger)


class Approval(Base):
    __tablename__ = "approvals"
    proposal_id: Mapped[str] = mapped_column(ForeignKey("proposals.id"), primary_key=True)
    decided_by: Mapped[str] = mapped_column(String(36))
    decision: Mapped[str] = mapped_column(String(12))
    args_hash: Mapped[str] = mapped_column(String(64))
    decided_at: Mapped[int] = mapped_column(BigInteger)


class Operation(Base):
    __tablename__ = "operations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("proposals.id"), unique=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
    committed_at: Mapped[int] = mapped_column(BigInteger)


class Refund(Base):
    __tablename__ = "refunds"
    operation_id: Mapped[str] = mapped_column(ForeignKey("operations.id"), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))


class ReturnRequest(Base):
    __tablename__ = "return_requests"
    operation_id: Mapped[str] = mapped_column(ForeignKey("operations.id"), primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("order_items.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="REQUESTED")


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    owner_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    route: Mapped[str] = mapped_column(String(120), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    body_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class SessionToken(Base):
    __tablename__ = "session_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36))
    expires_at: Mapped[int] = mapped_column(BigInteger)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    active_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("conversation_id", "client_message_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    client_message_id: Mapped[str] = mapped_column(String(100))
    body: Mapped[str] = mapped_column(String(8000))
    created_at: Mapped[int] = mapped_column(BigInteger)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    outcome: Mapped[str | None] = mapped_column(String(24), nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    fencing_token: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_until: Mapped[int] = mapped_column(BigInteger, default=0, index=True)
    resume_generation: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[dict[str, Any]] = mapped_column(JSON)
    state_hash: Mapped[str] = mapped_column(String(64))
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    active_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    next_step: Mapped[int] = mapped_column(Integer, default=0)
    next_event_seq: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[int] = mapped_column(BigInteger)
    expires_at: Mapped[int] = mapped_column(BigInteger)
    config_hash: Mapped[str] = mapped_column(String(64))
    runtime_version: Mapped[str] = mapped_column(String(32), default="runtime-v1")


class RunEvent(Base):
    __tablename__ = "run_events"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[int] = mapped_column(BigInteger)


class Checkpoint(Base):
    __tablename__ = "checkpoints"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[dict[str, Any]] = mapped_column(JSON)
    state_hash: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[int] = mapped_column(Integer, default=1)


class ModelStep(Base):
    __tablename__ = "model_steps"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    phase: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24))
    input_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    elapsed_ms: Mapped[int] = mapped_column(BigInteger, default=0)


class ToolInvocation(Base):
    __tablename__ = "tool_invocations"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    step_seq: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(80))
    args_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    generation: Mapped[int] = mapped_column(Integer)
    published_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    __table_args__ = (UniqueConstraint("run_id", "generation"),)


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(160))
    storage_key: Mapped[str] = mapped_column(String(64))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)


class Analysis(Base):
    __tablename__ = "analyses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"))
