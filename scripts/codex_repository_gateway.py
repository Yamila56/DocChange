"""Tool-free Codex CLI model adapter for the existing repository harness.

Only the model is changed. The child sees supplied public task observations,
has its tools disabled, and returns a RepositoryDecision; our harness still
owns every file operation and all durable state.
"""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from scripts.score_dogbench import readonly_judge
from shopresolve.agent.gateway import ModelReply
from shopresolve.documents.repository import RepositoryDecision, RepositoryGateway


class SubscriptionRecordedGateway:
    def __init__(self, inner, path):
        self.inner, self.path = inner, path

    async def request(self, phase, context):
        record = {
            "configured_model": self.inner.settings.model,
            "billing": "existing Codex subscription; monetary cost not estimated",
        }
        try:
            reply = await self.inner.request(phase, context)
            record.update(output=reply.output, usage=reply.usage)
            return reply
        except Exception as exc:
            record["error_type"] = type(exc).__name__
            raise
        finally:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")


class CodexRepositoryGateway(RepositoryGateway):
    async def request(self, phase, context):
        payload = self.build_payload(phase, context)
        directory = Path(".cache/codex-model-calls") / str(uuid4())
        directory.mkdir(parents=True)
        root = Path(directory).resolve()
        prompt, schema, output, log = [
            root / name for name in ("prompt.txt", "schema.json", "result.json", "events.jsonl")
        ]
        prompt.write_text(
            "Act only as a JSON decision model. Use only the supplied observations. "
            "Do not call tools or read files. The parent harness executes decisions.\n"
            + json.dumps(payload["messages"], ensure_ascii=False),
            encoding="utf-8",
        )
        definition = RepositoryDecision.model_json_schema()

        def strict(value):
            if isinstance(value, dict):
                value.pop("default", None)
                if "properties" in value:
                    value["required"] = list(value["properties"])
                    value["additionalProperties"] = False
                for child in value.values():
                    strict(child)
            elif isinstance(value, list):
                for child in value:
                    strict(child)

        strict(definition)
        schema.write_text(json.dumps(definition), encoding="utf-8")
        await asyncio.to_thread(readonly_judge, prompt, schema, output, log, self.settings.model)
        result = json.loads(output.read_text(encoding="utf-8"))
        RepositoryDecision.model_validate(result)
        return ModelReply(result, None, self.settings.model, None)
