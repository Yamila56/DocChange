import pytest

from shopresolve.documents.maintenance_batched_protocol import ResearchQuestion
from shopresolve.documents.maintenance_research import execute_research, normalize_questions
from shopresolve.errors import DomainError


def test_bad_path_hint_does_not_drop_an_available_query():
    bad = ResearchQuestion(question="Guess migration path", root="docs", paths=["guessed.md"])
    query = ResearchQuestion(
        question="Find migration",
        root="docs",
        paths=["../secret.md"],
        queries=["authentication  migration"],
        anchor="old token",
    )
    roots = {"docs": "repo", "code": None}
    tracked = {"docs": {"migration.md"}}
    questions = normalize_questions([bad, query], tracked, roots)
    assert len(questions) == 1
    assert questions[0]["queries"] == ["authentication migration"]
    assert questions[0]["paths"] == [] and not questions[0]["anchor"]
    assert questions[0]["ignored_paths"] == ["../secret.md"]
    with pytest.raises(DomainError, match="No available root"):
        normalize_questions([bad], tracked, roots)


@pytest.mark.asyncio
async def test_reads_skip_observed_ranges_but_allow_new_sections_in_same_file():
    offsets = []
    hits = {"initial": [5], "expanded": [5, 80], "distant": [800]}

    async def tool(_, args):
        if args["kind"] == "search":
            return {
                "results": [
                    {"path": "guide.mdx", "start_line": line, "end_line": line + 3}
                    for line in hits[args["queries"][0]]
                ]
            }
        offset = args["reads"][0]["offset"]
        offsets.append(offset)
        count = 10 if offset == 0 else 120
        return {
            "files": [
                {
                    "path": "guide.mdx",
                    "start_line": offset + 1,
                    "next_offset": offset + count,
                    "total_lines": 1000,
                    "content": "x\n" * count,
                    "truncated": offset == 0,
                }
            ]
        }

    history = []
    for number, query in enumerate(hits, 1):
        branches = await execute_research(
            [{"root": "docs", "paths": [], "anchor": "", "queries": [query]}],
            number,
            history,
            tool,
        )
        history.append({"branches": branches})
    assert offsets == [0, 75, 795]
    assert [r["branches"][0]["observed_reads"][0]["end"] for r in history] == [10, 195, 915]
