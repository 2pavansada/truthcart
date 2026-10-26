import time
from dataclasses import dataclass, field
from app.domain.constraints import ConstraintSet
from app.domain.enums import ClarifyReason
from app.domain.parsed_query import ParsedQuery
from app.domain.plans import OptionSpec


@dataclass
class PendingClarification:
    clarification_id: str
    reason: ClarifyReason
    options: tuple[OptionSpec, ...]
    parsed: ParsedQuery


@dataclass
class ConversationState:
    session_id: str
    constraints: ConstraintSet = field(default_factory=ConstraintSet)
    focus_product_id: str | None = None
    last_result_ids: tuple[str, ...] = ()
    availability_context: str | None = None
    availability_terms: tuple[str, ...] = ()
    pending: PendingClarification | None = None
    turn_no: int = 0
    updated_at: float = field(default_factory=time.time)
