from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PrepareAction(StrictInput):
    action: Literal["CANCEL_ORDER", "REQUEST_RETURN"]
    order_id: str = Field(min_length=1, max_length=36)
    item_id: str | None = Field(default=None, min_length=1, max_length=36)
    quantity: int | None = Field(default=None, ge=1, le=10000)
    reason: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_action(self):
        if self.action == "REQUEST_RETURN":
            if self.item_id is None or self.quantity is None or not (self.reason or "").strip():
                raise ValueError("Return requires item_id, quantity, and a non-blank reason")
        elif any(x is not None for x in (self.item_id, self.quantity, self.reason)):
            raise ValueError("Cancel does not accept item, quantity, or reason")
        return self


class Decision(StrictInput):
    decision: Literal["approve", "reject"]
    expected_version: int = Field(ge=1)


class DemoLogin(StrictInput):
    account: Literal["alice", "bob"]
    demo_code: str = Field(min_length=1, max_length=256)
