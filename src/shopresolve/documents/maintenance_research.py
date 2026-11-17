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
        queries = [query for query in queries if 0 < len(query) <= 512]
        if not roots.get(root):
            continue
        authorized = [
            p
            for p in paths
            if valid_path(p)
            and p in tracked[root]
            and (root != "docs" or p.lower().endswith(tuple(EDITABLE_SUFFIXES)))
        ]
        if not authorized and not queries:
            continue
        entry.update(
            paths=authorized,
            queries=queries,
            anchor=entry["anchor"] if authorized else "",
            ignored_paths=[p for p in paths if p not in authorized],
        )
        normalized.append(entry)
    if not normalized:
        raise DomainError("INVALID_RESEARCH", "No available root has a valid query or path")
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
    anchors = {
        (branch["root"], r["path"], r["query"].casefold())
        for record in history
        for branch in record["branches"]
        for r in branch["reads"]
        if r.get("query")
    }
    intervals, totals = {}, {}
    for record in history:
        for branch in record["branches"]:
            for span in branch.get("observed_reads", []):
                locator = branch["root"], span["path"]
                intervals.setdefault(locator, []).append((span["start"], span["end"]))
                if span["total_lines"] is not None:
                    totals[locator] = span["total_lines"]
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
        candidates = [
            ({"path": path, "query": q["anchor"], "limit": 120}, None) for path in q["paths"]
        ] + [
            (
                {"path": hit["path"], "offset": max(0, hit.get("start_line", 1) - 5), "limit": 120},
                (hit["start_line"] - 1, hit.get("end_line", hit["start_line"]))
                if "start_line" in hit
                else None,
            )
            for hit in hits
        ]
        reads = []
        for target, focus in candidates:
            locator = root, target["path"]
            anchor = target.get("query", "").casefold()
            if any(r["path"] == target["path"] for r in reads):
                continue
            if anchor:
                if (*locator, anchor) in anchors:
                    continue
                anchors.add((*locator, anchor))
            else:
                start = target.get("offset", 0)
                end = min(start + target["limit"], totals.get(locator, start + target["limit"]))
                seen = intervals.setdefault(locator, [])
                if end <= start or _covered(seen, *(focus or (start, end))):
                    continue
                seen.append((start, end))  # Reserve planned ranges for this batch only.
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
        q["observed_reads"] = [
            _observed_read(f)
            for f in files
            if f.get("content") and f.get("path") and "start_line" in f
        ]
        q["read_evidence_ids"] = evidence_ids(files)
        q["evidence_ids"] = list(dict.fromkeys([*q["read_evidence_ids"], *q["evidence_ids"]]))
    return branches


def _covered(intervals, start, end):
    for left, right in sorted(intervals):
        if left > start:
            break
        start = max(start, right)
        if start >= end:
            return True
    return False


def _observed_read(file):
    start = file["start_line"] - 1
    total = file.get("total_lines")
    end = file.get("next_offset")
    if end is None:
        end = start + len(file["content"].splitlines())
        if file.get("truncated") and not file["content"].endswith(("\r", "\n")):
            end -= 1
    return {"path": file["path"], "start": start, "end": end, "total_lines": total}
