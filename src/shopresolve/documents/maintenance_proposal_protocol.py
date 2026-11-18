"""Proposal-first repository decisions. Permissions are checked in the runtime."""

from typing import Literal

from pydantic import Field, TypeAdapter

from .maintenance_ledger_protocol import (
    INSTRUCTIONS,
    DecisionFields,
    Draft,
    Question,
    Surface,
    build_maintenance_json_payload,
    normalize_decision,
)

PROPOSAL_POLICY = "proposal-react-v1"
PROPOSAL_PREFIX = "MAINT_P_"
VERIFY_RESERVE = 2


class ProposalSurface(Surface):
    # Model output tokens already bound free text. Delegate the audit excerpt to the runtime.
    reason: str = Field(min_length=1)


class Reason(DecisionFields):
    kind: Literal["research", "propose", "no_change", "unresolved"]
    questions: list[Question] = Field(default_factory=list)
    surfaces: list[ProposalSurface] = Field(default_factory=list, max_length=8)
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str = ""


class Necessity(DecisionFields):
    kind: Literal["keep_patch", "no_change", "research", "unresolved"]
    reason: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    counterevidence_ids: list[str] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)


class Defect(DecisionFields):
    path: str = Field(min_length=1, max_length=512)
    problem: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    question: str = ""


class Quality(DecisionFields):
    kind: Literal["pass", "revise", "research", "counterevidence", "unresolved"]
    reason: str = ""
    defects: list[Defect] = Field(default_factory=list, max_length=12)
    questions: list[Question] = Field(default_factory=list)
    counterevidence_ids: list[str] = Field(default_factory=list)


PROTOCOLS = {
    "REASON": TypeAdapter(Reason),
    "DRAFT": TypeAdapter(Draft),
    "NECESSITY": TypeAdapter(Necessity),
    "QUALITY": TypeAdapter(Quality),
}
PROMPTS = {
    "REASON": (
        "Find material implementation changes and plausible affected documentation. "
        "Propose a draft when a path has observed implementation and related documentation "
        "evidence. An existing target can be read by runtime. A new file needs adjacent "
        "documentation or a navigation/convention source. Exact proof of an outdated claim "
        "comes during review. Propose only new or changed paths. A no_change proposal is "
        "reviewed independently; explain why no user-facing gap exists and cite both sides "
        "where available. Stop researching once a grounded proposal can be drafted."
    ),
    "DRAFT": INSTRUCTIONS["DRAFT"]
    + " For a revision, correct each concrete defect using its cited observations.",
    "NECESSITY": (
        "Given the task, observed implementation, and current docs, independently determine "
        "whether any documentation change is needed. Treat the candidate as an untrusted "
        "hypothesis. Ignore its polish and earlier target rationale. Use keep_patch when a "
        "documented gap needs editing, even if the candidate was no_change; cite observations. "
        "Use no_change only when cited implementation and docs explain why no user-facing gap "
        "remains. For a proposed new file, cite the observed related docs that make it redundant. "
        "To reject an existing patch, state why its claimed gap does not exist and give "
        "counterevidence_ids that include implementation and docs. Include a repository source "
        "that was not cited to draft the patch. Research uncertainty. Do not review wording."
    ),
    "QUALITY": (
        "Challenge factuality, completeness and placement. Use pass only when all are supported. "
        "For revise, list concrete defects with the affected approved path, precise missing or "
        "wrong claim in problem, and observed evidence IDs. If evidence is missing, provide "
        "a question for that defect or use research. Use research to investigate a new path "
        "rather than assigning it directly to the editor. Use counterevidence with reason "
        "and counterevidence_ids for observed material that disproves the need to change docs. "
        "Uncertainty about the patch is not counterevidence."
    ),
}

ALIASES = {
    "REASON": {"edit": "propose", "ready_to_edit": "propose"},
    "NECESSITY": {"necessary": "keep_patch", "keep": "keep_patch", "overturn": "no_change"},
}
FIELDS = {
    "research": {"questions", "reason"},
    "propose": {"surfaces"},
    "no_change": {"reason", "evidence_ids", "counterevidence_ids", "questions"},
    "counterevidence": {"reason", "counterevidence_ids"},
    "keep_patch": {"reason", "evidence_ids"},
    "pass": {"reason"},
    "revise": {"defects", "reason"},
    "unresolved": {"reason"},
}


def parse_decision(phase, response):
    if not isinstance(response, dict):
        return PROTOCOLS[phase].validate_python(response), False
    data, _ = normalize_decision(response)

    def canonical(kind):
        if not isinstance(kind, str):
            raise ValueError("Decision action must be a string")
        name = kind.strip().lower().replace("-", "_")
        if name == "investigate":
            return "research"
        return ALIASES.get(phase, {}).get(name, name)

    kinds = {canonical(data[k]) for k in ("kind", "action") if k in data}
    if len(kinds) > 1:
        raise ValueError("Conflicting action and kind")
    if phase == "DRAFT":
        if kinds and not kinds <= {"propose", "edit", "draft", "revise"}:
            raise ValueError("Draft must contain edit operations")
        data = {k: v for k, v in data.items() if k == "edits"}
    elif kinds:
        kind = next(iter(kinds))
        data = {"kind": kind, **{k: v for k, v in data.items() if k in FIELDS.get(kind, ())}}
        reason = data.get("reason")
        if isinstance(reason, dict):
            reason = reason.get("text")
        if isinstance(reason, str):
            data["reason"] = reason.strip()
        elif kind not in {"no_change", "unresolved"}:
            data.pop("reason", None)
    action = PROTOCOLS[phase].validate_python(data)
    return action, data != response


def build_proposal_payload(settings, phase, context):
    role = phase.removeprefix(PROPOSAL_PREFIX)
    return build_maintenance_json_payload(settings, role, context, PROTOCOLS, PROMPTS)
