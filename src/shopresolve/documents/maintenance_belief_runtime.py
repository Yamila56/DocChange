"""A bounded semantic loop on the existing evidence store, receipts and Git layer."""

import asyncio

import httpx

from shopresolve.errors import DomainError
from shopresolve.service import digest

from .maintenance import NoChangeDecision
from .maintenance_batched import BatchedResearchRuntime
from .maintenance_batched_protocol import RevisionDraft
from .maintenance_belief_protocol import (
    BELIEF_MODEL_LIMIT,
    BELIEF_PREFIX,
    PROTOCOLS,
    Question,
)
from .maintenance_beliefs import impact_targets, implementation_sources, new_belief, update_belief
from .maintenance_recovery import (
    RECOVERABLE_EDITS,
    recovery_identity,
    reserve_recovery,
    target_reads,
)
from .maintenance_runtime import CANDIDATE_ERRORS

PHASES = {
    "RESEARCH": "INVESTIGATE",
    "REASON": "BELIEF_UPDATE",
    "EDIT": "DRAFT",
    "REVISE": "DRAFT_DELTA",
}


class BeliefMaintenanceRuntime(BatchedResearchRuntime):
    initial_phase = "ORIENT"
    protocol_version = "maintenance-belief-v1"
    phase_prefix = BELIEF_PREFIX

    def _protocol(self, phase):
        return PROTOCOLS[phase]

    def _model_limit(self):
        return min(self.state["config"]["model_limit"], BELIEF_MODEL_LIMIT)

    def _candidate_evidence_ids(self, candidate):
        required = super()._candidate_evidence_ids(candidate)
        for surface in self.data.get("belief", {}).get("surfaces", {}).values():
            if (
                surface["status"] in {"confirmed_edit", "confirmed_no_change"}
                or surface["material"]
            ):
                required.update(surface["implementation_evidence_ids"])
                required.update(surface["documentation_evidence_ids"])
        for claim in self.data.get("belief", {}).get("claims", {}).values():
            required.update(claim["contradicting_evidence_ids"])
        return required

    async def _transition(self, previous, phase):
        await super()._transition(PHASES.get(previous, previous), PHASES.get(phase, phase))

    async def _decision(self, phase, prompt):
        while True:
            try:
                return await self._decision_once(phase, prompt)
            except (httpx.TransportError, TimeoutError) as exc:
                if not reserve_recovery(self.data, phase, "model_transport", limit=2):
                    raise DomainError(
                        "TRANSPORT_EXHAUSTED", "Model transport retries exhausted"
                    ) from exc
                await self._save()
                await asyncio.sleep(0.25)
            except ValueError as exc:
                # Invalid JSON/types still require a bounded semantic correction.
                await self._protocol_feedback(phase, exc)

    async def _advance_batched(self, phase, prompt):
        if phase == "INVESTIGATE":
            return await self._research()
        if phase == "IMPACT_GATE":
            return await self._impact_gate()
        if phase == "RECOVER":
            return await self._recover_edit()
        if self.calls >= self._model_limit() and not self.state.get("pending"):
            return await self._unresolved("MODEL_BUDGET", "Semantic decision budget exhausted")
        try:
            action, step = await self._decision(phase, prompt)
        except ValueError:
            return await self._unresolved("MODEL_PROTOCOL", "Model protocol correction exhausted")
        except DomainError as exc:
            if exc.code not in {"MODEL_BUDGET", "TRANSPORT_EXHAUSTED", "CONTEXT_LIMIT"}:
                raise
            return await self._unresolved(exc.code, str(exc))
        try:
            if phase == "ORIENT":
                self.data["belief"] = new_belief(action, step)
                return await self._schedule_research(phase, action, step)
            if phase == "BELIEF_UPDATE":
                update_belief(self.data["belief"], action, self.data["evidence"], step)
                if action.next_action == "unresolved":
                    return await self._unresolved("MODEL_UNRESOLVED", action.reason)
                if action.next_action == "investigate":
                    return await self._schedule_research(phase, action, step)
                self.data["impact_step"] = step
                await self._transition(phase, "IMPACT_GATE")
            elif phase in {"DRAFT", "DRAFT_DELTA"}:
                await self._draft(action, step, phase)
                self.data["belief"]["candidate_hash"] = self.data["candidate"]["patch_hash"]
                await self._transition(phase, "VERIFY")
            else:
                return await self._review(action, step)
        except DomainError as exc:
            if phase in {"DRAFT", "DRAFT_DELTA"} and exc.code in RECOVERABLE_EDITS:
                if await self._schedule_edit_recovery(phase, action, step, exc):
                    return None
            if exc.code not in CANDIDATE_ERRORS | {
                "INVALID_REVIEW",
                "INVALID_RESEARCH",
                "INVALID_PATH",
            }:
                raise
            if self.data.get("protocol_retries", {}).get(phase, 0):
                return await self._unresolved(exc.code, str(exc))
            await self._protocol_feedback(phase, exc)
        return None

    async def _schedule_research(self, phase, action, step):
        if not action.questions:
            raise DomainError("INVALID_RESEARCH", "Provide a concrete research question")
        # Model output is retained in ModelStep. Only executable mechanics are bounded here.
        questions = [
            Question(
                question=q.question,
                root=q.root,
                queries=list(dict.fromkeys(q.queries))[:3],
                paths=list(dict.fromkeys(q.paths))[:2],
                anchor=q.anchor[:240],
            )
            for q in action.questions[:4]
        ]
        self.data["research_selection"] = {
            "step": step,
            "offered_questions": len(action.questions),
            "selected_questions": len(questions),
            "offered_queries": sum(len(q.queries) for q in action.questions),
            "selected_queries": sum(len(q.queries) for q in questions),
        }
        action = action.model_copy(update={"questions": questions})
        return await super()._schedule_research(phase, action, step)

    async def _impact_gate(self):
        belief = self.data["belief"]
        try:
            targets, refs, gaps = impact_targets(belief, self.data["evidence"])
        except DomainError as exc:
            return await self._unresolved(exc.code, str(exc))
        self.data["impact_gate"] = {
            "belief_version": belief["version"],
            "targets": targets,
            "no_change_evidence_ids": refs,
            "gaps": [s["key"] for s in gaps],
        }
        if gaps:
            if self._research_budget("IMPACT_GATE")[1]:
                questions = [
                    Question(
                        root="docs",
                        question="Resolve user-visible gap and document ownership",
                        paths=[s["path"]],
                        queries=[s["changed_contract"][:512]],
                    )
                    for s in gaps[:4]
                ]
                action = PROTOCOLS["BELIEF_UPDATE"].validate_python(
                    {
                        "kind": "update",
                        "next_action": "investigate",
                        "reason": "Impact needs supporting observations",
                        "questions": questions,
                    }
                )
                return await self._schedule_research(
                    "IMPACT_GATE", action, self.data["impact_step"]
                )
            return await self._unresolved(
                "IMPACT_UNRESOLVED", "Material impact lacks sufficient evidence"
            )
        if not targets:
            if not refs:
                return await self._unresolved(
                    "IMPACT_UNRESOLVED", "No supported edit or no-change decision"
                )
            try:
                await self._set_no_change(
                    refs,
                    "Observed implementation and docs support no material documentation gap",
                    self.data["impact_step"],
                    "already_documented",
                )
            except (DomainError, ValueError) as exc:
                return await self._unresolved("IMPACT_UNRESOLVED", str(exc))
            await self._transition("IMPACT_GATE", "VERIFY")
            return None
        self.data["tasks"] = targets
        has_draft = self.data.get("candidate", {}).get("decision") == "patch"
        await self._transition("IMPACT_GATE", "DRAFT_DELTA" if has_draft else "DRAFT")
        return None

    async def _set_no_change(self, refs, reason, step, impact):
        sources = self.data["evidence"]
        if set(refs) - sources.keys():
            raise DomainError("UNKNOWN_EVIDENCE", "No-change cites unknown sources")
        impl = implementation_sources(sources, refs)
        docs = {}
        for ref in refs:
            if sources[ref].get("root") == "docs":
                docs.setdefault(sources[ref]["path"], []).append(ref)
        if not impl or not docs:
            raise DomainError(
                "MISSING_EVIDENCE", "No-change needs both implementation and documentation"
            )
        assessment = {
            "kind": "internal_only" if impact == "internal_only" else "public_change",
            "evidence_ids": list(dict.fromkeys([impl[0], next(iter(docs.values()))[0]])),
            "reason": reason[:1600],
        }
        tasks, coverage = [], []
        for index, (path, document_refs) in enumerate(docs.items(), 1):
            bound = list(dict.fromkeys([*impl, *document_refs]))
            rid = f"NC{index:03d}"
            tasks.append(
                {
                    "id": rid,
                    "path": path,
                    "description": reason[:600],
                    "evidence_ids": bound,
                    "dependencies": [],
                    "surfaces": ["change"],
                }
            )
            coverage.append(
                {
                    "requirement_id": rid,
                    "surface": "change",
                    "status": "no_change_supported",
                    "paths": [path],
                    "evidence_ids": bound,
                    "reason": reason[:1600],
                }
            )
        decision = NoChangeDecision.model_validate(
            {
                "kind": "no_change",
                "text": reason[:4000],
                "impact": assessment,
                "evidence_ids": list(dict.fromkeys(refs)),
                "edits": [],
                "coverage": coverage,
            }
        )
        self.data["tasks"], self.data["impact"] = tasks, assessment
        await self._candidate(decision, step, repair=False)

    async def _draft(self, action, step, phase):
        edits = []
        for raw in action.edits:
            targets = [t for t in self.data["tasks"] if t["path"] == raw.path]
            if not targets:
                raise DomainError("REPAIR_SCOPE", "Draft path has no approved impact target")
            refs = list(
                dict.fromkeys(
                    [
                        *raw.evidence_ids,
                        *(e for t in targets for e in t["evidence_ids"]),
                        *(
                            key
                            for key, item in self.data["evidence"].items()
                            if item.get("root") == "docs"
                            and item.get("path") == raw.path
                            and (not raw.old or raw.old in item["content"])
                        ),
                    ]
                )
            )
            edits.append(
                {
                    **raw.model_dump(exclude={"evidence_ids"}),
                    "count": 1,
                    "requirement_ids": [t["id"] for t in targets],
                    "evidence_ids": refs,
                }
            )
        adapted = RevisionDraft.model_validate(
            {
                "kind": "revise",
                "edits": edits,
                "text": action.text,
                "replace_edit_ids": action.replace_edit_ids,
            }
        )
        await super()._propose(adapted, step, phase="REVISE" if phase == "DRAFT_DELTA" else "EDIT")

    async def _schedule_edit_recovery(self, phase, action, step, exc):
        if not reserve_recovery(self.data, phase, "edit_read"):
            return False
        try:
            reads = target_reads(action.edits, self.data["tasks"], self.env.tracked["docs"])
        except DomainError:
            return False
        self.data["recovering"] = {"phase": phase, "step": step, "reads": reads, "code": exc.code}
        self.data["draft_feedback"] = {
            "code": exc.code,
            "reason": str(exc),
            "instruction": "Rebuild exact edits from reread observations",
        }
        await self._transition(phase, "RECOVER")
        return True

    async def _recover_edit(self):
        recovery = self.data["recovering"]
        for start in range(0, len(recovery["reads"]), 4):
            reads = recovery["reads"][start : start + 4]
            key = "recover:" + recovery_identity(reads)
            result = await self._tool(
                recovery["step"],
                key,
                {
                    "kind": "read_many",
                    "root": "docs",
                    "reads": reads,
                },
                self.env,
            )
            missing = [f["path"] for f in result.get("files", []) if not f.get("content")]
            if missing:
                await self._tool(
                    recovery["step"],
                    key + ":fallback",
                    {
                        "kind": "read_many",
                        "root": "docs",
                        "paths": missing,
                    },
                    self.env,
                )
        self.data["recovery_evidence_ids"] = [
            key
            for key, value in self.data["evidence"].items()
            if value.get("path") in {r["path"] for r in recovery["reads"]}
        ]
        phase = recovery["phase"]
        self.data.pop("recovering")
        await self._transition("RECOVER", phase)
        return None

    async def _review(self, action, step):
        checks = [
            getattr(action, key) for key in ("necessity", "factuality", "completeness", "placement")
        ]
        refs = set(action.evidence_ids)
        refs.update(ref for check in checks for ref in check.evidence_ids)
        if refs - self.data["evidence"].keys():
            raise DomainError("UNKNOWN_EVIDENCE", "Review cites unknown observations")
        self.data["review"] = action.model_dump()
        if action.kind == "unresolved":
            return await self._unresolved("REVIEW_UNRESOLVED", action.reason)
        if action.kind == "research":
            return await self._schedule_research("VERIFY", action, step)
        if action.kind in {"pass", "no_change"}:
            if any(check.status != "supported" or not check.evidence_ids for check in checks):
                raise DomainError("INVALID_REVIEW", "All four review checks need explicit support")
            if action.kind == "no_change":
                paths = {t["path"] for t in self.data["tasks"] if t.get("path")}
                cited_paths = {
                    self.data["evidence"][ref].get("path")
                    for ref in refs
                    if self.data["evidence"][ref].get("root") == "docs"
                }
                if not paths <= cited_paths:
                    raise DomainError(
                        "MISSING_EVIDENCE", "No-change must address all draft targets"
                    )
                if self.data.get("candidate"):
                    self.data["previous_candidate"] = self.data["candidate"]
                await self._set_no_change(sorted(refs), action.reason, step, action.impact)
                self.data["belief"]["candidate_hash"] = None
                self.data["belief"]["decision_override"] = {
                    "step": step,
                    "reason": action.reason,
                    "evidence_ids": sorted(refs),
                }
            self.data["belief"]["updates"].append(
                {"step": step, "kind": action.kind, "reason": action.reason}
            )
            await self._transition("VERIFY", "FINALIZE")
            return None
        if not action.paths or set(action.paths) - {
            t["path"] for t in self.data["tasks"] if t.get("path")
        }:
            raise DomainError(
                "INVALID_REVIEW", "Revision needs approved paths; research other surfaces"
            )
        fingerprint = digest(
            [action.paths, action.reason, self.data["candidate"].get("patch_hash")]
        )
        if fingerprint in self.data.setdefault("review_revision_signatures", []):
            return await self._unresolved(
                "REVIEW_STALLED", "Review repeated without draft progress"
            )
        self.data["review_revision_signatures"].append(fingerprint)
        await self._transition("VERIFY", "DRAFT_DELTA")
        return None

    def _context(self, phase, task_prompt):
        alias = {
            "ORIENT": "REASON",
            "BELIEF_UPDATE": "REASON",
            "DRAFT": "EDIT",
            "DRAFT_DELTA": "REVISE",
        }.get(phase, phase)
        context = super()._context(alias, task_prompt)
        if feedback := self.data.get("protocol_feedback", {}).get(phase):
            context["protocol_feedback"] = feedback
        context["selective_thinking"] = self.state["config"].get("belief_thinking", True)
        context["belief_state"] = self.data.get("belief", {})
        context["approved_impact"] = self.data.get("impact_gate", {})
        context["remaining_model_decisions"] = max(0, self._model_limit() - self.calls)
        context["draft_feedback"] = self.data.get("draft_feedback")
        if self.data.get("review", {}).get("kind") == "research":
            context["review_requested_research"] = self.data["review"]["questions"]
        if phase in {"DRAFT", "DRAFT_DELTA"}:
            known = {e["evidence_id"] for e in context["evidence"]}
            context["evidence"] = [
                self.data["evidence"][key]
                for key in self.data.get("recovery_evidence_ids", [])
                if key not in known
            ] + context["evidence"]
        if phase == "VERIFY":
            cited = self._candidate_evidence_ids(self.data["candidate"])
            context["challenge_evidence"] = [
                {
                    **self.data["evidence"][key],
                    "content": self.data["evidence"][key]["content"][:1600],
                    "excerpt_truncated": len(self.data["evidence"][key]["content"]) > 1600,
                }
                for key in self._ranked_evidence()
                if key not in cited
            ][:8]
            context["search_coverage"] = self.data.get("research_history", [])
        return context

    def _graph(self):
        graph = super()._graph()
        belief = self.data.get("belief", {})
        for claim in belief.get("claims", {}).values():
            node = "belief:" + claim["id"]
            graph["nodes"].append({**claim, "id": node, "type": "belief"})
            graph["edges"].extend(
                {"from": "evidence:" + ref, "to": node, "type": "supports"}
                for ref in claim["evidence_ids"]
            )
            graph["edges"].extend(
                {"from": "evidence:" + ref, "to": node, "type": "contradicts"}
                for ref in claim["contradicting_evidence_ids"]
            )
        return graph
