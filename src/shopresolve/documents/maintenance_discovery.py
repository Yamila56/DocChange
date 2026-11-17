"""Bounded deterministic discovery: DAG layers, tool receipts, no LLM decisions."""

import asyncio
from fnmatch import fnmatchcase
from weakref import WeakValueDictionary

from .maintenance import task_layers
from .service import EDITABLE_SUFFIXES

_INDEX_SLOTS = WeakValueDictionary()


def implementation_index_slots():
    """Bound cold code indexing across runs in one worker process, per event loop."""
    loop = asyncio.get_running_loop()
    slots = _INDEX_SLOTS.get(loop)
    if slots is None:
        slots = asyncio.Semaphore(2)
        _INDEX_SLOTS[loop] = slots
    return slots


def read_targets(hits, paths=(), hints=()):
    ranked = {}
    for hit in hits:
        ranked.setdefault(hit["path"], max(0, hit.get("start_line", 1) - 5))
    # Keep one retrieval slot and reserve the other for the best valid hint.
    # Exact paths beat globs. Within a glob, prefer the highest-ranked match.
    available = sorted(path for path in paths if path.lower().endswith(tuple(EDITABLE_SUFFIXES)))
    known = set(available)
    exact = [hint for hint in hints if hint in known]
    hinted = [
        path
        for hint in hints
        for path in dict.fromkeys([*ranked, *available])
        if path in known and fnmatchcase(path, hint)
    ]
    first = next(iter(ranked), None)
    hint_order = [*exact, *hinted]
    preferred = next(iter(hint_order), None)
    order = list(dict.fromkeys(path for path in (first, preferred, *ranked, *hint_order) if path))
    return {path: ranked.get(path, 0) for path in order[:2]}


def evidence_ids(items):
    return list(
        dict.fromkeys(
            item["evidence_id"] for item in items if item.get("evidence_id") and item.get("content")
        )
    )


async def discover(tasks, execute, available_paths, concurrency=3):
    semaphore = asyncio.Semaphore(concurrency)
    by_id = {task["id"]: task for task in tasks}
    completed = {}

    async def one(task):
        async with semaphore:
            observations, paths, editor_order = [], [], {}
            roots = [("docs", task["queries"]), ("code", task["code_queries"])]
            for root, queries in roots:
                if not queries:
                    continue
                result = await execute(
                    task["id"], {"kind": "search", "root": root, "queries": queries}
                )
                hits = result.get("results", [])
                selected = (
                    read_targets(hits, available_paths["docs"], task["path_hints"])
                    if root == "docs"
                    else read_targets(hits)
                )
                files = []
                if selected:
                    read = await execute(
                        task["id"],
                        {
                            "kind": "read_many",
                            "root": root,
                            "reads": [
                                {"path": path, "offset": offset, "limit": 120}
                                for path, offset in selected.items()
                            ],
                        },
                    )
                    files = read.get("files", [])
                    paths.extend({"root": root, "path": path} for path in selected)
                observations.extend([*hits, *files])
                editor_order[root] = evidence_ids(
                    [*files, *hits] if root == "docs" else [*hits, *files]
                )
            docs, code = editor_order.get("docs", []), editor_order.get("code", [])
            # Retain room for implementation evidence when both roots were searched.
            editor = list(dict.fromkeys([*docs[:3], *code[:1], *docs[3:], *code[1:]]))[:4]
            observed = evidence_ids(observations)
            return task["id"], {
                "status": "evidence_found" if observed else "unresolved",
                "evidence_ids": observed,
                "editor_evidence_ids": editor,
                "candidate_paths": paths,
                "search_complete": True,
                "exhaustive_repository_coverage": False,
            }

    for layer in task_layers(tasks):
        results = await asyncio.gather(*(one(by_id[key]) for key in layer), return_exceptions=True)
        error = next((result for result in results if isinstance(result, BaseException)), None)
        if error:
            raise error
        completed.update(results)
    return completed
