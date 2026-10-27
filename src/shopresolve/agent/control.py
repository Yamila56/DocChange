"""Deterministic guards. Model output is a proposal, never authority."""

from dataclasses import dataclass, field

from shopresolve.agent.contracts import FinalAnswer, Intent, IntentFrame
from shopresolve.errors import DomainError
from shopresolve.service import digest


@dataclass(frozen=True)
class AuthorizedEntity:
    id: str
    label: str
    source_call_id: str


def resolve_entity(candidates: list[AuthorizedEntity]) -> AuthorizedEntity:
    # Candidates must come from an owner-scoped tool, not from model-generated IDs.
    unique = {candidate.id: candidate for candidate in candidates}
    if len(unique) != 1:
        raise DomainError("CLARIFICATION_REQUIRED", "Choose one authorized order or item")
    return next(iter(unique.values()))


def validate_intent_sources(frame: IntentFrame, message_ids: set[str], goal_ids: set[str]):
    if not set(frame.source_message_ids) <= message_ids:
        raise DomainError("INVALID_SOURCE", "Intent refers to unavailable messages")
    frame_sources = set(frame.source_message_ids)
    for item in frame.items:
        if item.existing_goal_id is not None and item.existing_goal_id not in goal_ids:
            raise DomainError("INVALID_GOAL", "Intent refers to an unavailable goal")
        if any(slot.source_message_id not in frame_sources for slot in item.slot_candidates):
            raise DomainError("INVALID_SOURCE", "Slot source is not part of this intent frame")


@dataclass(frozen=True)
class GoalSnapshot:
    goal_id: str
    intent: Intent
    mode: str
    status: str
    operation_ids: tuple[str, ...] = ()
    evidence_required: bool = False


@dataclass(frozen=True)
class CompletionReport:
    allowed: bool
    outcome: str
    errors: tuple[str, ...]


def check_completion(
    answer: FinalAnswer,
    goals: list[GoalSnapshot],
    *,
    verified_operations: set[str],
    accessible_evidence: set[str],
    pending_tool_calls: int = 0,
    unknown_operations: int = 0,
) -> CompletionReport:
    errors = []
    expected = {goal.goal_id for goal in goals}
    if not expected or set(answer.goal_ids) != expected:
        errors.append("GOAL_COVERAGE_MISMATCH")
    if pending_tool_calls or unknown_operations:
        errors.append("UNRESOLVED_EXECUTION")
    if not set(answer.evidence_ids) <= accessible_evidence:
        errors.append("INVALID_EVIDENCE")
    terminal = {"COMPLETED", "DECLINED", "WITHDRAWN", "ESCALATED", "FAILED"}
    for goal in goals:
        if goal.status not in terminal:
            errors.append(f"GOAL_NOT_FINISHED:{goal.goal_id}")
        if goal.status == "COMPLETED" and goal.mode == "REQUEST_ACTION":
            if not goal.operation_ids or not set(goal.operation_ids) <= verified_operations:
                errors.append(f"MISSING_OPERATION_RECEIPT:{goal.goal_id}")
        if goal.evidence_required and goal.status in {"COMPLETED", "DECLINED"}:
            if not answer.evidence_ids:
                errors.append(f"MISSING_EVIDENCE:{goal.goal_id}")
    statuses = {goal.status for goal in goals}
    if errors:
        outcome = "UNRESOLVED"
    elif statuses == {"COMPLETED"}:
        outcome = "FULFILLED"
    elif "COMPLETED" in statuses:
        outcome = "PARTIAL"
    elif "ESCALATED" in statuses:
        outcome = "ESCALATED"
    elif "FAILED" in statuses:
        outcome = "UNRESOLVED"
    else:
        outcome = "DECLINED"
    return CompletionReport(not errors, outcome, tuple(errors))


@dataclass
class ProgressMonitor:
    """Serializable guard state; runtime persists it with the checkpoint in M3."""

    last_fingerprint: str | None = None
    repeat_count: int = 0
    recovery_used: bool = False
    history: list[dict] = field(default_factory=list)

    def observe(
        self,
        action: str,
        args: dict,
        revision: int,
        observation_version: str,
        *,
        made_progress: bool,
    ) -> bool:
        fingerprint = digest([action, args, revision, observation_version])
        if made_progress:
            self.repeat_count = 0
            self.last_fingerprint = None
        elif fingerprint == self.last_fingerprint:
            self.repeat_count += 1
        else:
            self.last_fingerprint = fingerprint
            self.repeat_count = 1
        self.history.append({"fingerprint": fingerprint, "progress": made_progress})
        self.history = self.history[-12:]
        # False tells the runtime not to execute a third identical no-progress attempt.
        return self.repeat_count < 2

    def allow_recovery(self):
        if self.recovery_used:
            return False
        self.recovery_used = True
        return True
