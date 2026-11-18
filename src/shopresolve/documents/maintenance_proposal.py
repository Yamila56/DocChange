"""Draft evidence-backed hypotheses, then verify necessity and content separately."""

import asyncio
import copy
from pathlib import PurePosixPath

import httpx

from shopresolve.errors import DomainError
from shopresolve.service import digest

from .maintenance_beliefs import implementation_sources
from .maintenance_evidence import compact_nonanchored, review_evidence
from .maintenance_ledger import REVIEW_PHASES, LedgerMaintenanceRuntime
from .maintenance_ledger_protocol import Question
from .maintenance_proposal_protocol import PROPOSAL_PREFIX, VERIFY_RESERVE, Reason, parse_decision
from .service import EDITABLE_SUFFIXES, valid_path

RECOVERABLE_REVIEW_STOPS = {
    "CONTEXT_LIMIT",
    "EMPTY_PATCH",
    "EVIDENCE_REQUIRED",
    "INCOMPLETE_COVERAGE",
    "INVALID_PATH",
    "INVALID_RESEARCH",
    "INVALID_REVIEW",
    "MISSING_EVIDENCE",
    "MODEL_BUDGET",
    "MODEL_PROTOCOL",
    "MODEL_UNRESOLVED",
    "PATCH_MISMATCH",
    "READ_FIRST",
    "REPAIR_SCOPE",
    "RESEARCH_BUDGET",
    "REVIEW_PROVIDER_UNAVAILABLE",
    "REVIEW_STALLED",
    "SEARCH_SATURATED",
    "TRANSPORT_EXHAUSTED",
    "UNBOUND_EDIT",
    "UNKNOWN_EVIDENCE",
    "UNOBSERVED_EDIT",
    "VERIFY_RESERVE",
}


class ProposalMaintenanceRuntime(LedgerMaintenanceRuntime):
    protocol_version = "maintenance-proposal-v1"
    phase_prefix = PROPOSAL_PREFIX

    def _validate_model_decision(self, phase, step):
        data = self.state["pending"]["response"]
        action, changed = parse_decision(phase, data)
        return self._record_normalization(phase, step, data, action, changed)

    def _candidate_evidence_ids(self, candidate):
        refs = super()._candidate_evidence_ids(candidate)
        if candidate["decision"] == "abstain":
            refs.update(self.data.get("counterevidence_ids", []))
        return refs

    def _research_budget(self, phase):
        remaining = self._model_limit() - self.calls
        if phase in REVIEW_PHASES:
            return "review", max(0, remaining)
        # One follow-up REASON, one DRAFT, and two reviews after a research decision.
        bucket = "review" if self.data.get("review_followup") else "pre_draft"
        return bucket, max(0, remaining - (VERIFY_RESERVE + 1))

    async def _advance_batched(self, phase, prompt):
        pending = self.state.get("pending")
        remaining = self._model_limit() - self.calls
        if phase == "DRAFT" and not pending:
            reserve = 1 if self.data.get("quality_revision") else VERIFY_RESERVE
            if remaining <= reserve:
                return await self._unresolved(
                    "VERIFY_RESERVE", "Keep enough decisions to review the next draft"
                )
        if phase == "REASON" and not pending and remaining < 2:
            return await self._unresolved("VERIFY_RESERVE", "A proposal needs independent review")
        try:
            return await super()._advance_batched(phase, prompt)
        except httpx.HTTPStatusError as exc:
            if "best_valid_candidate" not in self.data:
                raise
            return await self._unresolved(
                "REVIEW_PROVIDER_UNAVAILABLE", f"Provider returned HTTP {exc.response.status_code}"
            )

    async def _schedule_research(self, phase, action, step):
        remaining = self._model_limit() - self.calls
        if phase == "REASON" and remaining <= VERIFY_RESERVE + 1:
            raise DomainError("INVALID_RESEARCH", "Finish with a proposal or explicitly unresolved")
        if not action.questions:
            raise DomainError("INVALID_RESEARCH", "Research needs a concrete question")
        return await super()._schedule_research(phase, action, step)

    async def _after_research(self, scheduled, history, new_ids):
        trigger = scheduled["trigger"]
        if not new_ids:
            self.data["research_feedback"] = "No new evidence. Change the query or decide."
        else:
            self.data.pop("research_feedback", None)
        next_phase = "REASON"
        if trigger in REVIEW_PHASES:
            self.data["review_followup"] = True
            candidate = self.data["candidate"]
            if not new_ids or self._model_limit() - self.calls < VERIFY_RESERVE + 2:
                next_phase = "NECESSITY" if candidate["decision"] == "abstain" else trigger
        await self._transition("RESEARCH", next_phase)

    @staticmethod
    def _adjacent(path, document_path):
        target = PurePosixPath(path).parent
        source = PurePosixPath(document_path)
        if source.parent == target:
            return True
        navigation = {"index", "readme", "toc", "contributing"}
        return source.parent in target.parents and source.stem.lower() in navigation

    def _authorized_path(self, path):
        if not valid_path(path) or not path.lower().endswith(tuple(EDITABLE_SUFFIXES)):
            raise DomainError("INVALID_PATH", "Use an editable path inside the documentation root")

    async def _read_proposed_paths(self, paths, step):
        for start in range(0, len(paths), 4):
            batch = paths[start : start + 4]
            await self._tool(
                step,
                "proposal-read:" + digest(batch)[:16],
                {"kind": "read_many", "root": "docs", "paths": batch},
                self.env,
            )

    async def _impact(self, phase, action, step):
        # The inherited dispatcher calls this for REASON; no IMPACT phase is entered.
        sources = self.data["evidence"]
        if action.kind == "no_change":
            refs = self._known_refs(action.evidence_ids)
            reason = self._require_reason(action)
            proposal = {"decision": "abstain", "reason": reason, "evidence_ids": refs}
            self.data["proposed_abstention"] = proposal
            if self.data.get("candidate", {}).get("decision") != "patch":
                self.data["candidate"] = {**proposal, "edits": [], "coverage": []}
            return await self._transition(phase, "NECESSITY")
        if not action.surfaces:
            raise DomainError("UNBOUND_EDIT", "Propose an affected documentation surface")

        tracked = self.env.tracked["docs"]
        targets = {target["path"]: target for target in self.data["tasks"] if "path" in target}
        approved, needs_read, unsupported = {}, [], []
        for surface in action.surfaces:
            path = surface.path
            self._authorized_path(path)
            refs = self._known_refs(surface.evidence_ids)
            docs = [sources[ref]["path"] for ref in refs if sources[ref].get("root") == "docs"]
            implementation = implementation_sources(sources, refs)
            if (
                not implementation
                or not docs
                or (path not in tracked and not any(self._adjacent(path, doc) for doc in docs))
            ):
                unsupported.append(path)
                self._record_surface(path, "unknown", surface.reason, refs)
                continue
            description = surface.reason.strip()
            if not description:
                raise DomainError("MISSING_EVIDENCE", "Explain the proposed effect on this path")
            approved[path] = {
                "id": "T" + digest(path)[:16],
                "path": path,
                "description": description[:600],
                "evidence_ids": refs,
                "dependencies": [],
                "surfaces": ["change"],
            }
            if path in tracked and not self._path_refs(path) and path not in needs_read:
                needs_read.append(path)
        if needs_read:
            await self._read_proposed_paths(needs_read, step)
        for path in needs_read:
            if not self._path_refs(path):
                target = approved.pop(path)
                unsupported.append(path)
                self._record_surface(path, "unknown", target["description"], target["evidence_ids"])
        if not approved:
            raise DomainError(
                "MISSING_EVIDENCE",
                "No proposal has implementation and relevant docs or source spans",
            )
        if len(targets | approved) > 8:
            raise DomainError("UNBOUND_EDIT", "At most eight document paths per candidate")
        self.data["tasks"] = list((targets | approved).values())
        self.data["draft_paths"] = sorted(approved)
        self.data["open_questions"] = [f"Check whether {p} is material" for p in unsupported]
        self.data.pop("proposed_abstention", None)
        self.data.pop("quality_revision", None)
        for path, target in approved.items():
            self._record_surface(path, "candidate", target["description"], target["evidence_ids"])
        return await self._transition(phase, "DRAFT")

    @staticmethod
    def _require_reason(action):
        if not action.reason:
            raise DomainError("MISSING_EVIDENCE", "Explain why no user-facing gap remains")
        return action.reason

    def _rebutted_paths(self, previous_paths, doc_paths):
        return all(
            path in doc_paths
            or (
                path not in self.env.tracked["docs"]
                and any(self._adjacent(path, doc) for doc in doc_paths)
            )
            for path in previous_paths
        )

    async def _write_draft(self, action, step):
        tracked = self.env.tracked["docs"]
        if any((edit.operation == "create") == (edit.path in tracked) for edit in action.edits):
            raise DomainError("REPAIR_SCOPE", "Create only new paths and edit only existing files")
        try:
            await super()._write_draft(action, step)
        except DomainError as exc:
            if exc.code in {"CREATE_CONFLICT", "INVALID_PATCH", "READ_ONLY", "SCOPE_VIOLATION"}:
                raise DomainError("REPAIR_SCOPE", str(exc)) from exc
            if exc.code == "REPOSITORY_IO" and "best_valid_candidate" in self.data:
                raise DomainError("PATCH_MISMATCH", "Revision failed Git validation") from exc
            raise

    async def _after_draft(self):
        approved = self.data.get("necessity", {})
        candidate = self.data["candidate"]
        for target in self.data["tasks"]:
            path = target["path"]
            self._record_surface(path, "edit", target["description"], target["evidence_ids"])
        if "best_valid_candidate" not in self.data:
            self._checkpoint_candidate()
        if self.data.pop("quality_revision", False):
            if approved.get("paths") != sorted(candidate["changed_paths"]):
                # The delta guard should make scope expansion impossible.
                raise DomainError("CHECKPOINT_INVALID", "Revision changed the necessity scope")
            approved["patch_hash"] = candidate["patch_hash"]
            return await self._transition("DRAFT", "QUALITY")
        return await super()._after_draft()

    def _checkpoint_candidate(self, *, defect_count=None):
        candidate = self.data["candidate"]
        prior = self.data.get("best_valid_candidate")
        if prior and prior["candidate"]["patch_hash"] != candidate["patch_hash"]:
            prior_count = prior["defect_count"]
            same_scope = prior["candidate"]["changed_paths"] == candidate["changed_paths"]
            if defect_count != 0 and (
                not same_scope
                or prior_count is None
                or defect_count is None
                or defect_count >= prior_count
            ):
                return
        if prior and prior["candidate"]["patch_hash"] == candidate["patch_hash"]:
            previous_count = prior["defect_count"]
            if previous_count is not None and (
                defect_count is None or previous_count < defect_count
            ):
                defect_count = previous_count
        self.data["best_valid_candidate"] = {
            "candidate": copy.deepcopy(candidate),
            "tasks": copy.deepcopy(self.data["tasks"]),
            "defect_count": defect_count,
        }

    async def _unresolved(self, code, reason):
        best = self.data.get("best_valid_candidate")
        if best and code in RECOVERABLE_REVIEW_STOPS:
            phase = self.data["phase"]
            status = (
                "unavailable"
                if code
                in {
                    "MODEL_PROTOCOL",
                    "TRANSPORT_EXHAUSTED",
                    "CONTEXT_LIMIT",
                    "REVIEW_PROVIDER_UNAVAILABLE",
                }
                else "degraded"
            )
            self.data["review_fallback"] = {
                "phase": phase,
                "reason_code": code,
                "reason": reason[:800],
            }
            self.data["candidate"] = {
                **copy.deepcopy(best["candidate"]),
                "review_status": status,
                "review_reason_code": code,
                "review_phase": phase,
            }
            self.data["tasks"] = copy.deepcopy(best["tasks"])
            return await self._transition(phase, "FINALIZE")
        return await super()._unresolved(code, reason)

    async def _check_sources(self):
        await super()._check_sources()
        candidate = self.data["candidate"]
        if candidate["decision"] == "patch":
            await asyncio.to_thread(
                self.env._git,
                self.env.roots["docs"],
                "apply",
                "--check",
                "-",
                data=candidate["patch"].encode(),
            )

    def _rebuttal_refs(self, refs):
        sources = self.data["evidence"]
        best = self.data["best_valid_candidate"]
        cited = {ref for task in best["tasks"] for ref in task["evidence_ids"]}
        cited.update(self._candidate_evidence_ids(self.data["candidate"]))
        used_ranges = {
            (
                sources[e].get("root"),
                sources[e].get("path"),
                sources[e].get("start_char"),
                sources[e].get("end_char"),
            )
            for e in cited
            if sources[e].get("root")
        }
        if not implementation_sources(sources, refs) or not any(
            sources[ref].get("root") == "docs" for ref in refs
        ):
            raise DomainError("MISSING_EVIDENCE", "Overturning needs implementation and docs")
        independent = False
        for ref in refs:
            item = sources[ref]
            if not item.get("root"):
                continue
            if not any(
                (root, path) == (item["root"], item["path"])
                and start <= item["start_char"]
                and end >= item["end_char"]
                for root, path, start, end in used_ranges
            ):
                independent = True
                break
        if not independent:
            raise DomainError("MISSING_EVIDENCE", "Overturning needs independent counterevidence")
        return refs

    def _compact_rebuttal_refs(self, refs):
        sources = self.data["evidence"]
        code = implementation_sources(sources, refs)
        docs = [ref for ref in refs if sources[ref].get("root") == "docs"]
        essential = [*code[:1], *docs[:1]]
        for task in self.data["tasks"]:
            path = task.get("path")
            matching = [ref for ref in docs if sources[ref]["path"] == path]
            if path and not matching and path not in self.env.tracked["docs"]:
                matching = [ref for ref in docs if self._adjacent(path, sources[ref]["path"])]
            essential.extend(matching[:1])
        return list(dict.fromkeys([*essential, *refs]))[:12]

    async def _review(self, phase, action, step):
        self.data["review"] = action.model_dump()
        candidate = self.data["candidate"]
        if phase == "NECESSITY":
            refs = self._known_refs(action.evidence_ids)
            if action.kind == "no_change":
                reason = self._require_reason(action)
                if best := self.data.get("best_valid_candidate"):
                    try:
                        refs = self._rebuttal_refs(self._known_refs(action.counterevidence_ids))
                        all_paths = set(candidate["changed_paths"]) | set(
                            best["candidate"]["changed_paths"]
                        )
                        docs = {
                            self.data["evidence"][ref]["path"]
                            for ref in refs
                            if self.data["evidence"][ref].get("root") == "docs"
                        }
                        if not self._rebutted_paths(all_paths, docs):
                            raise DomainError("MISSING_EVIDENCE", "Rebut every retained patch path")
                    except DomainError as exc:
                        if exc.code == "MISSING_EVIDENCE" and action.questions:
                            return await self._schedule_research(phase, action, step)
                        raise
                await self._accept_no_change(reason[:1600], self._compact_rebuttal_refs(refs), step)
                if best:
                    self.data["retracted_candidate"] = self.data.pop("best_valid_candidate")
                    self.data["counterevidence_ids"] = refs
                self.data.pop("proposed_abstention", None)
                return await self._transition(phase, "FINALIZE")
            if not refs or not any(self.data["evidence"][ref].get("root") for ref in refs):
                raise DomainError("MISSING_EVIDENCE", "Keeping an edit needs repository evidence")
            if candidate["decision"] == "abstain":
                self.data["previous_candidate"] = self.data.pop("candidate")
                self.data["proposed_abstention"] = None
                self.data["review_followup"] = True
                return await self._transition(phase, "REASON")
            self.data["necessity"] = {
                "patch_hash": candidate["patch_hash"],
                "paths": sorted(candidate["changed_paths"]),
                "evidence_ids": refs,
                "reason": action.reason,
            }
            self.data.pop("proposed_abstention", None)
            return await self._transition(phase, "QUALITY")

        necessity = self.data.get("necessity", {})
        if necessity.get("patch_hash") != candidate["patch_hash"]:
            raise DomainError("CHECKPOINT_INVALID", "Quality requires necessity for this draft")
        if action.kind == "pass":
            candidate["review_status"] = "verified"
            self._checkpoint_candidate(defect_count=0)
            return await self._transition(phase, "FINALIZE")
        if action.kind == "counterevidence":
            refs = self._rebuttal_refs(self._known_refs(action.counterevidence_ids))
            self.data["necessity_reconsideration"] = {"reason": action.reason, "evidence_ids": refs}
            return await self._transition(phase, "NECESSITY")
        return await self._revise(action, step)

    async def _revise(self, action, step):
        candidate = self.data["candidate"]
        approved = set(candidate["changed_paths"])
        questions, defects = [], []
        if not action.defects:
            raise DomainError("INVALID_REVIEW", "Revision needs specific defects")
        for defect in action.defects:
            self._authorized_path(defect.path)
            refs = self._known_refs(defect.evidence_ids)
            if defect.question.strip():
                questions.append(
                    Question(
                        root="docs",
                        question=defect.question.strip()[:400],
                        paths=[defect.path],
                        queries=[defect.question.strip()[:400]],
                    )
                )
            else:
                if defect.path not in approved or not refs:
                    raise DomainError("INVALID_REVIEW", "Cite evidence for an in-scope defect")
                defects.append(defect.model_dump())
        if questions:
            return await self._schedule_research(
                "QUALITY", Reason(kind="research", questions=questions[:4]), step
            )
        paths = sorted({d["path"] for d in defects})
        defect_ids = {
            digest([d["path"], " ".join(d["problem"].casefold().split())]) for d in defects
        }
        self._checkpoint_candidate(defect_count=len(defect_ids))
        signatures = self.data.setdefault("review_revision_signatures", [])
        attempted = set(self.data.get("attempted_defects", []))
        revision = digest([candidate["patch_hash"], paths])
        if revision in signatures or defect_ids <= attempted:
            return await self._unresolved(
                "REVIEW_STALLED", "Revision did not resolve the previously reported defects"
            )
        if self._model_limit() - self.calls < 2:
            return await self._unresolved("VERIFY_RESERVE", "Revision needs a final quality check")
        signatures.append(revision)
        self.data["attempted_defects"] = sorted(attempted | defect_ids)
        self.data["draft_paths"] = paths
        self.data["draft_feedback"] = {"defects": defects}
        self.data["quality_revision"] = True
        return await self._transition("QUALITY", "DRAFT")

    def _context(self, phase, task_prompt):
        context = super()._context(phase, task_prompt)
        sources = self.data["evidence"]
        if phase == "DRAFT":
            defects = (context.get("draft_feedback") or {}).get("defects", [])
            cited = {item["evidence_id"] for item in context["evidence"]}
            extra = {ref for defect in defects for ref in defect["evidence_ids"]} - cited
            context["evidence"].extend(review_evidence(sources, extra))
            implementation_id = self.data["implementation_id"]
            if any(implementation_id in target["evidence_ids"] for target in context["targets"]):
                context["implementation_excerpt"] = self.data["implementation"].get(
                    "diff_excerpt", ""
                )
            return context
        context.pop("remaining_research_rounds", None)
        context["decisions_left_after_this"] = self._model_limit() - self.calls - 1
        if phase == "REASON":
            context["minimum_draft_and_review_decisions"] = VERIFY_RESERVE + 1
            if self.data.get("review_followup"):
                context["previous_review"] = self.data.get("review")
        if phase in REVIEW_PHASES:
            contrary = {
                ref
                for entry in self.data.get("surface_ledger", {}).values()
                for ref in entry["contrary_evidence"]
            }
            reconsideration = self.data.get("necessity_reconsideration")
            if reconsideration and phase == "NECESSITY":
                context["counterevidence_to_check"] = reconsideration
                contrary.update(reconsideration["evidence_ids"])
            context["challenge_evidence"] = [
                *[compact_nonanchored(view, 1200) for view in review_evidence(sources, contrary)],
                *context["challenge_evidence"],
            ]
        if phase == "NECESSITY":
            context.pop("targets", None)
            context.pop("proposed_abstention", None)
            context["surface_ledger"] = {
                path: {k: v for k, v in record.items() if k not in {"last_reason", "status"}}
                for path, record in context["surface_ledger"].items()
            }
            context["candidate"] = {
                k: v for k, v in context["candidate"].items() if k not in {"coverage", "reason"}
            }
        return context
