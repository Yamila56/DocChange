"""Deterministic views of immutable observations for editing and review."""

import re

from shopresolve.service import digest


def edit_identity(edit):
    return digest(edit)[:24]


def draft_view(candidate):
    """Send exact edit operations without duplicating them in a unified diff."""
    view = {k: v for k, v in candidate.items() if k not in {"patch", "edits"}}
    view["edits"] = [{**edit, "edit_id": edit_identity(edit)} for edit in candidate["edits"]]
    return view


def _span(view, start, end):
    return {
        "start_char": view["start_char"] + start,
        "end_char": view["start_char"] + end,
        "content": view["content"][start:end],
    }


def review_evidence(sources, required):
    fields = (
        "evidence_id",
        "root",
        "path",
        "revision",
        "content_hash",
        "start_char",
        "end_char",
        "content",
    )
    return [
        {**{field: sources[key][field] for field in fields}, "view": "full"}
        for key in sorted(required)
        if sources[key].get("root")
    ]


def anchor_edit_evidence(view, edits):
    if view.get("root") != "docs" or view.get("view") != "full":
        return view
    content = view["content"]
    anchors = sorted(
        (max(0, match.start() - 320), min(len(content), match.end() + 320))
        for edit in edits
        if edit["path"] == view["path"]
        and view["evidence_id"] in edit["evidence_ids"]
        and edit["old"]
        for match in re.finditer(re.escape(edit["old"]), content)
    )
    merged = []
    for start, end in anchors:
        if merged and start <= merged[-1][1]:
            merged[-1] = merged[-1][0], max(end, merged[-1][1])
        else:
            merged.append((start, end))
    if not merged or sum(end - start for start, end in merged) == len(content):
        return view
    return {
        **{k: v for k, v in view.items() if k != "content"},
        "view": "edit_anchored_excerpt",
        "spans": [_span(view, start, end) for start, end in merged],
    }


def compact_nonanchored(view, budget):
    """Keep source IDs and exact endpoint excerpts; mark omission for the reviewer."""
    if view.get("view") != "full" or len(view["content"]) <= budget:
        return view
    compact = {k: v for k, v in view.items() if k != "content"}
    length = len(view["content"])
    half = budget // 2
    return {
        **compact,
        "view": "partial_excerpt",
        "spans": [_span(view, 0, half), _span(view, length - half, length)],
    }
