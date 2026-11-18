"""Batched investigation with runtime-owned surface state and two independent reviews."""

import asyncio

import httpx

from shopresolve.errors import DomainError
from shopresolve.service import digest

from .maintenance_batched import BatchedResearchRuntime
from .maintenance_batched_protocol import NoChange, RevisionDraft
from .maintenance_beliefs import implementation_sources
from .maintenance_evidence import compact_nonanchored, edit_identity, review_evidence
from .maintenance_ledger_protocol import (
    LEDGER_MODEL_LIMIT,
    LEDGER_PREFIX,
    PROTOCOLS,
    RESEARCH_LIMITS,
    Question,
    Reason,
    normalize_decision,
)
from .maintenance_recovery import (
    RECOVERABLE_EDITS,
    recovery_identity,
    reserve_recovery,
    target_reads,
)
from .maintenance_runtime import CANDIDATE_ERRORS
from .service import EDITABLE_SUFFIXES, valid_path

REVIEW_PHASES = {"NECESSITY", "QUALITY"}


class LedgerMaintenanceRuntime(BatchedResearchRuntime):
    protocol_version = "maintenance-ledger-v1"
    phase_prefix = LEDGER_PREFIX

    def _model_limit(self):
        return min(self.state["config"]["model_limit"], LEDGER_MODEL_LIMIT)

    def _validate_model_decision(self, phase, step):
        data, changed = normalize_decision(self.state["pending"]["response"])
        action = PROTOCOLS[phase].validate_python(data)
        return self._record_normalization(phase, step, data, action, changed)

    def _record_normalization(self, phase, step, data, action, changed):
        if changed or (isinstance(data, dict) and set(data) - type(action).model_fields.keys()):
            self.data.setdefault("normalized_steps", {})[str(step)] = phase
        return action

    def _research_budget(self, phase):
        history = self.data.get("research_history", [])
        general_used = sum(r["budget"] == "general" for r in history)
        if phase in REVIEW_PHASES or self.data.get("review_followup"):
            bucket = "review"
        elif phase == "IMPACT" or general_used >= RESEARCH_LIMITS["general"]:
            bucket = "impact"
        else:
            bucket = "general"
        return bucket, RESEARCH_LIMITS[bucket] - sum(r["budget"] == bucket for r in history)

    async def _repeat_research(self, phase):
        if not reserve_recovery(self.data, phase, "repeated_research"):
            return await super()._repeat_research(phase)
        self.data["research_feedback"] = "This search was already executed. Try a different source."
        await self._transition(phase, phase)

    async def _after_research(self, scheduled, history, new_ids):
        if not new_ids:
            self.data["research_feedback"] = "No new evidence in this round. Change the source."
        else:
            self.data.pop("research_feedback", None)
        trigger = scheduled["trigger"]
        if trigger in REVIEW_PHASES:
            self.data["review_followup"] = True
        next_phase = "REASON" if scheduled["budget"] == "general" else "IMPACT"
        await self._transition("RESEARCH", next_phase)

    async def _advance_batched(self, phase, prompt):
        if phase == "RESEARCH":
            return await self._research()
        if phase == "RECOVER":
            return await self._recover_edits()
        if self.calls >= self._model_limit() and not self.state.get("pending"):
            return await self._unresolved("MODEL_BUDGET", "Decision budget exhausted")
        try:
            action, step = await self._decision_once(phase, prompt)
        except (httpx.TransportError, TimeoutError):
            if reserve_recovery(self.data, phase, "model_transport"):
                await self._save()
                await asyncio.sleep(0.25)
                return None
            return await self._unresolved(
                "TRANSPORT_EXHAUSTED", "Model transport retries exhausted"
            )
        except ValueError as exc:
            return await self._correction(phase, exc, "MODEL_PROTOCOL")
        except DomainError as exc:
            if exc.code in {"MODEL_BUDGET", "CONTEXT_LIMIT"}:
                return await self._unresolved(exc.code, str(exc))
            raise
        try:
            kind = getattr(action, "kind", "draft")
            self.data.setdefault("decision_history", {})[str(step)] = {
                "phase": phase,
                "kind": kind,
                "reason": getattr(action, "reason", ""),
            }
            if kind == "research":
                self.data["open_questions"] = [q.question for q in action.questions]
                if phase in REVIEW_PHASES:
                    self.data["review"] = action.model_dump()
                return await self._schedule_research(phase, action, step)
            if kind == "unresolved":
                return await self._unresolved("MODEL_UNRESOLVED", action.reason)
            if phase in {"REASON", "IMPACT"}:
                return await self._impact(phase, action, step)
            if phase == "DRAFT":
                await self._write_draft(action, step)
                return await self._after_draft()
            return await self._review(phase, action, step)
        except DomainError as exc:
            if phase == "DRAFT" and exc.code in RECOVERABLE_EDITS:
                if await self._schedule_recovery(action, step, exc):
                    return None
                return await self._correction(phase, exc, exc.code)
            if exc.code in CANDIDATE_ERRORS | {
                "INVALID_PATH",
                "INVALID_REVIEW",
                "INVALID_RESEARCH",
            }:
                return await self._correction(phase, exc, exc.code)
            raise

    async def _correction(self, phase, exc, code):
        if self.data.get("protocol_retries", {}).get(phase):
            return await self._unresolved(code, str(exc)[:800])
        await self._protocol_feedback(phase, exc)

    async def _after_draft(self):
        return await self._transition("DRAFT", "NECESSITY")

    def _known_refs(self, refs):
        if set(refs) - self.data["evidence"].keys():
            raise DomainError("UNKNOWN_EVIDENCE", "Cite only observations from this run")
        return list(dict.fromkeys(refs))

    def _path_refs(self, path):
        return [
            ref
            for ref, item in self.data["evidence"].items()
            if item.get("root") == "docs" and item.get("path") == path
        ]

    def _record_surface(self, path, status, reason, refs):
        surfaces = self.data.setdefault("surface_ledger", {})
        entry = surfaces.setdefault(path, {"supporting_evidence": [], "contrary_evidence": []})
        field = (
            "contrary_evidence" if status in {"dismissed", "no_change"} else "supporting_evidence"
        )
        entry[field] = list(dict.fromkeys([*entry[field], *refs]))
        entry.update(status=status, last_reason=reason)

    async def _impact(self, phase, action, step):
        sources = self.data["evidence"]
        if action.kind == "no_change":
            refs = self._known_refs(action.evidence_ids)
            if not implementation_sources(sources, refs) or not any(
                sources[e].get("root") == "docs" for e in refs
            ):
                return await self._need_evidence(
                    phase, step, "No-change needs implementation and documentation observations"
                )
            if self.data.get("candidate", {}).get("decision") == "patch":
                self.data["proposed_abstention"] = action.model_dump()
            else:
                await self._accept_no_change(action.reason, refs, step)
            self.data.pop("impact_feedback", None)
            self.data["open_questions"] = []
            return await self._transition(phase, "NECESSITY")

        self.data.pop("proposed_abstention", None)
        targets = {t["path"]: t for t in self.data["tasks"] if "path" in t}
        approved, missing = {}, []
        for surface in action.surfaces:
            path = surface.path
            if not valid_path(path) or not path.lower().endswith(tuple(EDITABLE_SUFFIXES)):
                raise DomainError("INVALID_PATH", "Only relative editable document paths are valid")
            refs = self._known_refs(surface.evidence_ids)
            self._record_surface(path, "candidate", surface.reason, refs)
            observed = self._path_refs(path)
            has_doc_support = any(sources[e].get("root") == "docs" for e in refs)
            if not observed and (path in self.env.tracked["docs"] or not has_doc_support):
                missing.append(path)
                self._record_surface(path, "unknown", surface.reason, refs)
                continue
            existing = targets.get(path)
            approved[path] = {
                "id": existing["id"] if existing else "T" + str(len(targets) + len(approved) + 1),
                "path": path,
                "description": surface.reason,
                "evidence_ids": list(dict.fromkeys([*refs, *observed[-3:]])),
                "dependencies": [],
                "surfaces": ["change"],
            }
        if missing:
            return await self._need_evidence(
                phase, step, "Inspect evidence for the proposed documentation paths", missing
            )
        if len(set(targets) | set(approved)) > 8:
            raise DomainError("UNBOUND_EDIT", "At most eight document paths per candidate")
        self.data["tasks"] = list((targets | approved).values())
        for path, target in approved.items():
            self._record_surface(path, "edit", target["description"], target["evidence_ids"])
        self.data["draft_paths"] = sorted(approved)
        self.data.pop("impact_feedback", None)
        self.data["open_questions"] = []
        return await self._transition(phase, "DRAFT")

    async def _need_evidence(self, phase, step, reason, paths=()):
        if self._research_budget("IMPACT")[1] <= 0:
            return await self._unresolved("IMPACT_UNRESOLVED", reason)
        self.data["impact_feedback"] = {"reason": reason, "paths": list(paths)}
        tracked = [path for path in dict.fromkeys(paths) if path in self.env.tracked["docs"]]
        questions = [Question(root="docs", question=reason, paths=[path]) for path in tracked[:4]]
        if not questions and not self.data.get("impact_feedback_requested"):
            summary = self.data["implementation"]
            terms = summary.get("seed_terms", [])[:3]
            code_paths = [
                path
                for path in summary.get("changed_paths", [])
                if path in self.env.tracked["code"]
            ][:2]
            if terms:
                questions.append(Question(root="docs", question=reason, queries=terms))
            if code_paths:
                questions.append(Question(root="code", question=reason, paths=code_paths))
            self.data["impact_feedback_requested"] = True
        if questions:
            self.data["open_questions"] = [
                f"{q.question} {' '.join(q.paths)}".strip() for q in questions
            ]
            return await self._schedule_research(
                "IMPACT", Reason(kind="research", questions=questions), step
            )
        await self._transition(phase, "IMPACT")

    async def _accept_no_change(self, reason, refs, step):
        old_candidate = self.data.get("candidate")
        previous_paths = {t["path"] for t in self.data["tasks"] if "path" in t}
        doc_paths = {
            self.data["evidence"][e]["path"]
            for e in refs
            if self.data["evidence"][e].get("root") == "docs"
        }
        if not self._rebutted_paths(previous_paths, doc_paths):
            raise DomainError("MISSING_EVIDENCE", "Address each approved path before overturning")
        await self._no_change(
            NoChange(kind="no_change", impact="already_documented", evidence_ids=refs, text=reason),
            step,
        )
        implementation = implementation_sources(self.data["evidence"], refs)
        for path in previous_paths:
            used_refs = [*implementation, *(ref for ref in refs if ref in self._path_refs(path))]
            self._record_surface(path, "dismissed", reason, used_refs)
        for path in doc_paths - previous_paths:
            used_refs = [*implementation, *(ref for ref in refs if ref in self._path_refs(path))]
            self._record_surface(path, "no_change", reason, used_refs)
        if old_candidate and old_candidate["decision"] == "patch":
            self.data["previous_candidate"] = old_candidate
            self.data["overturned_patch_hash"] = old_candidate["patch_hash"]

    def _rebutted_paths(self, previous_paths, doc_paths):
        return previous_paths <= doc_paths

    async def _write_draft(self, action, step):
        targets = {t["path"]: t for t in self.data["tasks"]}
        scope = set(self.data["draft_paths"])
        if any(edit.path not in scope for edit in action.edits):
            raise DomainError("REPAIR_SCOPE", "Draft can edit only the supplied paths")
        edits = [
            {
                **edit.model_dump(),
                "requirement_ids": [targets[edit.path]["id"]],
                "evidence_ids": list(
                    dict.fromkeys(
                        [*targets[edit.path]["evidence_ids"], *self._path_refs(edit.path)]
                    )
                )[:24],
            }
            for edit in action.edits
        ]
        previous = self.data.get("candidate", {})
        replace = [edit_identity(e) for e in previous.get("edits", []) if e["path"] in scope]
        await self._propose(
            RevisionDraft(
                kind="revise", edits=edits, replace_edit_ids=replace, text="Documentation update"
            ),
            step,
            phase="REVISE" if previous.get("decision") == "patch" else "EDIT",
        )
        self.data.pop("draft_feedback", None)
        self.data.pop("reread_evidence_ids", None)

    async def _schedule_recovery(self, action, step, exc):
        try:
            reads = target_reads(action.edits, self.data["tasks"], self.env.tracked["docs"])
        except DomainError:
            return False
        if not reads or not reserve_recovery(self.data, "DRAFT", "edit_read"):
            return False
        self.data["recovering"] = {"step": step, "reads": reads}
        self.data["draft_feedback"] = {"code": exc.code, "reason": str(exc)}
        await self._transition("DRAFT", "RECOVER")
        return True

    async def _recover_edits(self):
        recovery = self.data["recovering"]
        refs = []
        reads = recovery["reads"]
        for start in range(0, len(reads), 4):
            batch = reads[start : start + 4]
            key = "reread:" + recovery_identity(batch)
            result = await self._tool(
                recovery["step"],
                key,
                {"kind": "read_many", "root": "docs", "reads": batch},
                self.env,
            )
            missing = [f["path"] for f in result.get("files", []) if not f.get("content")]
            if missing:
                result = await self._tool(
                    recovery["step"],
                    key + ":fallback",
                    {"kind": "read_many", "root": "docs", "paths": missing},
                    self.env,
                )
            refs.extend(f["evidence_id"] for f in result.get("files", []) if f.get("evidence_id"))
        self.data["reread_evidence_ids"] = list(dict.fromkeys(refs))
        self.data.pop("recovering")
        await self._transition("RECOVER", "DRAFT")

    async def _review(self, phase, action, step):
        self.data["review"] = action.model_dump()
        candidate = self.data["candidate"]
        if phase == "NECESSITY":
            refs = self._known_refs(action.evidence_ids)
            if action.kind == "no_change":
                await self._accept_no_change(action.reason, refs, step)
                self.data.pop("proposed_abstention", None)
                return await self._transition(phase, "FINALIZE")
            if not any(self.data["evidence"][ref].get("root") for ref in refs):
                raise DomainError(
                    "MISSING_EVIDENCE", "Necessity needs observed repository evidence"
                )
            if candidate["decision"] == "abstain":
                self.data["previous_candidate"] = self.data.pop("candidate")
                self.data["tasks"] = []
                self.data["review_followup"] = True
                return await self._transition(phase, "IMPACT")
            self.data["necessity"] = {
                "patch_hash": candidate["patch_hash"],
                "evidence_ids": refs,
                "reason": action.reason,
            }
            self.data.pop("proposed_abstention", None)
            return await self._transition(phase, "QUALITY")
        if self.data.get("necessity", {}).get("patch_hash") != candidate["patch_hash"]:
            raise DomainError("CHECKPOINT_INVALID", "Quality requires necessity for this draft")
        if action.kind == "pass":
            return await self._transition(phase, "FINALIZE")
        approved = {t["path"] for t in self.data["tasks"]}
        if not action.paths or not set(action.paths) <= approved:
            raise DomainError("INVALID_REVIEW", "Revision needs approved paths; research new paths")
        revision = digest([candidate["patch_hash"], sorted(set(action.paths))])
        signatures = self.data.setdefault("review_revision_signatures", [])
        if revision in signatures:
            return await self._unresolved("REVIEW_STALLED", "Revision did not change the patch")
        signatures.append(revision)
        self.data["draft_paths"] = sorted(set(action.paths))
        self.data["draft_feedback"] = {"reason": action.reason}
        await self._transition(phase, "DRAFT")

    def _context(self, phase, task_prompt):
        sources = self.data["evidence"]
        if phase == "DRAFT":
            paths = set(self.data["draft_paths"])
            targets = [t for t in self.data["tasks"] if t["path"] in paths]
            path_sources = [self._path_refs(target["path"])[-2:] for target in targets]
            refs = list(
                dict.fromkeys(
                    [
                        *self.data.get("reread_evidence_ids", []),
                        *(ref for observed in path_sources for ref in observed),
                        *(e for t in targets for e in t["evidence_ids"]),
                    ]
                )
            )
            recent = self.data.get("candidate", {}).get("edits", [])
            context = {
                "targets": targets,
                "evidence": review_evidence(sources, refs),
                "prior_edits": [e for e in recent if e["path"] in paths],
                "draft_feedback": self.data.get("draft_feedback"),
            }
        else:
            context = super()._context(
                "VERIFY" if phase in REVIEW_PHASES else "REASON", task_prompt
            )
            context.pop("selective_thinking", None)
            context["remaining_research_rounds"] = self._research_budget(phase)[1]
            context["surface_ledger"] = self.data.get("surface_ledger", {})
            context["open_questions"] = self.data.get("open_questions", [])
            if phase in {"REASON", "IMPACT"}:
                selected = [view["evidence_id"] for view in context["evidence"]]
                context["evidence"] = [
                    compact_nonanchored(view, 2400) for view in review_evidence(sources, selected)
                ]
            for key in ("impact_feedback", "research_feedback", "proposed_abstention"):
                if key in self.data:
                    context[key] = self.data[key]
            if phase in REVIEW_PHASES:
                context["search_coverage"] = [
                    {
                        "question": q["question"],
                        "root": q["root"],
                        "evidence_count": len(q["evidence_ids"]),
                    }
                    for record in self.data.get("research_history", [])
                    for q in record["branches"]
                ]
            if phase == "IMPACT" and self.data.get("review_followup"):
                context["previous_review"] = self.data.get("review")
        if feedback := self.data.get("protocol_feedback", {}).get(phase):
            context["protocol_feedback"] = feedback
        else:
            context.pop("protocol_feedback", None)
        return context

    def _graph(self):
        graph = super()._graph()
        for path, entry in self.data.get("surface_ledger", {}).items():
            node = "surface:" + path
            graph["nodes"].append({"id": node, "type": "surface", "path": path, **entry})
            for field, relation in (
                ("supporting_evidence", "supports"),
                ("contrary_evidence", "contradicts"),
            ):
                graph["edges"].extend(
                    {"from": "evidence:" + ref, "to": node, "type": relation}
                    for ref in entry[field]
                )
        return graph
