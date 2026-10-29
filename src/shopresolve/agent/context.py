"""Deterministic context selection. Structured business state is never summarized away."""

import copy
import json

from shopresolve.agent.capabilities import manifest
from shopresolve.errors import DomainError
from shopresolve.service import digest


class ContextBuilder:
    def __init__(self, max_utf8_bytes=48000, recent_messages=8):
        self.max_bytes = max_utf8_bytes
        self.recent_messages = recent_messages

    def refresh_summary(self, state):
        earlier = state["messages"][: -self.recent_messages]
        previous = state.get("conversation_summary")
        summary = None
        if earlier:
            text = "\n".join(m["text"][:100] for m in earlier)[-2000:]
            summary = {
                "version": previous.get("version", 0) if previous else 0,
                "method": "extractive-not-authoritative",
                "message_ids": [m["id"] for m in earlier],
                "source_hash": digest(earlier),
                "text": text,
                "text_hash": digest(text),
            }
            if summary != previous:
                summary["version"] += 1
        if summary == previous:
            return False
        state["conversation_summary"] = summary
        return True

    def build(self, state, tool_specs):
        self.refresh_summary(state)
        messages = state["messages"]
        summary = copy.deepcopy(state.get("conversation_summary"))
        context = {
            "capabilities": manifest(),
            "goals": copy.deepcopy(state["goals"]),
            "messages": messages[-self.recent_messages :],
            "history_summary": summary,
            "observations": state["observations"][-8:],
            "evidence": state["evidence"],
            "tools": tool_specs,
            "results": state["results"],
            "last_error": state.get("last_error"),
            "last_error_detail": state.get("last_error_detail"),
        }
        # This is a conservative byte bound, NOT an exact model tokenizer. Actual
        # usage is recorded from provider responses; do not call byte counts tokens.
        if len(json.dumps(context, ensure_ascii=False).encode()) > self.max_bytes:
            context["observations"] = context["observations"][-2:]
            context["history_summary"] = None
        if len(json.dumps(context, ensure_ascii=False).encode()) > self.max_bytes:
            raise DomainError("CONTEXT_BUDGET", "Essential context exceeds the configured limit")
        return context
