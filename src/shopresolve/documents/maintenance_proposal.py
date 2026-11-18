"""Draft evidence-backed hypotheses, then verify necessity and content separately."""

import asyncio
import copy
import re
from bisect import bisect_right
from itertools import accumulate
from pathlib import PurePosixPath

import httpx

from shopresolve.errors import DomainError
from shopresolve.service import digest

from .maintenance_batched_protocol import RevisionDraft
from .maintenance_change_ledger import (
    change_source_types,
    documentation_request_span,
    supplied_change_span,
)
from .maintenance_evidence import compact_nonanchored, edit_identity, review_evidence
from .maintenance_ledger import REVIEW_PHASES, LedgerMaintenanceRuntime
from .maintenance_ledger_protocol import Question
from .maintenance_proposal_protocol import PROPOSAL_PREFIX, VERIFY_RESERVE, Reason, parse_decision
from .service import EDITABLE_SUFFIXES, apply_edits, valid_path

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
    protocol_version = "maintenance-proposal-v2"
    phase_prefix = PROPOSAL_PREFIX

    def _validate_model_decision(self, phase, step):
        data = self.state["pending"]["response"]
        action, changed = parse_decision(phase, data)
        return self._record_normalization(phase, step, data, action, changed)

    def _candidate_evidence_ids(self, candidate):
        refs = super()._candidate_evidence_ids(candidate)
        refs.update(
            ref
            for target in self.data.get("tasks", [])
            for requirement in target.get("requirements", [])
            for ref in requirement["evidence_ids"]
        )
        refs.update(
            ref
            for requirement in candidate.get("quality", {}).get("requirements", {}).values()
            for ref in requirement["evidence_ids"]
        )
        if candidate["decision"] == "abstain":
            refs.update(self.data.get("counterevidence_ids", []))
        return refs

    def _change_side_refs(self, refs):
        return list(change_source_types(self.data["evidence"], refs))

    def _add_change_fact(self, summary, refs):
        facts = self.data.setdefault("change_ledger", {})
        record = {
            "summary": summary,
            "evidence_ids": refs,
            "source_types": change_source_types(self.data["evidence"], refs),
        }
        for ref, fact in facts.items():
            if fact == record:
                return ref
        ref = f"C{len(facts) + 1}"
        facts[ref] = record
        return ref

    def _seed_change_ledger(self):
        if "change_ledger" in self.data:
            return
        self.data["change_ledger"] = {}
        source_id = self.data["implementation_id"]
        if self._change_side_refs([source_id]):
            paths = ", ".join(self.data["implementation"].get("changed_paths", [])[:4])
            label = "Implementation diff supplied for this task"
            self.data["task_diff_change"] = self._add_change_fact(
                f"{label}: {paths}" if paths else label, [source_id]
            )

    def _active_change(self):
        facts = self.data["change_ledger"]
        task_diff = self.data.get("task_diff_change")
        concrete = [ref for ref in facts if ref != task_diff]
        scoped = self.data.get("research_change_ref")
        if scoped and (scoped != task_diff or not concrete):
            return scoped
        eligible = concrete or list(facts)
        return eligible[0] if len(eligible) == 1 else None

    def _change_refs(self, refs, *, inherit=False):
        known = self.data["change_ledger"]
        selected = list(dict.fromkeys(refs))
        if set(selected) - known.keys():
            raise DomainError("UNKNOWN_EVIDENCE", "Reference only known change IDs from this run")
        if not selected and inherit and (active := self._active_change()):
            selected.append(active)
        evidence = [ref for key in selected for ref in known[key]["evidence_ids"]]
        return selected, list(dict.fromkeys(evidence))

    def _register_changes(self, observations):
        sources = self.data["evidence"]
        for observation in observations:
            refs = self._change_side_refs(self._known_refs(observation.evidence_ids))
            summary = observation.summary.strip()
            if refs and summary:
                self._add_change_fact(summary, refs)
                continue
            if observation.task_quote:
                if self.data["implementation"].get("has_implementation"):
                    # A task excerpt is an optional fallback and does not replace code.
                    continue
                task_id = self.data["task_id"]
                quote, offset = supplied_change_span(
                    sources[task_id]["content"], observation.task_quote
                )
                key = digest(["task_change_description", task_id, offset, quote])[:24]
                sources[key] = {
                    "source_type": "task_change_description",
                    "evidence_id": key,
                    "origin_task_evidence_id": task_id,
                    "start_char": offset,
                    "end_char": offset + len(quote),
                    "content": quote,
                    "content_hash": digest(quote),
                }
                self._add_change_fact(quote, [key])
            else:
                raise DomainError(
                    "MISSING_EVIDENCE", "A change fact needs an observed change source"
                )

    def _research_budget(self, phase):
        remaining = self._model_limit() - self.calls
        if phase in REVIEW_PHASES:
            return "review", max(0, remaining)
        # One follow-up REASON, one DRAFT, and two reviews after a research decision.
        bucket = "review" if self.data.get("review_followup") else "pre_draft"
        return bucket, max(0, remaining - (VERIFY_RESERVE + 1))

    async def _advance_batched(self, phase, prompt):
        self._seed_change_ledger()
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
        if phase == "REASON":
            self._register_changes(action.changes)
            selected, _ = self._change_refs(action.change_refs)
            self.data["research_change_ref"] = selected[0] if len(selected) == 1 else None
        return await super()._schedule_research(phase, action, step)

    async def _repeat_research(self, phase):
        self.data["research_feedback"] = (
            "This exact investigation already ran. Use the current observations to propose, "
            "decide no_change with both sides cited, or ask a materially different question."
        )
        self.data["repeated_research"] = self.data.get("repeated_research", 0) + 1
        await self._transition(phase, phase)

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
            scope_known = any(
                self._path_refs(gap["path"]) for gap in self.data.get("review_scope_gaps", [])
            )
            if (not new_ids and not scope_known) or self._model_limit() - self.calls < (
                VERIFY_RESERVE + 2
            ):
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

    def _resolve_surface_evidence(self, surface, proposal_change_refs):
        sources = self.data["evidence"]
        explicit_docs = self._known_refs(surface.doc_evidence_ids)
        if any(sources[ref].get("root") != "docs" for ref in explicit_docs):
            raise DomainError("MISSING_EVIDENCE", "Documentation citations must come from docs")
        refs = self._known_refs([*explicit_docs, *surface.evidence_ids])
        explicit = surface.change_refs or proposal_change_refs
        selected, change_ids = self._change_refs(explicit, inherit=not self._change_side_refs(refs))
        refs = list(dict.fromkeys([*refs, *change_ids]))
        binding = "explicit" if explicit else "inherited" if selected else "direct"
        return refs, selected, binding

    def _material_requirement(self, path, statement, placement, refs, change_refs=()):
        statement = " ".join(statement.split())
        if not statement:
            raise DomainError("MISSING_EVIDENCE", "A material requirement needs a concrete fact")
        key = "R" + digest([path, statement.casefold()])[:16]
        return key, {
            "id": key,
            "path": path,
            "statement": statement,
            "placement": placement.strip(),
            "evidence_ids": refs,
            "change_refs": list(change_refs),
        }

    def _surface_requirements(self, surface, refs, selected, editorial):
        requirements = {}
        for item in surface.requirements:
            cited = self._known_refs(item.evidence_ids)
            if editorial and not any(self.data["evidence"][e].get("root") == "docs" for e in cited):
                raise DomainError(
                    "MISSING_EVIDENCE", "Editorial facts need explicit repository citations"
                )
            change_refs = self._change_side_refs(cited) or self._change_side_refs(refs)
            key, requirement = self._material_requirement(
                surface.path,
                item.statement,
                item.placement,
                list(dict.fromkeys([*change_refs, *cited])),
                [
                    change
                    for change in selected
                    if set(change_refs) & set(self.data["change_ledger"][change]["evidence_ids"])
                ],
            )
            requirements[key] = requirement
        return list(requirements.values())

    async def _impact(self, phase, action, step):
        # The inherited dispatcher calls this for REASON; no IMPACT phase is entered.
        sources = self.data["evidence"]
        editorial = None
        if (
            action.kind == "propose"
            and action.documentation_request_quote
            and not self.data["implementation"].get("has_implementation")
        ):
            task_id = self.data["task_id"]
            quote, offset = documentation_request_span(
                sources[task_id]["content"], action.documentation_request_quote
            )
            editorial = {"quote": quote, "task_id": task_id, "offset": offset}
        else:
            self._register_changes(action.changes)
        self._change_refs(action.change_refs)
        if action.kind == "no_change":
            _, change_ids = self._change_refs(action.change_refs)
            refs = self._known_refs([*action.evidence_ids, *change_ids])
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
            refs, selected, binding = self._resolve_surface_evidence(surface, action.change_refs)
            docs = [sources[ref]["path"] for ref in refs if sources[ref].get("root") == "docs"]
            change_sides = change_source_types(sources, refs)
            editorial_surface = editorial if not change_sides else None
            if (
                (not change_sides and not (editorial_surface and surface.requirements))
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
                "change_refs": selected,
                "change_source_types": change_sides,
                "change_binding": binding,
                "dependencies": [],
                "surfaces": ["change"],
                "requirements": self._surface_requirements(
                    surface, refs, selected, editorial_surface
                ),
            }
            if editorial_surface:
                approved[path]["documentation_request"] = editorial_surface
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
                "No proposal has scoped change evidence and relevant documentation",
            )
        if len(targets | approved) > 8:
            raise DomainError("UNBOUND_EDIT", "At most eight document paths per candidate")
        gaps = self.data.get("review_scope_gaps", [])
        if gaps:
            existing = set(self.data.get("candidate", {}).get("changed_paths", []))
            approved = {path: target for path, target in approved.items() if path not in existing}
            if not approved:
                return await self._unresolved(
                    "REVIEW_STALLED", "Replanning found no supported additional document"
                )
            for gap in gaps:
                if (target := approved.get(gap["path"])) is None:
                    continue
                key, requirement = self._material_requirement(
                    gap["path"],
                    gap["expected"],
                    "",
                    self._known_refs([*gap["evidence_ids"], *target["evidence_ids"]]),
                    target["change_refs"],
                )
                if all(item["id"] != key for item in target["requirements"]):
                    target["requirements"].append(requirement)
        self.data["tasks"] = list((targets | approved).values())
        self.data["draft_paths"] = sorted(approved)
        if action.reader_goal.strip():
            self.data["reader_goal"] = action.reader_goal.strip()
        self.data["open_questions"] = [f"Check whether {p} is material" for p in unsupported]
        self.data.pop("proposed_abstention", None)
        self.data.pop("quality_revision", None)
        self.data.pop("pending_quality_revision", None)
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
            await self._read_exact_edit_ranges(action, step)
            if pending := self.data.get("pending_quality_revision"):
                await self._write_quality_delta(action, step, pending)
            else:
                await super()._write_draft(action, step)
        except DomainError as exc:
            if exc.code in {"CREATE_CONFLICT", "INVALID_PATCH", "READ_ONLY", "SCOPE_VIOLATION"}:
                raise DomainError("REPAIR_SCOPE", str(exc)) from exc
            if exc.code == "REPOSITORY_IO" and "best_valid_candidate" in self.data:
                raise DomainError("PATCH_MISMATCH", "Revision failed Git validation") from exc
            raise

    async def _read_exact_edit_ranges(self, action, step):
        reads = []
        scope = set(self.data["draft_paths"])
        for edit in action.edits:
            if (
                edit.path not in scope
                or edit.operation != "replace"
                or not edit.old
                or edit.count != 1
            ):
                continue
            self._authorized_path(edit.path)
            text = self.env._file("docs", edit.path).read_text(encoding="utf-8")
            if text.count(edit.old) != 1:
                continue
            try:
                self.env.require_observed(edit, text)
                continue
            except DomainError as exc:
                if exc.code != "UNOBSERVED_EDIT":
                    raise
            offsets = [0, *accumulate(map(len, text.splitlines(keepends=True)))]
            start = text.index(edit.old)
            first = bisect_right(offsets, start) - 1
            last = bisect_right(offsets, start + len(edit.old) - 1) - 1
            while first <= last:
                end = min(first + 120, last + 1)
                cap = bisect_right(offsets, offsets[first] + 14000) - 1
                end = max(first + 1, min(end, cap))
                read = {"path": edit.path, "offset": first, "limit": end - first}
                if read not in reads:
                    reads.append(read)
                first = end
        for start in range(0, len(reads), 4):
            batch = reads[start : start + 4]
            await self._tool(
                step,
                "exact-edit-read:" + digest(batch)[:16],
                {"kind": "read_many", "root": "docs", "reads": batch},
                self.env,
            )

    async def _write_quality_delta(self, action, step, pending):
        previous = self.data["candidate"]["edits"]
        replace = set(action.replace_edit_ids)
        permitted = {
            pending["defects"][key]["id"]: pending["defects"][key] for key in pending["offered"]
        }
        claimed = set(action.defect_ids)
        if not claimed or claimed - permitted.keys():
            raise DomainError("REPAIR_SCOPE", "Name the open defects addressed by this delta")
        paths = {permitted[key]["path"] for key in claimed}
        targets = {t["path"]: t for t in self.data["tasks"]}
        if {edit.path for edit in action.edits} != paths:
            raise DomainError("REPAIR_SCOPE", "Edit exactly the paths of the named defects")
        if replace - {edit_identity(edit) for edit in previous if edit["path"] in paths}:
            raise DomainError("REPAIR_SCOPE", "Replace only existing edits on affected paths")
        edits = []
        for edit in action.edits:
            target = targets[edit.path]
            defect_refs = [
                ref
                for key in claimed
                if permitted[key]["path"] == edit.path
                for ref in permitted[key]["evidence_ids"]
            ]
            refs = list(
                dict.fromkeys(
                    [
                        *defect_refs,
                        *self._path_refs(edit.path),
                        *target["evidence_ids"],
                    ]
                )
            )
            edits.append(
                {
                    **edit.model_dump(),
                    "requirement_ids": [target["id"]],
                    "evidence_ids": refs[:24],
                }
            )
        await self._propose(
            RevisionDraft(
                kind="revise",
                edits=edits,
                replace_edit_ids=sorted(replace),
                text="Documentation update",
            ),
            step,
            phase="REVISE",
        )
        pending["claimed"] = sorted(claimed)
        self.data.pop("draft_feedback", None)
        self.data.pop("reread_evidence_ids", None)

    async def _after_draft(self):
        approved = self.data.get("necessity", {})
        candidate = self.data["candidate"]
        candidate["change_provenance"] = [
            {
                "path": target["path"],
                "change_refs": target["change_refs"],
                "source_types": target["change_source_types"],
                "binding": target["change_binding"],
                **(
                    {"documentation_request": target["documentation_request"]}
                    if "documentation_request" in target
                    else {}
                ),
            }
            for target in self.data["tasks"]
        ]
        if any(
            "trusted_change_spec" in entry["source_types"].values()
            for entry in candidate["change_provenance"]
        ):
            candidate["change_source_note"] = (
                "Implementation unavailable; grounded in the supplied change specification."
            )
        for target in self.data["tasks"]:
            path = target["path"]
            self._record_surface(path, "edit", target["description"], target["evidence_ids"])
        if "first_draft" not in self.data:
            self.data["first_draft"] = {
                key: candidate[key] for key in ("patch", "patch_hash", "changed_paths")
            }
        if gaps := self.data.pop("review_scope_gaps", []):
            self.data["scope_expansion"] = {
                "patch_hash": candidate["patch_hash"],
                "reviewed_gaps": gaps,
            }
        if "best_valid_candidate" not in self.data:
            self._checkpoint_candidate()
        if self.data.pop("quality_revision", False):
            if approved.get("paths") != sorted(candidate["changed_paths"]):
                # The delta guard should make scope expansion impossible.
                raise DomainError("CHECKPOINT_INVALID", "Revision changed the necessity scope")
            approved["patch_hash"] = candidate["patch_hash"]
            return await self._transition("DRAFT", "QUALITY")
        return await super()._after_draft()

    def _checkpoint_candidate(self, *, defect_count=None, validated_improvement=False):
        candidate = self.data["candidate"]
        prior = self.data.get("best_valid_candidate")
        if prior and prior["candidate"]["patch_hash"] != candidate["patch_hash"]:
            prior_count = prior["defect_count"]
            same_scope = prior["candidate"]["changed_paths"] == candidate["changed_paths"]
            if validated_improvement and not same_scope:
                # A widened draft keeps its older fallback until its full scope passes.
                if self.data.get("scope_expansion"):
                    return
                raise DomainError("INVALID_REVIEW", "A quality delta must preserve approved paths")
            if (
                not validated_improvement
                and defect_count != 0
                and (
                    not same_scope
                    or prior_count is None
                    or defect_count is None
                    or defect_count >= prior_count
                )
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
                "review_incomplete": True,
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
        if not self._change_side_refs(refs) or not any(
            sources[ref].get("root") == "docs" for ref in refs
        ):
            raise DomainError("MISSING_EVIDENCE", "Overturning needs change-side and docs evidence")
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
        code = self._change_side_refs(refs)
        docs = [ref for ref in refs if sources[ref].get("root") == "docs"]
        essential = [*code[:1], *docs[:1]]
        for task in self.data["tasks"]:
            path = task.get("path")
            matching = [ref for ref in docs if sources[ref]["path"] == path]
            if path and not matching and path not in self.env.tracked["docs"]:
                matching = [ref for ref in docs if self._adjacent(path, sources[ref]["path"])]
            essential.extend(matching[:1])
        return list(dict.fromkeys([*essential, *refs]))[:12]

    def _quality_requirements(self):
        known = {
            r["id"]: r for target in self.data["tasks"] for r in target.get("requirements", [])
        }
        pending = self.data.get("pending_quality_revision")
        if pending:
            known.update(pending["requirements"])
        if self.data.get("scope_expansion"):
            prior = self.data.get("best_valid_candidate", {}).get("candidate", {})
            known.update(prior.get("quality", {}).get("requirements", {}))
        known.update(self.data.get("candidate", {}).get("quality", {}).get("requirements", {}))
        return known

    def _final_review_text(self, candidate):
        edits = candidate["edits"]
        base = {
            path: {"content": self.env._file("docs", path).read_text(encoding="utf-8")}
            for path in {edit["path"] for edit in edits if edit["operation"] != "create"}
        }
        return apply_edits(base, edits, self.env.read_paths)

    def _literal_evidence(self, requirements, edits):
        sources = self.data["evidence"]
        results = []
        for requirement in requirements:
            path = requirement["path"]
            for literal in dict.fromkeys(
                re.findall(r"`([^`\n]{1,100})`", requirement["statement"])
            ):
                for ref in requirement["evidence_ids"]:
                    source = sources[ref]
                    if not self._change_side_refs([ref]):
                        continue
                    content = source["content"]
                    if isinstance(content, dict):
                        content = content.get("diff_excerpt", "")
                    start = content.find(literal)
                    if start < 0:
                        continue
                    results.append(
                        {
                            "path": path,
                            "literal": literal,
                            "evidence_id": ref,
                            "source_excerpt": content[
                                max(0, start - 60) : start + len(literal) + 60
                            ],
                            "in_replacement": any(
                                edit["path"] == path and literal in edit["new"] for edit in edits
                            ),
                        }
                    )
                    break
                if len(results) == 8:
                    return results
        return results

    def _assess_quality(self, action, candidate, requirements):
        pending = self.data.get("pending_quality_revision") or {}
        defects = dict(pending.get("defects", {}))
        sources = self.data["evidence"]
        targets = {t["path"]: t for t in self.data["tasks"]}
        reported = set()
        for defect in action.defects:
            key = defect.requirement_id
            target = targets.get(defect.path, {})
            if defect.expected and (not key or key == target.get("id")):
                explicit = self._known_refs(defect.evidence_ids)
                scoped = target.get("evidence_ids", [])
                docs_refs = [
                    ref
                    for ref in dict.fromkeys([*explicit, *scoped])
                    if sources[ref].get("root") == "docs"
                    and (sources[ref]["path"] == defect.path or ref in scoped)
                ]
                change_refs = self._change_side_refs(explicit) or self._change_side_refs(scoped)
                if (
                    not explicit
                    or not docs_refs
                    or not (change_refs or target.get("documentation_request"))
                ):
                    raise DomainError(
                        "INVALID_REVIEW", "New requirements need scoped change and docs evidence"
                    )
                refs = list(dict.fromkeys([*explicit, *change_refs, *docs_refs]))
                key, requirement = self._material_requirement(
                    defect.path, defect.expected, "", refs
                )
                requirements.setdefault(key, requirement)
            requirement = requirements.get(key)
            if not requirement or requirement["path"] != defect.path:
                raise DomainError(
                    "INVALID_REVIEW", "A defect must identify a requirement on its path"
                )
            if defect.path not in candidate["changed_paths"]:
                raise DomainError(
                    "INVALID_REVIEW", "Research a new path before proposing edits there"
                )
            refs = list(
                dict.fromkeys(
                    [*self._known_refs(defect.evidence_ids), *requirement["evidence_ids"]]
                )
            )
            defects[key] = {
                **defect.model_dump(),
                "id": "D" + key[1:],
                "requirement_id": key,
                "evidence_ids": refs,
            }
            reported.add(key)
        covered = {}
        postimage = self._final_review_text(candidate) if action.coverage else {}
        for item in action.coverage:
            key = item.requirement_id
            if key not in requirements:
                raise DomainError("INVALID_REVIEW", "Coverage names an unknown requirement")
            # A quote can support part of a requirement while a defect leaves it open.
            if key in reported or key in covered:
                continue
            path = requirements[key]["path"]
            quote = item.quote.strip()
            present = quote in postimage.get(path, {}).get("content", "")
            original = item.absent and any(
                source.get("root") == "docs"
                and source.get("path") == path
                and quote in source.get("content", "")
                for source in sources.values()
            )
            if (
                not quote
                or (item.absent and (present or not original))
                or (not item.absent and not present)
            ):
                continue
            covered[key] = {"quote": quote, "absent": item.absent}
            defects.pop(key, None)
        if action.kind == "pass" and (set(requirements) != covered.keys() or defects):
            raise DomainError(
                "INVALID_REVIEW", "Pass requires a final-text quote for every requirement"
            )
        return {"requirements": requirements, "covered": covered, "defects": defects}

    async def _review_requirements(self, action, step, candidate, requirements):
        assessment = await asyncio.to_thread(self._assess_quality, action, candidate, requirements)
        candidate["quality"] = assessment
        pending = self.data.get("pending_quality_revision")
        if pending:
            closed = {
                key
                for key, defect in pending["defects"].items()
                if defect["id"] in pending["claimed"] and key in assessment["covered"]
            }
            if (
                not closed
                or not pending["covered"].keys() <= assessment["covered"].keys()
                or pending["patch_hash"] == candidate["patch_hash"]
            ):
                return await self._unresolved(
                    "REVIEW_STALLED",
                    "Delta did not close a named defect with prior coverage intact",
                )
            self.data.pop("pending_quality_revision")
        count = len(assessment["requirements"]) - len(assessment["covered"])
        self._checkpoint_candidate(defect_count=count, validated_improvement=bool(pending))
        if action.kind == "pass":
            candidate["review_status"] = "verified"
            self._checkpoint_candidate(defect_count=0)
            return await self._transition("QUALITY", "FINALIZE")
        actionable = assessment["defects"]
        if not actionable:
            raise DomainError("INVALID_REVIEW", "Revision needs a specific open defect")
        if self._model_limit() - self.calls < 2:
            return await self._unresolved("VERIFY_RESERVE", "Revision needs a final quality check")
        attempted = set(self.data.get("attempted_defects", []))
        defects = {
            key: defect for key, defect in actionable.items() if defect["id"] not in attempted
        }
        if not defects:
            return await self._unresolved("REVIEW_STALLED", "No new actionable defects")
        paths = sorted({d["path"] for d in defects.values()})
        self.data.setdefault("review_revision_signatures", []).append(
            digest([candidate["patch_hash"], sorted(defects)])
        )
        self.data["attempted_defects"] = sorted(attempted | {d["id"] for d in defects.values()})
        self.data["draft_paths"] = paths
        self.data["draft_feedback"] = {"defects": list(defects.values())}
        self.data["pending_quality_revision"] = {
            **assessment,
            "patch_hash": candidate["patch_hash"],
            "offered": sorted(defects),
        }
        self.data["quality_revision"] = True
        return await self._transition("QUALITY", "DRAFT")

    async def _review(self, phase, action, step):
        self.data["review"] = action.model_dump()
        candidate = self.data["candidate"]
        if phase == "NECESSITY":
            _, change_ids = self._change_refs(action.change_refs)
            refs = self._known_refs([*action.evidence_ids, *change_ids])
            if action.kind == "no_change":
                reason = self._require_reason(action)
                if best := self.data.get("best_valid_candidate"):
                    try:
                        refs = self._rebuttal_refs(
                            self._known_refs([*action.counterevidence_ids, *change_ids])
                        )
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
                self.data["candidate"]["change_source_types"] = change_source_types(
                    self.data["evidence"], refs
                )
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
        if action.kind in {"revise", "pass"}:
            requirements = self._quality_requirements()
            additional = [
                d
                for d in action.defects
                if d.expected
                and d.path not in candidate["changed_paths"]
                and d.path in self.env.tracked["docs"]
            ]
            if additional:
                for defect in additional:
                    self._authorized_path(defect.path)
                    self._known_refs(defect.evidence_ids)
                if self._model_limit() - self.calls < VERIFY_RESERVE + 2:
                    return await self._unresolved(
                        "VERIFY_RESERVE", "Additional document needs replanning and verification"
                    )
                self.data["review_scope_gaps"] = [defect.model_dump() for defect in additional]
                if all(self._path_refs(defect.path) for defect in additional):
                    self.data["review_followup"] = True
                    return await self._transition("QUALITY", "REASON")
                questions = [
                    Question(root="docs", paths=[d.path], question=d.expected[:400])
                    for d in additional[:4]
                ]
                return await self._schedule_research(
                    "QUALITY", Reason(kind="research", questions=questions), step
                )
            has_requirements = (
                requirements
                or action.coverage
                or any(defect.requirement_id or defect.expected for defect in action.defects)
            )
            if has_requirements and not any(defect.question.strip() for defect in action.defects):
                return await self._review_requirements(action, step, candidate, requirements)
        if action.kind == "pass":
            candidate.update(
                review_status="degraded",
                review_incomplete=True,
                review_reason_code="REQUIREMENTS_UNAVAILABLE",
            )
            self._checkpoint_candidate(defect_count=0)
            return await self._transition(phase, "FINALIZE")
        if action.kind == "counterevidence":
            _, change_ids = self._change_refs(action.change_refs)
            refs = self._rebuttal_refs(self._known_refs([*action.counterevidence_ids, *change_ids]))
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
            required = {
                ref
                for target in context["targets"]
                for requirement in target.get("requirements", [])
                for ref in requirement["evidence_ids"]
            }
            extra = (
                required | {ref for defect in defects for ref in defect["evidence_ids"]}
            ) - cited
            context["evidence"].extend(review_evidence(sources, extra))
            context["task"] = task_prompt
            context["reader_goal"] = self.data.get("reader_goal", "")
            context["prior_edits"] = [
                {**edit, "edit_id": edit_identity(edit)} for edit in context["prior_edits"]
            ]
            implementation_id = self.data["implementation_id"]
            if implementation_id in required or any(
                implementation_id in target["evidence_ids"] for target in context["targets"]
            ):
                context["implementation_excerpt"] = self.data["implementation"].get(
                    "diff_excerpt", ""
                )
            target_refs = (
                required
                | extra
                | {ref for target in context["targets"] for ref in target["evidence_ids"]}
            )
            context["supplied_change_specs"] = [
                sources[ref]
                for ref in sorted(target_refs)
                if sources[ref]["source_type"] == "task_change_description"
            ]
            return context
        context.pop("remaining_research_rounds", None)
        context["change_ledger"] = self.data["change_ledger"]
        context["active_change_ref"] = self._active_change()
        context["decisions_left_after_this"] = self._model_limit() - self.calls - 1
        if phase == "REASON":
            context["minimum_draft_and_review_decisions"] = VERIFY_RESERVE + 1
            if self.data.get("review_followup"):
                context["previous_review"] = self.data.get("review")
            if gaps := self.data.get("review_scope_gaps"):
                context["review_scope_gaps"] = gaps
                context["retained_draft_paths"] = self.data["candidate"]["changed_paths"]
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
        if phase == "QUALITY":
            context.pop("targets", None)
            draft = context["candidate"]
            for key in ("coverage", "quality", "evidence_ids", "change_provenance"):
                draft.pop(key, None)
            for edit in draft["edits"]:
                edit.pop("requirement_ids", None)
            context["requirements"] = list(self._quality_requirements().values())
            context["reader_goal"] = self.data.get("reader_goal", "")
            if literals := self._literal_evidence(context["requirements"], draft["edits"]):
                context["literal_evidence"] = literals
            if scope := self.data.get("scope_expansion"):
                context["previous_scope_findings"] = scope["reviewed_gaps"]
            pending = self.data.get("pending_quality_revision")
            if pending:
                context["previous_quality"] = {
                    "covered": pending["covered"],
                    "defects": list(pending["defects"].values()),
                    "claimed_defect_ids": pending["claimed"],
                }
        return context

    def _graph(self):
        graph = super()._graph()
        for ref, fact in self.data.get("change_ledger", {}).items():
            node = f"change:{ref}"
            graph["nodes"].append({"id": node, "type": "change", **fact})
            graph["edges"].extend(
                {"from": "evidence:" + key, "to": node, "type": "supports"}
                for key in fact["evidence_ids"]
            )
        for target in self.data["tasks"]:
            graph["edges"].extend(
                {"from": f"change:{ref}", "to": "requirement:" + target["id"], "type": "motivates"}
                for ref in target.get("change_refs", [])
            )
        return graph
