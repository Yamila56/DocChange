"""Compile the current research intent to bounded, concurrent search/read branches."""

import asyncio

from shopresolve.errors import DomainError
from shopresolve.service import digest

from .service import EDITABLE_SUFFIXES, valid_path


def evidence_ids(items):
    return list(
        dict.fromkeys(
            item["evidence_id"] for item in items if item.get("evidence_id") and item.get("content")
        )
    )


async def gather_tools(calls):
    results = await asyncio.gather(*calls, return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return results


def normalize_questions(questions, tracked, roots):
    normalized = []
    for question in questions:
        entry = question.model_dump()
        root = entry["root"]
        paths = list(dict.fromkeys(entry["paths"]))
        queries = list(dict.fromkeys(" ".join(q.split()) for q in entry["queries"]))
        if not roots.get(root) or (not paths and not queries):
            raise DomainError("INVALID_RESEARCH", "Choose an available root and a query or path")
        if any(not query or len(query) > 512 for query in queries):
            raise DomainError("INVALID_RESEARCH", "Use nonempty queries under 513 characters")
        for path in paths:
            if (
                not valid_path(path)
                or path not in tracked[root]
                or (root == "docs" and not path.lower().endswith(tuple(EDITABLE_SUFFIXES)))
            ):
                raise DomainError(
                    "INVALID_RESEARCH", "Exact read paths must be tracked source files"
                )
        if entry["anchor"] and not paths:
            raise DomainError("INVALID_RESEARCH", "Use an exact anchor only with exact paths")
        entry.update(paths=paths, queries=queries)
        normalized.append(entry)
    return normalized


def intent_signature(questions):
    # Wording of a research question and order of branches do not make a repeated intent new.
    return digest(
        sorted(
            {
                (
                    q["root"],
                    tuple(sorted(query.casefold() for query in q["queries"])),
                    tuple(sorted(q["paths"])),
                    q["anchor"].casefold(),
                )
                for q in questions
            }
        )
    )


async def execute_research(questions, round_number, history, execute):
    visited = {
        (branch["root"], r["path"], r.get("query", "").casefold())
        for record in history
        for branch in record["branches"]
        for r in branch["reads"]
    }
    semaphore = asyncio.Semaphore(3)

    async def tool(key, arguments):
        async with semaphore:
            return await execute(key, arguments)

    branches = [{"id": f"Q{round_number}-{i + 1}", **q} for i, q in enumerate(questions)]
    results = await gather_tools(
        tool(q["id"], {"kind": "search", "root": q["root"], "queries": q["queries"]})
        for q in branches
        if q["queries"]
    )
    results = iter(results)
    for q in branches:
        hits = next(results).get("results", []) if q["queries"] else []
        root = q["root"]
        candidates = [{"path": path, "query": q["anchor"], "limit": 120} for path in q["paths"]] + [
            {"path": hit["path"], "offset": max(0, hit.get("start_line", 1) - 5), "limit": 120}
            for hit in hits
        ]
        reads = []
        for target in candidates:
            visit = (root, target["path"], target.get("query", "").casefold())
            if visit in visited or any(r["path"] == target["path"] for r in reads):
                continue
            visited.add(visit)
            reads.append(target)
            if len(reads) == (3 if root == "docs" else 2):
                break
        q.update(
            reads=reads,
            evidence_ids=evidence_ids(hits),
            ranked_hits=[
                {
                    "rank": rank,
                    **{k: hit[k] for k in ("path", "evidence_id") if k in hit},
                    **({"score": str(hit["score"])} if "score" in hit else {}),
                }
                for rank, hit in enumerate(hits, 1)
            ],
        )
    read_results = iter(
        await gather_tools(
            tool(q["id"], {"kind": "read_many", "root": q["root"], "reads": q["reads"]})
            for q in branches
            if q["reads"]
        )
    )
    for q in branches:
        files = next(read_results).get("files", []) if q["reads"] else []
        q["read_evidence_ids"] = evidence_ids(files)
        q["evidence_ids"] = list(dict.fromkeys([*q["read_evidence_ids"], *q["evidence_ids"]]))
    return branches
