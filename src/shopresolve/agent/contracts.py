from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Intent(StrEnum):
    ORDER_QUERY = "ORDER_QUERY"
    SHIPMENT_QUERY = "SHIPMENT_QUERY"
    POLICY_QUERY = "POLICY_QUERY"
    RETURN_REQUEST = "RETURN_REQUEST"
    CANCEL_ORDER = "CANCEL_ORDER"
    REPLACEMENT_SEARCH = "REPLACEMENT_SEARCH"
    COMPARE_PRODUCTS = "COMPARE_PRODUCTS"
    HUMAN_ESCALATION = "HUMAN_ESCALATION"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    AMBIGUOUS = "AMBIGUOUS"


class SlotCandidate(Contract):
    name: str = Field(min_length=1, max_length=64)
    value: str | int | bool | None
    source_message_id: str


class IntentItem(Contract):
    intent: Intent
    mode: Literal["INQUIRE", "REQUEST_ACTION"]
    target_mentions: list[str] = Field(default_factory=list, max_length=10)
    slot_candidates: list[SlotCandidate] = Field(default_factory=list, max_length=20)
    missing_slots: list[str] = Field(default_factory=list, max_length=20)
    ambiguity_reason: str | None = None
    relation_to_goal: Literal["new", "refine", "withdraw", "replace"] = "new"
    existing_goal_id: str | None = None

    @model_validator(mode="after")
    def require_existing_goal(self):
        if self.relation_to_goal != "new" and self.existing_goal_id is None:
            raise ValueError("Goal change requires an existing_goal_id")
        return self


class IntentFrame(Contract):
    schema_version: Literal[1] = 1
    source_message_ids: list[str] = Field(min_length=1, max_length=20)
    items: list[IntentItem] = Field(min_length=1, max_length=8)
    unresolved_references: list[str] = Field(default_factory=list, max_length=20)


class ToolCall(Contract):
    call_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any]


class ToolCalls(Contract):
    kind: Literal["tool_calls"]
    calls: list[ToolCall] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def unique_calls(self):
        if len({c.call_id for c in self.calls}) != len(self.calls):
            raise ValueError("Duplicate tool call IDs in batch")
        return self


class AskUser(Contract):
    kind: Literal["ask_user"]
    question: str = Field(min_length=1, max_length=2000)
    goal_ids: list[str] = Field(min_length=1, max_length=8)
    missing_slots: list[str] = Field(default_factory=list)


class ProposeAction(Contract):
    kind: Literal["propose_action"]
    goal_id: str
    action: Literal["CANCEL_ORDER", "REQUEST_RETURN"]
    order_id: str
    item_id: str | None = None
    quantity: int | None = Field(default=None, ge=1)
    reason: str | None = None


class FinalAnswer(Contract):
    kind: Literal["final_answer"]
    text: str = Field(min_length=1, max_length=8000)
    goal_ids: list[str] = Field(min_length=1, max_length=8)
    evidence_ids: list[str] = Field(default_factory=list, max_length=30)


AgentDecision = Annotated[
    ToolCalls | AskUser | ProposeAction | FinalAnswer,
    Field(discriminator="kind"),
]
decision_adapter = TypeAdapter(AgentDecision)
