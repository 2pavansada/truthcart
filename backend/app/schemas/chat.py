from typing import Any, Literal
from pydantic import Field
from app.schemas.common import ContractModel


class ConstraintOpIn(ContractModel):
    op: Literal["SET", "ADD", "REMOVE", "RESET"]
    key: Literal["category", "price_min", "price_max", "stock", "rating_min", "attribute", "term", "sort", "all"]
    value: Any | None = None


class ConstraintView(ContractModel):
    key: str
    label: str
    op_to_remove: ConstraintOpIn


class ClientContext(ContractModel):
    focus_product_id: str | None = None


class Resolution(ContractModel):
    clarification_id: str
    option_id: str


class ChatOptions(ContractModel):
    max_results: int = Field(default=5, ge=1, le=10)


class ChatRequest(ContractModel):
    session_id: str | None = None
    message: str = ""
    client_context: ClientContext = Field(default_factory=ClientContext)
    constraint_ops: list[ConstraintOpIn] = Field(default_factory=list)
    resolution: Resolution | None = None
    options: ChatOptions = Field(default_factory=ChatOptions)


class ResetRequest(ContractModel):
    session_id: str


class ResetResponse(ContractModel):
    session_id: str
    reset: bool


class HealthResponse(ContractModel):
    status: Literal["ok"]


class ReadinessReport(ContractModel):
    ready: bool
    checks: dict[str, bool]
    degraded: list[str]
