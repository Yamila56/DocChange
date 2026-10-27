"""Owner-scoped, read-only tools. Proposals and execution use dedicated guarded paths."""

import asyncio
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from shopresolve.errors import DomainError
from shopresolve.models import Policy
from shopresolve.service import CommerceService, digest


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    goal_id: str = Field(min_length=1, max_length=36)


class OrderArguments(Arguments):
    order_id: str = Field(min_length=1, max_length=36)


class PolicyArguments(Arguments):
    query: str = Field(min_length=1, max_length=1000)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    schema: type[BaseModel]
    description: str
    effect: str = "read"
    timeout_seconds: int = 10


class ToolRouter:
    def __init__(self, service: CommerceService, retriever=None):
        self.service = service
        self.retriever = retriever
        self.specs = {
            s.name: s
            for s in [
                ToolSpec(
                    "list_orders", Arguments, "List current user's orders; no other user's IDs"
                ),
                ToolSpec("get_order", OrderArguments, "Read one authorized order and its items"),
                ToolSpec(
                    "get_shipment", OrderArguments, "Read order/dispatch state, never invent ETA"
                ),
                ToolSpec(
                    "search_policy", PolicyArguments, "Find current policy text and citations"
                ),
            ]
        }

    def descriptions(self):
        return [
            {
                "name": spec.name,
                "effect": spec.effect,
                "description": spec.description,
                "input_schema": spec.schema.model_json_schema(),
            }
            for spec in self.specs.values()
        ]

    async def execute(self, owner, name, arguments):
        spec = self.specs.get(name)
        if spec is None:
            return self.error("UNKNOWN_TOOL", "Tool is not registered")
        try:
            args = spec.schema.model_validate(arguments)
            async with asyncio.timeout(spec.timeout_seconds):
                if name == "list_orders":
                    data = await self.service.list_orders(owner)
                elif name in {"get_order", "get_shipment"}:
                    data = await self.service.get_order(owner, args.order_id)
                else:
                    async with self.service.sessions() as session:
                        policies = (
                            await session.scalars(select(Policy).where(Policy.active.is_(True)))
                        ).all()
                        if len(policies) != 1:
                            raise DomainError("POLICY_UNAVAILABLE", "No unique active policy")
                        policy = policies[0]
                        rules = {
                            "return_days": policy.return_days,
                            "excluded_categories": policy.excluded_categories,
                            "document": policy.document,
                        }
                        if digest(rules) != policy.content_hash:
                            raise DomainError("POLICY_UNAVAILABLE", "Policy bundle is inconsistent")
                        version, content_hash, document = (
                            policy.version,
                            policy.content_hash,
                            policy.document,
                        )
                    # Release the DB connection before CPU inference; no transaction spans RAG.
                    if self.retriever:
                        if self.retriever.policy_bundle_hash != content_hash:
                            raise DomainError("POLICY_INDEX_STALE", "Policy index bundle mismatch")
                        data = await asyncio.to_thread(
                            self.retriever.search,
                            args.query,
                            policy_version=version,
                            owner_id=owner,
                        )
                    else:
                        data = [
                            {
                                "id": f"{version}:policy",
                                "version": version,
                                "locator": "policy",
                                "quote": document,
                                "content_hash": content_hash,
                            }
                        ]
                return {
                    "status": "ok",
                    "data": data,
                    "error_code": None,
                    "retryable": False,
                    "truncated": False,
                }
        except DomainError as exc:
            return self.error(exc.code, exc.message)
        except ValidationError:
            return self.error("INVALID_ARGUMENTS", "Tool parameters did not match the schema")
        except TimeoutError:
            return self.error("TOOL_TIMEOUT", "Read-only tool exceeded its deadline", True)

    @staticmethod
    def error(code: str, message: str, retryable: bool = False) -> dict[str, Any]:
        return {
            "status": "error",
            "data": None,
            "error_code": code,
            "summary": message,
            "retryable": retryable,
            "truncated": False,
        }
