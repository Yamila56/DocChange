"""Executable maintenance plans and auditable evidence relationships.

Inspired by DeepResearch's layered DAG scheduling, implemented for DocChange's
existing receipt/checkpoint contract; no upstream runtime code is imported.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from shopresolve.errors import DomainError
from shopresolve.service import digest

from .coverage import Requirement, require_plan
from .runtime import Edit


class ImpactAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["public_change", "internal_only", "uncertain"]
    evidence_ids: list[str] = Field(min_length=1, max_length=12)
    reason: str = Field(min_length=1, max_length=1600)


class MaintenanceTask(Requirement):
    dependencies: list[str] = Field(default_factory=list, max_length=12)
    surfaces: list[str] = Field(min_length=1, max_length=6)
    queries: list[str] = Field(min_length=1, max_length=4)
    code_queries: list[str] = Field(default_factory=list, max_length=4)
    path_hints: list[str] = Field(default_factory=list, max_length=4)


class MaintenanceEdit(Edit):
    requirement_ids: list[str] = Field(min_length=1, max_length=12)
    evidence_ids: list[str] = Field(min_length=1, max_length=24)


class CoverageCell(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_id: str
    surface: str
    status: Literal["edit_required", "no_change_supported", "unresolved"]
    paths: list[str] = Field(default_factory=list, max_length=6)
    evidence_ids: list[str] = Field(default_factory=list, max_length=24)
    reason: str = Field(min_length=1, max_length=1600)


class MaintenanceDefect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal[
        "MissingEvidence",
        "WrongTarget",
        "UnsupportedEdit",
        "OutdatedReference",
        "IncompleteCoverage",
        "VersionConflict",
    ]
    requirement_id: str
    surface: str
    path: str = ""
    reason: str = Field(min_length=1, max_length=1600)


class MaintenanceDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["plan", "propose", "no_change", "unresolved", "review"]
    impact: ImpactAssessment | None = None
    tasks: list[MaintenanceTask] = Field(default_factory=list, max_length=12)
    edits: list[MaintenanceEdit] = Field(default_factory=list, max_length=30)
    coverage: list[CoverageCell] = Field(default_factory=list, max_length=72)
    evidence_ids: list[str] = Field(default_factory=list, max_length=30)
    defects: list[MaintenanceDefect] = Field(default_factory=list, max_length=24)
    ready: bool = False
    text: str = Field(default="", max_length=4000)


def task_layers(tasks):
    """Deterministic Kahn layers; reject dangling dependencies and cycles."""
    by_id = {task["id"]: task for task in tasks}
    if not tasks or len(by_id) != len(tasks):
        raise DomainError("INVALID_PLAN", "Maintenance tasks must have unique IDs")
    for task in tasks:
        deps = task["dependencies"]
        if len(deps) != len(set(deps)) or set(deps) - by_id.keys() or task["id"] in deps:
            raise DomainError("INVALID_PLAN", "Invalid maintenance dependency")
    pending, done, layers = set(by_id), set(), []
    while pending:
        layer = sorted(key for key in pending if set(by_id[key]["dependencies"]) <= done)
        if not layer:
            raise DomainError("INVALID_PLAN", "Maintenance task graph contains a cycle")
        layers.append(layer)
        done.update(layer)
        pending.difference_update(layer)
    return layers


def validate_maintenance_plan(action, known):
    require_plan(action.tasks, known)
    if action.impact is None or not set(action.impact.evidence_ids) <= known:
        raise DomainError("INVALID_PLAN", "Impact assessment requires known source evidence")
    for task in action.tasks:
        for values in (task.queries, task.code_queries, task.path_hints, task.surfaces):
            if any(not value.strip() or len(value) > 512 for value in values):
                raise DomainError("INVALID_PLAN", "Use nonempty bounded maintenance plan fields")
        if len(task.surfaces) != len(set(task.surfaces)):
            raise DomainError("INVALID_PLAN", "Duplicate maintenance surface")
    tasks = [task.model_dump() for task in action.tasks]
    task_layers(tasks)
    return tasks


def validate_cells(action, tasks, evidence):
    expected = {(t["id"], surface) for t in tasks for surface in t["surfaces"]}
    keys = [(cell.requirement_id, cell.surface) for cell in action.coverage]
    if set(keys) != expected or len(keys) != len(expected):
        raise DomainError("INCOMPLETE_COVERAGE", "Assess every planned requirement/surface once")
    observed = {key: item for key, item in evidence.items() if item.get("root")}
    for cell in action.coverage:
        if not set(cell.evidence_ids) <= evidence.keys():
            raise DomainError("UNKNOWN_EVIDENCE", "Coverage cites unknown evidence")
        if cell.status != "unresolved" and not set(cell.evidence_ids) & observed.keys():
            raise DomainError("MISSING_EVIDENCE", "A resolved cell requires repository evidence")
        paths = {observed[e]["path"] for e in cell.evidence_ids if e in observed}
        if cell.status == "no_change_supported" and not set(cell.paths) <= paths:
            raise DomainError("MISSING_EVIDENCE", "No-change paths must have cited observations")
        if cell.status == "edit_required" and not any(
            cell.requirement_id in edit.requirement_ids and edit.path in cell.paths
            for edit in action.edits
        ):
            raise DomainError("UNBOUND_EDIT", "Edit-required cell needs an associated path/edit")
    if any(cell.status == "unresolved" for cell in action.coverage):
        raise DomainError("COVERAGE_UNRESOLVED", "Missing evidence is unresolved, not no-change")
    for edit in action.edits:
        cited = [observed[key] for key in edit.evidence_ids if key in observed]
        if not cited or (
            edit.operation != "create"
            and not any(item["root"] == "docs" and item["path"] == edit.path for item in cited)
        ):
            raise DomainError(
                "MISSING_EVIDENCE", "An existing-file edit must cite its observed path"
            )
        if any(
            not any(
                cell.requirement_id == rid
                and cell.status == "edit_required"
                and edit.path in cell.paths
                for cell in action.coverage
            )
            for rid in edit.requirement_ids
        ):
            raise DomainError("UNBOUND_EDIT", "Every edited requirement/path needs a coverage cell")


def evidence_graph(tasks, evidence, candidate=None, review=None):
    """A relation artifact, not a claim that an evidence link proves semantics."""
    nodes, edges = [], []
    for key, item in sorted(evidence.items()):
        nodes.append({"id": "evidence:" + key, "type": "evidence", **item})
        edges.extend(
            {"from": "evidence:" + key, "to": "requirement:" + rid, "type": "retrieved_for"}
            for rid in item.get("retrieved_for", [])
        )
    for task in tasks:
        node = "requirement:" + task["id"]
        nodes.append({"id": node, "type": "requirement", "description": task["description"]})
        edges.extend(
            {"from": "evidence:" + e, "to": node, "type": "motivates"} for e in task["evidence_ids"]
        )
        edges.extend(
            {"from": "requirement:" + dependency, "to": node, "type": "precedes"}
            for dependency in task["dependencies"]
        )
    if candidate:
        for index, edit in enumerate(candidate.get("edits", [])):
            node = "edit:" + str(index)
            nodes.append({"id": node, "type": "edit", "path": edit["path"], "hash": digest(edit)})
            edges.extend(
                {"from": "requirement:" + r, "to": node, "type": "addressed_by"}
                for r in edit["requirement_ids"]
            )
            edges.extend(
                {"from": "evidence:" + e, "to": node, "type": "cited_by"}
                for e in edit["evidence_ids"]
            )
        for index, cell in enumerate(candidate.get("coverage", [])):
            node = "coverage:" + str(index)
            nodes.append({"id": node, "type": "coverage", **cell})
            edges.append(
                {"from": "requirement:" + cell["requirement_id"], "to": node, "type": "assessed_by"}
            )
            edges.extend(
                {"from": "evidence:" + e, "to": node, "type": "cited_by"}
                for e in cell["evidence_ids"]
            )
        if review:
            nodes.append({"id": "review:current", "type": "review", **review})
            edges.extend(
                {"from": n["id"], "to": "review:current", "type": "reviewed_by"}
                for n in nodes
                if n["type"] in {"edit", "coverage"}
            )
    return {
        "version": "maintenance-evidence-v1",
        "nodes": nodes,
        "edges": edges,
        "semantic_status": "Model judgments with checked references; requires human approval",
    }
