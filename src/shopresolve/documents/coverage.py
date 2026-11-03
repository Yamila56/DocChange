"""Auditable maintenance requirements; model judgments are never proofs of truth."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from shopresolve.errors import DomainError


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=1600)
    evidence_ids: list[str] = Field(min_length=1, max_length=12)


class CoverageFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_id: str
    status: Literal["covered", "no_change", "missing"]
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    reason: str = Field(min_length=1, max_length=2000)


def require_plan(requirements, known):
    if not requirements or len({r.id for r in requirements}) != len(requirements):
        raise DomainError("INVALID_PLAN", "Provide unique, source-backed maintenance requirements")
    if any(not set(r.evidence_ids) <= known for r in requirements):
        raise DomainError("INVALID_PLAN", "Requirement cites unknown task/implementation evidence")
    return [r.model_dump() for r in requirements]


def require_edit_bindings(action, plan, known):
    ids = {r["id"] for r in plan}
    for edit in action.edits:
        if not edit.requirement_ids or not set(edit.requirement_ids) <= ids:
            raise DomainError("UNBOUND_EDIT", "Every edit must name existing requirement_ids")
        if not edit.evidence_ids or not set(edit.evidence_ids) <= known:
            raise DomainError("UNBOUND_EDIT", "Every edit must cite observed evidence_ids")
    if not action.edits:
        raise DomainError("EMPTY_PATCH", "Provide edits or explicitly propose abstention")


def evaluate_review(action, plan, candidate, known):
    """Check referential integrity; semantic assessment remains an LLM judgment."""
    ids = {r["id"] for r in plan}
    findings = action.coverage
    if {r.requirement_id for r in findings} != ids or len(findings) != len(ids):
        raise DomainError("INCOMPLETE_REVIEW", "Review every requirement exactly once")
    additions = require_plan(action.requirements, known) if action.requirements else []
    if len(plan) + len(additions) > 24:
        raise DomainError("INVALID_PLAN", "Consolidate overlapping requirements within 24 items")
    if ids & {r["id"] for r in additions}:
        raise DomainError("INVALID_PLAN", "Newly discovered requirements need new IDs")
    bound = {rid for edit in candidate.get("edits", []) for rid in edit.get("requirement_ids", [])}
    for finding in findings:
        if not set(finding.evidence_ids) <= known:
            raise DomainError("INVALID_REVIEW", "Review cites unknown evidence")
        if finding.status != "missing" and not finding.evidence_ids:
            raise DomainError("INVALID_REVIEW", "Resolved requirements need supporting evidence")
        if finding.status == "covered" and (
            candidate["decision"] != "patch" or finding.requirement_id not in bound
        ):
            raise DomainError("INVALID_REVIEW", "Covered requirement has no associated edit")
    ready = (
        action.ready
        and not additions
        and not action.unsupported_claims
        and all(r.status != "missing" for r in findings)
        and (candidate["decision"] != "abstain" or all(r.status == "no_change" for r in findings))
    )
    return {
        "ready": ready,
        "coverage": [r.model_dump() for r in findings],
        "new_requirements": additions,
        "unsupported_claims": action.unsupported_claims,
        "reason": action.text,
        "assessment": "Model review with deterministic reference checks; not human approval",
    }
