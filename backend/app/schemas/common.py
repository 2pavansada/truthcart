from typing import Any
from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorBody(ContractModel):
    code: str
    message: str
    details: list[dict[str, Any]] | None = None
    request_id: str


class ErrorEnvelope(ContractModel):
    error: ErrorBody


class Metrics(ContractModel):
    request_id: str
    total_ms: float
    stages: dict[str, float]
    tokens: dict[str, Any]
    validator_trips: int
