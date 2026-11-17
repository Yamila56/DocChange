"""Bounded deterministic discovery: DAG layers, tool receipts, no LLM decisions."""

import asyncio
from fnmatch import fnmatchcase

from .maintenance import task_layers
from .service import EDITABLE_SUFFIXES


async def discover(tasks, execute, available_paths, concurrency=3):
    semaphore = asyncio.Semaphore(concurrency)
    by_id = {task["id"]: task for task in tasks}
    completed = {}

    async def one(task):
        async with semaphore:
            observations, paths = [], []
            roots = [("docs", task["queries"]), ("code", task["code_queries"])]
            for root, queries in roots:
                if not queries:
                    continue
                result = await execute(
                    task["id"], {"kind": "search", "root": root, "queries": queries}
                )
                hits = result.get("results", [])
                observations.extend(
                    item for item in hits if item.get("evidence_id") and item.get("content")
                )
                # Rank hits first; hints can add known tracked documentation paths.
                candidates = [
                    (item["path"], max(0, item.get("start_line", 1) - 5)) for item in hits
                ]
                if root == "docs":
                    candidates.extend(
                        (path, 0)
                        for path in available_paths["docs"]
                        if any(fnmatchcase(path, hint) for hint in task["path_hints"])
                        and any(path.lower().endswith(s) for s in EDITABLE_SUFFIXES)
                    )
                selected = {}
                for path, offset in candidates:
                    selected.setdefault(path, offset)
                    if len(selected) == 2:
                        break
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
                    observations.extend(
                        item
                        for item in read.get("files", [])
                        if item.get("evidence_id") and item.get("content")
                    )
                    paths.extend({"root": root, "path": path} for path in selected)
            return task["id"], {
                "status": "evidence_found" if observations else "unresolved",
                "evidence_ids": sorted({item["evidence_id"] for item in observations}),
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
