"""Evidence-grounded batched ReAct over the existing repository Harness."""

from shopresolve.errors import DomainError

from .maintenance import NoChangeDecision
from .maintenance_batched_protocol import (
    BATCHED_MODEL_LIMIT,
    BATCHED_PHASE_PREFIX,
    PROTOCOLS,
    RESEARCH_LIMIT,
)
from .maintenance_research import execute_research, intent_signature, normalize_questions
from .maintenance_runtime import CANDIDATE_ERRORS, MaintenanceRuntime


class BatchedResearchRuntime(MaintenanceRuntime):
    initial_phase = "REASON"
    protocol_version = "maintenance-batched-react-v1"
    phase_prefix = BATCHED_PHASE_PREFIX
    uses_batched_control = True

    def _protocol(self, phase):
        return PROTOCOLS[phase]

    def _candidate_evidence_ids(self, candidate):
        return super()._candidate_evidence_ids(candidate) | {
            e for target in self.data["tasks"] for e in target["evidence_ids"]
        }

    def _model_limit(self):
        return min(super()._model_limit(), BATCHED_MODEL_LIMIT)

    async def _transition(self, previous, phase):
        self.state["pending"] = None
        self.data["phase"] = phase
        self.data.get("protocol_feedback", {}).pop(previous, None)
        await self._save()

    async def _advance_batched(self, phase, prompt):
        if phase == "RESEARCH":
            return await self._research()
        if self.calls >= self._model_limit() and not self.state.get("pending"):
            return await self._unresolved("MODEL_BUDGET", "Batched model decision budget exhausted")
        try:
            action, step = await self._decision(phase, prompt)
        except DomainError as exc:
            if exc.code != "MODEL_BUDGET":
                raise
            return await self._unresolved(exc.code, str(exc))
        if action.kind == "unresolved":
            code = "REVIEW_UNRESOLVED" if phase == "VERIFY" else "MODEL_UNRESOLVED"
            return await self._unresolved(code, action.text)
        try:
            if action.kind == "investigate":
                return await self._schedule_research(phase, action, step)
            if phase == "REASON":
                if not self.data.get("research_history"):
                    raise DomainError("MISSING_EVIDENCE", "Investigate before choosing a decision")
                if action.kind == "no_change":
                    await self._no_change(action, step)
                    await self._transition(phase, "VERIFY")
                else:
                    self.data["tasks"] = self._targets(action)
                    if "candidate" in self.data:
                        self.data["previous_candidate"] = self.data.pop("candidate")
                    await self._transition(phase, "EDIT")
            elif phase in {"EDIT", "REPAIR"}:
                await self._propose(action, step, repair=phase == "REPAIR")
                await self._transition(phase, "VERIFY")
            elif phase == "VERIFY":
                self.data["review"] = action.model_dump()
                if action.kind == "pass":
                    await self._transition(phase, "FINALIZE")
                elif self.data["repairs"]:
                    return await self._unresolved(
                        "UNRESOLVED_AFTER_REVIEW", "Local repair rejected"
                    )
                else:
                    self._validate_local_scope(action)
                    self.data["repairs"] = 1
                    await self._transition(phase, "REPAIR")
        except DomainError as exc:
            if exc.code not in CANDIDATE_ERRORS | {"INVALID_RESEARCH", "INVALID_REVIEW"}:
                raise
            if self.data.get("protocol_retries", {}).get(phase, 0):
                return await self._unresolved(exc.code, str(exc))
            await self._protocol_feedback(phase, exc)
        return None

    async def _schedule_research(self, phase, action, step):
        history = self.data.get("research_history", [])
        if len(history) >= RESEARCH_LIMIT:
            return await self._unresolved("RESEARCH_BUDGET", "Research rounds exhausted")
        questions = normalize_questions(action.questions, self.env.tracked, self.env.roots)
        signature = intent_signature(questions)
        if any(r["signature"] == signature for r in history):
            return await self._unresolved("SEARCH_SATURATED", "Research intent already executed")
        if phase == "VERIFY":
            self.data["review"] = action.model_dump()
        self.data["scheduled_research"] = {
            "signature": signature,
            "questions": questions,
            "trigger": phase,
            "prior_evidence_ids": sorted(self.data["evidence"]),
        }
        self.data["plan_step"] = step
        await self._transition(phase, "RESEARCH")
        return None

    async def _research(self):
        history = self.data.setdefault("research_history", [])
        scheduled = self.data["scheduled_research"]
        branches = await execute_research(
            scheduled["questions"], len(history) + 1, history, self._discovery_tool
        )
        prior_ids = set(scheduled["prior_evidence_ids"])
        prior_paths = {
            (self.data["evidence"][e].get("root"), self.data["evidence"][e].get("path"))
            for e in prior_ids
        }
        new_ids = set(self.data["evidence"]) - prior_ids
        new_paths = {
            (self.data["evidence"][e]["root"], self.data["evidence"][e]["path"]) for e in new_ids
        } - prior_paths
        history.append(
            {
                "signature": scheduled["signature"],
                "trigger": scheduled["trigger"],
                "branches": branches,
                "new_evidence_count": len(new_ids),
                "new_path_count": len(new_paths),
            }
        )
        self.data.pop("scheduled_research")
        if len(history) > 1 and not new_ids:
            return await self._unresolved("SEARCH_SATURATED", "Research found no new evidence")
        await self._transition("RESEARCH", "REASON")
        return None

    def _targets(self, action):
        targets = action.targets
        if len({t.id for t in targets}) != len(targets):
            raise DomainError("UNBOUND_EDIT", "Delivery targets need unique IDs")
        if any(set(t.evidence_ids) - self.data["evidence"].keys() for t in targets):
            raise DomainError("UNKNOWN_EVIDENCE", "Delivery targets must cite known sources")
        return [
            {**target.model_dump(), "dependencies": [], "surfaces": ["change"]}
            for target in targets
        ]

    async def _no_change(self, action, step):
        sources = self.data["evidence"]
        if not set(action.evidence_ids) <= sources.keys():
            raise DomainError("UNKNOWN_EVIDENCE", "No-change cites unknown sources")
        docs = [sources[e]["path"] for e in action.evidence_ids if sources[e].get("root") == "docs"]
        if not docs:
            raise DomainError("MISSING_EVIDENCE", "No-change needs observed documentation")
        self.data["tasks"] = [
            {
                "id": "no-change",
                "description": action.text,
                "evidence_ids": action.evidence_ids,
                "surfaces": ["change"],
                "dependencies": [],
            }
        ]
        impact = {
            "kind": "internal_only" if action.impact == "internal_only" else "public_change",
            "evidence_ids": action.evidence_ids,
            "reason": action.text,
        }
        self.data["impact"] = impact
        await self._candidate(
            NoChangeDecision.model_validate(
                {
                    "kind": "no_change",
                    "text": action.text,
                    "edits": [],
                    "impact": impact,
                    "evidence_ids": action.evidence_ids,
                    "coverage": [
                        {
                            "requirement_id": "no-change",
                            "surface": "change",
                            "status": "no_change_supported",
                            "paths": list(dict.fromkeys(docs))[:6],
                            "evidence_ids": action.evidence_ids,
                            "reason": action.text,
                        }
                    ],
                }
            ),
            step,
            repair=False,
        )

    def _scope(self):
        return {(d["requirement_id"], d["path"]) for d in self.data["review"]["defects"]}

    def _validate_local_scope(self, action):
        candidate = self.data["candidate"]
        if candidate["decision"] != "patch":
            raise DomainError(
                "INVALID_REVIEW", "Use investigate or unresolved for a no-change defect"
            )
        ids = {t["id"] for t in self.data["tasks"]}
        paths = self.env.read_paths | set(candidate["changed_paths"])
        if any(d.requirement_id not in ids or d.path not in paths for d in action.defects):
            raise DomainError(
                "INVALID_REVIEW", "Local repair requires a known target and observed path"
            )

    async def _propose(self, action, step, *, repair):
        targets = {t["id"]: t for t in self.data["tasks"]}
        edits = []
        for raw in action.edits:
            edit = raw.model_dump()
            if not edit["requirement_ids"] and len(targets) == 1:
                edit["requirement_ids"] = list(targets)
            if not edit["requirement_ids"] or set(edit["requirement_ids"]) - targets.keys():
                raise DomainError("UNBOUND_EDIT", "Bind each edit to a known delivery target")
            if not edit["evidence_ids"]:
                raise DomainError("MISSING_EVIDENCE", "Each edit needs explicit source citations")
            edits.append(edit)
        if repair:
            scope = self._scope()

            def scoped(edit):
                return all((rid, edit["path"]) in scope for rid in edit["requirement_ids"])

            if any(not scoped(e) for e in edits):
                raise DomainError("REPAIR_SCOPE", "Repair exceeded the permitted target/path scope")
            replacements = {}
            for edit in edits:
                replacements.setdefault(edit["path"], []).append(edit)
            edits = []
            for previous in self.data["candidate"]["edits"]:
                if scoped(previous):
                    edits.extend(replacements.pop(previous["path"], []))
                else:
                    edits.append(previous)
            edits.extend(edit for group in replacements.values() for edit in group)
        coverage = []
        for rid, target in targets.items():
            bound = [e for e in edits if rid in e["requirement_ids"]]
            if not bound:
                raise DomainError("UNBOUND_EDIT", "Every material delivery target needs an edit")
            coverage.append(
                {
                    "requirement_id": rid,
                    "surface": "change",
                    "status": "edit_required",
                    "paths": list(dict.fromkeys(e["path"] for e in bound)),
                    "evidence_ids": list(
                        dict.fromkeys(ref for e in bound for ref in e["evidence_ids"])
                    )[:24],
                    "reason": target["description"],
                }
            )
        if len(edits) > 30:
            raise DomainError("UNBOUND_EDIT", "Merge to at most 30 exact edits per patch")
        sources = self.data["evidence"]
        cited = set()
        for edit in edits:
            refs = edit["evidence_ids"]
            if not set(refs) <= sources.keys():
                raise DomainError("UNKNOWN_EVIDENCE", "Edit cites an unobserved source ID")
            observed = [r for r in refs if sources[r].get("root")]
            if not observed or (
                edit["operation"] != "create"
                and not any(
                    sources[r]["root"] == "docs" and sources[r]["path"] == edit["path"]
                    for r in observed
                )
            ):
                raise DomainError("MISSING_EVIDENCE", "Edit needs observations of its target path")
            cited.update(observed)
        candidate = await self._draft_patch(step, edits, sorted(cited), action.text)
        if "candidate" in self.data:
            self.data["previous_candidate"] = self.data["candidate"]
        self.data["candidate"] = {
            **candidate,
            "edits": edits,
            "coverage": coverage,
            "evidence_ids": sorted(cited),
        }

    def _ranked_evidence(self):
        branches = [
            q for r in reversed(self.data.get("research_history", [])) for q in r["branches"]
        ]
        # Round-robin keeps one broad question from filling the entire context.
        return list(
            dict.fromkeys(
                q["evidence_ids"][i]
                for i in range(max((len(q["evidence_ids"]) for q in branches), default=0))
                for q in branches
                if i < len(q["evidence_ids"])
            )
        )

    def _context(self, phase, task_prompt):
        context = {
            "task": task_prompt,
            "implementation": self.data["implementation"],
            "task_evidence_id": self.data["task_id"],
            "implementation_evidence_id": self.data["implementation_id"],
            "available_roots": [root for root, path in self.env.roots.items() if path],
            "selective_thinking": self.state["config"].get("batched_thinking", False),
            "remaining_research_rounds": RESEARCH_LIMIT
            - len(self.data.get("research_history", [])),
            "targets": self.data["tasks"],
        }
        if feedback := self.data.get("protocol_feedback", {}).get(phase):
            context["protocol_feedback"] = feedback
        if phase == "REASON":
            context["research_history"] = [
                {
                    "trigger": r["trigger"],
                    "new_evidence_count": r["new_evidence_count"],
                    "branches": [
                        {k: q[k] for k in ("id", "question", "root", "queries", "reads")}
                        for q in r["branches"]
                    ],
                }
                for r in self.data.get("research_history", [])
            ]
            if self.data.get("review", {}).get("kind") == "investigate":
                context["review_requested_research"] = self.data["review"]["questions"]
        sources = self.data["evidence"]
        ranked = self._ranked_evidence()

        def excerpts(ids, limit, length):
            return [
                {
                    **sources[e],
                    "content": sources[e]["content"][:length],
                    "excerpt_truncated": len(sources[e]["content"]) > length,
                }
                for e in ids[:limit]
            ]

        if phase in {"VERIFY", "REPAIR"}:
            candidate = self.data["candidate"]
            required = self._candidate_evidence_ids(candidate)
            context["candidate"] = candidate
            context["evidence"] = [sources[e] for e in sorted(required) if sources[e].get("root")]
            others = [e for e in ranked if e not in required]
            if phase == "VERIFY":
                paths = {(sources[e].get("root"), sources[e].get("path")) for e in required}
                distinct = []
                for e in others:
                    locator = (sources[e]["root"], sources[e]["path"])
                    if locator not in paths:
                        distinct.append(e)
                        paths.add(locator)
                context["challenge_evidence"] = excerpts(distinct, 6, 1200)
            else:
                context["scope"] = sorted(self._scope())
                context["defects"] = self.data["review"]["defects"]
                paths = {path for _, path in self._scope()}
                context["challenge_evidence"] = excerpts(
                    [e for e in others if sources[e]["path"] in paths], 4, 1800
                )
        else:
            preferred = [
                ref
                for target in self.data["tasks"]
                for ref in target["evidence_ids"]
                if sources[ref].get("root")
            ]
            context["evidence"] = excerpts(list(dict.fromkeys([*preferred, *ranked])), 16, 2400)
        return context

    def _graph(self):
        graph = super()._graph()
        branches = [q for r in self.data.get("research_history", []) for q in r["branches"]]
        question_ids = {q["id"] for q in branches}
        # In-flight tool observations can predate the completed batch checkpoint.
        question_ids.update(
            rid for e in self.data["evidence"].values() for rid in e.get("retrieved_for", [])
        )
        names = {q["id"]: q["question"] for q in branches}
        graph["nodes"].extend(
            {
                "id": "research:" + q,
                "type": "research_question",
                "description": names.get(q, "Research question"),
            }
            for q in sorted(question_ids)
        )
        for edge in graph["edges"]:
            if edge["type"] == "retrieved_for":
                edge["to"] = edge["to"].replace("requirement:", "research:", 1)
        return graph
