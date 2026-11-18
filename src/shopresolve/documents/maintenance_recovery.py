"""Durable retry accounting and deterministic read recovery for the repository policy."""

from shopresolve.errors import DomainError
from shopresolve.service import digest

from .service import valid_path

RECOVERABLE_EDITS = {
    "PATCH_MISMATCH",
    "UNOBSERVED_EDIT",
    "MISSING_EVIDENCE",
    "READ_FIRST",
    "REPAIR_SCOPE",
    "UNBOUND_EDIT",
    "EMPTY_PATCH",
    "UNKNOWN_EVIDENCE",
}


def reserve_recovery(data, phase, kind, *, limit=1):
    recovery = data.setdefault("recovery", {"counts": {}, "events": []})
    key = phase + ":" + kind
    used = recovery["counts"].get(key, 0)
    if used >= limit:
        return False
    recovery["counts"][key] = used + 1
    recovery["events"].append({"phase": phase, "kind": kind, "attempt": used + 1})
    return True


def target_reads(edits, targets, tracked):
    allowed = {target["path"] for target in targets}
    reads = []
    for edit in edits:
        path = edit.path
        if path not in allowed or not valid_path(path):
            raise DomainError("REPAIR_SCOPE", "Recovery cannot grant a new edit target")
        if path not in tracked:
            continue
        anchor = next((line.strip() for line in edit.old.splitlines() if line.strip()), "")[:240]
        read = {"path": path, "query": anchor, "limit": 120}
        if read not in reads:
            reads.append(read)
    return reads


def recovery_identity(reads):
    return digest(reads)[:16]
