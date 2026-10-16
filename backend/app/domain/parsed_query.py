from dataclasses import dataclass
from app.domain.enums import Intent, RequestedField
from app.domain.constraints import ConstraintSet, ConstraintOp


@dataclass(frozen=True)
class TraceItem:
    kind: str
    before: str
    after: str


@dataclass(frozen=True)
class NormalizedText:
    original: str
    text: str
    trace: tuple[TraceItem, ...] = ()


@dataclass(frozen=True)
class ParsedQuery:
    original: str
    normalized: NormalizedText
    intent: Intent
    requested_fields: tuple[RequestedField, ...] = ()
    attribute_key: str | None = None
    product_ids: tuple[str, ...] = ()
    entity_terms: tuple[str, ...] = ()
    references_focus: bool = False
    policy_topic: str | None = None
    constraint_ops: tuple[ConstraintOp, ...] = ()
    this_turn_constraints: ConstraintSet = ConstraintSet()
    confidence: float = 1.0
