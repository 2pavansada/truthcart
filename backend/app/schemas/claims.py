from typing import Any, Literal
from pydantic import Field
from app.domain.enums import (
    CardOrigin,
    ClaimScope,
    ClaimType,
    ClarifyReason,
    Decision,
    RefusalReason,
    WarningCode,
)
from app.schemas.common import ContractModel, Metrics
from app.schemas.chat import ConstraintOpIn, ConstraintView
from app.schemas.evidence import Evidence


class Claim(ContractModel):
    claim_id: str
    type: ClaimType
    scope: ClaimScope
    product_id: str | None
    value: dict[str, Any]
    sentence: str
    evidence_ids: list[str]


class CardFact(ContractModel):
    field: Literal["price", "stock"]
    label: str
    value: str | int | float | None
    display: str
    available: bool
    evidence_id: str


class ProductCard(ContractModel):
    product_id: str
    title: str
    title_evidence_id: str
    origin: CardOrigin
    applied_search_id: str | None
    facts: list[CardFact]


class ClarificationOption(ContractModel):
    option_id: str
    kind: Literal["PRODUCT", "CONSTRAINT_OPS", "POLICY_TOPIC"]
    label: str
    product_id: str | None = None
    constraint_ops: list[ConstraintOpIn] = Field(default_factory=list)
    policy_topic: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ClarificationBlock(ContractModel):
    clarification_id: str
    reason: ClarifyReason
    options: list[ClarificationOption]


class RefusalBlock(ContractModel):
    reason: RefusalReason


class Suggestion(ContractModel):
    claim_id: str
    label: str
    constraint_ops: list[ConstraintOpIn]


class ChatResponse(ContractModel):
    session_id: str
    turn_id: int
    decision: Decision
    answer_text: str
    claims: list[Claim]
    evidence: list[Evidence]
    products: list[ProductCard]
    clarification: ClarificationBlock | None
    refusal: RefusalBlock | None
    active_constraints: list[ConstraintView]
    suggestions: list[Suggestion]
    warnings: list[WarningCode]
    metrics: Metrics
