"""Proposal-first repository decisions. Permissions are checked in the runtime."""

from typing import Literal

from pydantic import Field, TypeAdapter

from .maintenance_ledger_protocol import (
    DecisionFields,
    DraftEdit,
    Question,
    Surface,
    build_maintenance_json_payload,
    normalize_decision,
)

PROPOSAL_POLICY = "proposal-react-v1"
PROPOSAL_PREFIX = "MAINT_P_"
VERIFY_RESERVE = 2


class MaterialRequirement(DecisionFields):
    statement: str = Field(min_length=1, max_length=480)
    placement: str = Field(default="", max_length=200)
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)


class ProposalSurface(Surface):
    # Model output tokens already bound free text. Delegate the audit excerpt to the runtime.
    reason: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    doc_evidence_ids: list[str] = Field(default_factory=list)
    change_refs: list[str] = Field(default_factory=list)
    requirements: list[MaterialRequirement] = Field(default_factory=list, max_length=10)


class ChangeObservation(DecisionFields):
    summary: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    task_quote: str = ""


class Reason(DecisionFields):
    kind: Literal["research", "propose", "no_change", "unresolved"]
    questions: list[Question] = Field(default_factory=list)
    surfaces: list[ProposalSurface] = Field(default_factory=list, max_length=8)
    reader_goal: str = Field(default="", max_length=420)
    evidence_ids: list[str] = Field(default_factory=list)
    change_refs: list[str] = Field(default_factory=list)
    changes: list[ChangeObservation] = Field(default_factory=list, max_length=6)
    documentation_request_quote: str = Field(default="", max_length=800)
    reason: str = ""


class Necessity(DecisionFields):
    kind: Literal["keep_patch", "no_change", "research", "unresolved"]
    reason: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    change_refs: list[str] = Field(default_factory=list)
    counterevidence_ids: list[str] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)


class Defect(DecisionFields):
    path: str = Field(min_length=1, max_length=512)
    problem: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    question: str = ""
    requirement_id: str = ""
    expected: str = Field(default="", max_length=480)


class Coverage(DecisionFields):
    requirement_id: str
    quote: str = Field(min_length=1, max_length=800)
    absent: bool = False


class Draft(DecisionFields):
    edits: list[DraftEdit] = Field(min_length=1, max_length=30)
    replace_edit_ids: list[str] = Field(default_factory=list, max_length=30)
    defect_ids: list[str] = Field(default_factory=list, max_length=20)


class Quality(DecisionFields):
    kind: Literal["pass", "revise", "research", "counterevidence", "unresolved"]
    reason: str = ""
    defects: list[Defect] = Field(default_factory=list, max_length=12)
    coverage: list[Coverage] = Field(default_factory=list, max_length=80)
    questions: list[Question] = Field(default_factory=list)
    counterevidence_ids: list[str] = Field(default_factory=list)
    change_refs: list[str] = Field(default_factory=list)


PROTOCOLS = {
    "REASON": TypeAdapter(Reason),
    "DRAFT": TypeAdapter(Draft),
    "NECESSITY": TypeAdapter(Necessity),
    "QUALITY": TypeAdapter(Quality),
}
PROMPTS = {
    "REASON": (
        "Start with the user-facing outcome: what must a reader be able to do or decide "
        "after this change? State it in reader_goal before choosing surfaces. For a workflow "
        "identify its complete ordered handoff and material prerequisites; for an API or "
        "configuration change identify the exact entries, applicability, defaults and "
        "exception behavior needed to use it. Pick evidence and maintained locations for "
        "these needs, checking authoritative references and user guides for stale parallel "
        "claims. For each proposed surface cite "
        "doc_evidence_ids. A single active change can be inherited; when there is no active "
        "change, use change_refs for the relevant facts in change_ledger. If several distinct "
        "changes matter, describe them in changes with their own implementation citations, "
        "and research one change at a time. Omit task_quote when implementation is available. "
        "When no implementation is supplied, a changes "
        "entry may quote an explicit new behavior or contract verbatim in task_quote. A general "
        "request, linked discussion or existing docs alone does not specify changed behavior. "
        "For an explicit documentation-only request with no implementation, propose with "
        "documentation_request_quote copied verbatim from the authored task when existing "
        "repository docs substantiate the exact requested facts. Cite these docs in each "
        "requirement. If the requested behavior cannot be established, investigate or stop. "
        "An existing target can be read by runtime. A new file needs adjacent "
        "documentation or a navigation/convention source. Exact proof of an outdated claim "
        "comes during review. Propose only new or changed paths. A no_change proposal is "
        "reviewed independently; explain why no user-facing gap exists and cite both sides "
        "where available. On each proposed surface include requirements: distinct user-visible "
        "facts or actions the final document must express, each with statement, placement "
        "(maintained section or example), and optional observed evidence_ids. Include "
        "material defaults, conditions, failure behavior and actionable examples when supported. "
        "Use existing sources and do not restate the path rationale as a requirement. Prefer "
        "the main user guide over a changelog when the change affects usage. Stop researching "
        "once a grounded proposal can be drafted."
    ),
    "DRAFT": (
        "Write exact edits from the supplied original source. Use each target's requirements to "
        "cover the supported facts in the specified maintained location. Preserve existing correct "
        "guidance and make reader_goal achievable with the changed and retained text. In a "
        "workflow, spell out the ordered steps and the handoffs between them. In API and "
        "configuration examples, check literal values and argument order against cited sources. "
        "Create only an approved new path; delete only with the full source visible. "
        "For a revision, return only edits addressing the supplied defects, list their defect_ids, "
        "and provide replace_edit_ids only for prior edits these new edits supersede. Unlisted "
        "prior edits are retained, including other edits on the same path."
    ),
    "NECESSITY": (
        "Given the task, observed implementation, and current docs, independently determine "
        "whether any documentation change is needed. Treat the candidate as an untrusted "
        "hypothesis. Ignore its polish and earlier target rationale. Use keep_patch when a "
        "documented gap needs editing, even if the candidate was no_change; cite observations. "
        "Use no_change only when cited change evidence and docs explain why no user-facing gap "
        "remains. Explicitly cite change_refs or a change-side evidence ID; a docs-only "
        "no-change decision has insufficient evidence. Treat task specifications as supplied "
        "requirements, with no assumption that implementation was inspected. "
        "For a proposed new file, cite the observed related docs that make it redundant. "
        "To reject an existing patch, state why its claimed gap does not exist and give "
        "counterevidence_ids with documentation and explicit change-side citations. "
        "Include a repository source "
        "that was not cited to draft the patch. Research uncertainty. Do not review wording."
    ),
    "QUALITY": (
        "Independently reconstruct what a reader must do or decide from task and source evidence. "
        "Walk that scenario against the final text and identify the first missing step, wrong "
        "action or stale authoritative page before consulting reader_goal and requirements. "
        "Look for evidence outside the chosen documents even when all listed requirements "
        "appear satisfied. Compare code and documentation literals in context, distinguishing "
        "historical notes from currently applicable guidance. For a gap outside edited paths, "
        "identify the tracked path and give its concrete obligation in expected, or request "
        "targeted research if the authoritative location is unknown. For every satisfied "
        "requirement report coverage with requirement_id and a short exact quote from that "
        "path's final text, whether retained or added. For a requirement to remove a stale "
        "statement, use the original evidence. Literal evidence shows exact source occurrences "
        "and whether replacement text contains them. Check meaning, alternatives, and order. "
        "For removal coverage set absent true and quote the removed original text. Check examples, "
        "contradictions elsewhere in the edited text, and placement in the maintained guide. "
        "Report revise for defects, with path, problem, requirement_id if known, and observed "
        "evidence_ids. Use only IDs listed under requirements for coverage and known defects. "
        "List only fully satisfied requirements in coverage. "
        "A defect leaves its requirement open. "
        "For a gap uncovered by the checklist, leave requirement_id empty even when other "
        "requirements are present. A new requirement needs expected (the precise "
        "behavior or documentation obligation) and change-side plus relevant docs evidence. "
        "Keep reporting still-open "
        "defects and recheck previously verified requirements after a revision. Use pass only "
        "when all requirements are supported and there are no defects. Ask only targeted "
        "research questions that could change a concrete requirement or scope. Use counterevidence "
        "only when observed material disproves the need to change docs."
    ),
}

ALIASES = {
    "REASON": {"edit": "propose", "ready_to_edit": "propose"},
    "NECESSITY": {"necessary": "keep_patch", "keep": "keep_patch", "overturn": "no_change"},
}
FIELDS = {
    "research": {"questions", "reason", "change_refs", "changes"},
    "propose": {"reader_goal", "surfaces", "change_refs", "changes", "documentation_request_quote"},
    "no_change": {
        "reason",
        "evidence_ids",
        "counterevidence_ids",
        "questions",
        "change_refs",
        "changes",
    },
    "counterevidence": {"reason", "counterevidence_ids", "change_refs"},
    "keep_patch": {"reason", "evidence_ids"},
    "pass": {"reason", "coverage", "defects"},
    "revise": {"defects", "reason", "coverage"},
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
        data = {k: v for k, v in data.items() if k in {"edits", "replace_edit_ids", "defect_ids"}}
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
