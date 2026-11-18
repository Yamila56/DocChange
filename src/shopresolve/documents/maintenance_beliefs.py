"""Persistent, cited semantic assessments; repository observations remain authoritative."""

from shopresolve.errors import DomainError
from shopresolve.service import digest

from .service import EDITABLE_SUFFIXES, valid_path


def implementation_sources(sources, refs):
    result = []
    for ref in refs:
        source = sources.get(ref, {})
        summary = source.get("content", {})
        if source.get("root") == "code" or (
            source.get("source_type") == "implementation"
            and isinstance(summary, dict)
            and summary.get("has_implementation")
            and bool(summary.get("diff_excerpt", "").strip())
        ):
            result.append(ref)
    return result


def new_belief(orientation, step):
    return {
        "version": 1,
        "semantic_changes": orientation.semantic_changes,
        "potential_contracts": orientation.potential_contracts,
        "open_questions": orientation.uncertainties,
        "claims": {},
        "surfaces": {},
        "target_ids": {},
        "next_target_number": 1,
        "updates": [{"step": step, "kind": "orientation"}],
        "semantic_status_source": "Model assessment with runtime-validated provenance",
    }


def update_belief(belief, action, sources, step):
    # Validate the full delta before mutating a persistent working state.
    for claim in action.claims:
        refs = claim.evidence_ids + claim.contradicting_evidence_ids
        if set(refs) - sources.keys():
            raise DomainError("UNKNOWN_EVIDENCE", "Belief cites unknown observations")
        if claim.status in {"confirmed", "contradicted"} and not any(
            sources[ref].get("root") or implementation_sources(sources, [ref]) for ref in refs
        ):
            raise DomainError("MISSING_EVIDENCE", "Confirmed beliefs need repository observations")
    for surface in action.surfaces:
        refs = surface.implementation_evidence_ids + surface.documentation_evidence_ids
        if set(refs) - sources.keys():
            raise DomainError("UNKNOWN_EVIDENCE", "Surface cites unknown observations")
        if not valid_path(surface.path) or not surface.path.lower().endswith(
            tuple(EDITABLE_SUFFIXES)
        ):
            raise DomainError("INVALID_PATH", "Belief surfaces must be relative document paths")
    for claim in action.claims:
        key = "B" + digest(claim.statement)[:16]
        belief["claims"][key] = {**claim.model_dump(), "id": key, "step": step}
    for surface in action.surfaces:
        key = digest([surface.path, surface.changed_contract])[:24]
        material = (
            surface.material or belief["surfaces"].get(key, {}).get("status") == "confirmed_edit"
        )
        belief["surfaces"][key] = {
            **surface.model_dump(),
            "material": material,
            "key": key,
            "step": step,
        }
    belief["version"] += 1
    belief["open_questions"] = action.open_questions
    belief["updates"].append({"step": step, "kind": "belief_update", "reason": action.reason})


def impact_targets(belief, sources):
    targets, no_change_refs, gaps = [], [], []
    for surface in belief["surfaces"].values():
        status = surface["status"]
        if status in {"unknown", "suspected"}:
            if surface["material"]:
                gaps.append(surface)
            continue
        if status == "dismissed" and not surface["material"]:
            continue
        docs = [
            ref
            for ref in surface["documentation_evidence_ids"]
            if sources[ref].get("root") == "docs"
        ]
        impl = implementation_sources(sources, surface["implementation_evidence_ids"])
        necessary = status == "confirmed_edit"
        path_seen = any(sources[ref]["path"] == surface["path"] for ref in docs)
        valid = bool(
            docs and impl and surface["current_documented_claim"] and surface["user_impact"]
        )
        if necessary:
            valid = valid and (path_seen or surface["gap"] == "missing_required_guidance")
            valid = valid and surface["gap"] not in {"none", "unknown"}
            valid = valid and surface["maintenance_action"] in {
                "edit_document",
                "update_committed_output",
            }
            if surface["ownership"].startswith("generated"):
                valid = valid and surface["maintenance_action"] == "update_committed_output"
        else:
            valid = valid and path_seen and surface["gap"] == "none"
        if not valid:
            gaps.append(surface)
            continue
        refs = list(dict.fromkeys([*impl, *docs]))
        if necessary:
            key = surface["key"]
            if key not in belief["target_ids"]:
                belief["target_ids"][key] = f"T{belief['next_target_number']:03d}"
                belief["next_target_number"] += 1
            targets.append(
                {
                    "id": belief["target_ids"][key],
                    "path": surface["path"],
                    "belief_key": key,
                    "description": (surface["changed_contract"] + ". " + surface["user_impact"])[
                        :600
                    ],
                    "evidence_ids": refs,
                    "dependencies": [],
                    "surfaces": ["change"],
                }
            )
        else:
            no_change_refs.extend(refs)
    if len(targets) > 8:
        raise DomainError(
            "UNBOUND_EDIT", "Split more than eight material targets into separate tasks"
        )
    return targets, list(dict.fromkeys(no_change_refs)), gaps
