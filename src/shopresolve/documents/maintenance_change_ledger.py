"""Resolve change provenance without substituting unrelated repository observations."""

import re

from shopresolve.errors import DomainError

from .maintenance_beliefs import implementation_sources

_EXPLICIT_BEHAVIOR = re.compile(
    r"\b(?:now|newly|added|removed|changed from|defaults? to|uses?|returns?|accepts?|"
    r"supports?|requires?|must be|must not|no longer|is set to)\b"
    r"|现在|新增|移除|改为|默认值|默认使用|不再|必须|返回|接受",
    re.IGNORECASE,
)
_TOPIC_ONLY = re.compile(
    r"^(?:document|explain|clarify|update|improve|correct)\b[^:\n]*\b(?:when|if)\b",
    re.IGNORECASE,
)


def change_source_types(evidence, refs):
    implementation = set(implementation_sources(evidence, refs))
    return {
        ref: (
            "trusted_change_spec"
            if evidence[ref].get("source_type") == "task_change_description"
            else "code_observation"
            if evidence[ref].get("root") == "code"
            else "implementation_diff"
        )
        for ref in refs
        if ref in implementation or evidence[ref].get("source_type") == "task_change_description"
    }


def _authored_request(task):
    request_marker = "Provided task request:"
    linked_marker = "\n# Frozen pre-existing linked-source context"
    start = task.index(request_marker) + len(request_marker) if request_marker in task else 0
    text = task[start:].split(linked_marker, 1)[0]
    return text.split("\nFor this run, the documentation workspace", 1)[0], start


def documentation_request_span(task, quote):
    """Bind editorial intent separately from evidence of factual behavior."""
    text, start = _authored_request(task)
    quote = quote.strip()
    offset = text.find(quote)
    if offset < 0 or not re.match(
        r"(?:document|explain|clarify|update|improve|correct|add|describe)\s+\S",
        quote,
        re.IGNORECASE,
    ):
        raise DomainError("MISSING_EVIDENCE", "Quote the explicit documentation request verbatim")
    if offset and text[offset - 1] not in "\n.!?: \t":
        raise DomainError("MISSING_EVIDENCE", "Quote the authored documentation request")
    return quote, start + offset


def supplied_change_span(task, quote):
    """Use the authored task body; frozen linked context is supplementary material."""
    text, start = _authored_request(task)
    quote = quote.strip()
    offset = text.find(quote)
    assertion = any(
        re.search(r"\w", quote[: match.start()]) and re.search(r"\w", quote[match.end() :])
        for match in _EXPLICIT_BEHAVIOR.finditer(quote)
    )
    prefix = text[text.rfind("\n", 0, max(offset, 0)) + 1 : max(offset, 0)].rstrip()
    if (
        not quote
        or offset < 0
        or not assertion
        or (prefix and prefix[-1] not in ".!?;:。！？：-*")
        or (_TOPIC_ONLY.search(quote) and ":" not in quote)
        or quote.endswith(("?", "？"))
    ):
        raise DomainError(
            "MISSING_EVIDENCE", "Quote an explicit behavior or contract from the supplied task"
        )
    return quote, start + offset
