from dataclasses import dataclass, field
from typing import Any, Literal, Mapping
from app.domain.enums import (
    AttrBasis,
    CardOrigin,
    ClaimScope,
    ClaimType,
    ClarifyReason,
    Decision,
    Intent,
    RefusalReason,
    WarningCode,
)
from app.domain.constraints import AttrConstraint, ConstraintSet


@dataclass(frozen=True)
class AttrMatch:
    constraint: AttrConstraint
    basis: AttrBasis
    evidence_field: str  # "attr.<key_norm>" | "title" | "description"
    matched_text: str | None = None


@dataclass(frozen=True)
class Candidate:
    product_id: str
    term_coverage: float = 1.0
    match_location: Literal["TITLE_OR_ATTR", "CATEGORY", "DESCRIPTION_ONLY"] = "TITLE_OR_ATTR"
    lexical_rank: int | None = None
    semantic_rank: int | None = None
    fused_score: float = 1.0
    attr_matches: tuple[AttrMatch, ...] = ()


@dataclass(frozen=True)
class SearchSpec:
    intent: Intent
    constraints: ConstraintSet
    entity_terms: tuple[str, ...] = ()
    top_k: int = 5
    apply_semantic_related: bool = False


@dataclass(frozen=True)
class Relaxation:
    dropped: str
    label: str
    exact_match_count: int


@dataclass(frozen=True)
class PolicySection:
    section_id: str
    ordinal: int
    level: int
    heading: str
    body: str
    topic: str | None = None
    parent_id: str | None = None
    char_start: int = 0
    char_end: int = 0
    content_hash: str = ""


@dataclass(frozen=True)
class PolicyResult:
    manifest: Any
    sections: tuple[PolicySection, ...] = ()
    established: bool = False


@dataclass(frozen=True)
class SearchResult:
    manifest: Any
    exact_ids: tuple[str, ...]
    ranked: tuple[Candidate, ...]
    related: tuple[Candidate, ...] = ()


@dataclass(frozen=True)
class EntityResolution:
    kind: Literal["RESOLVED", "AMBIGUOUS", "NONE", "DESCRIPTION_MENTION_ONLY"]
    candidates: tuple[Candidate, ...] = ()
    manifest: Any = None


@dataclass(frozen=True)
class IdLookupResult:
    found: dict[str, Any]
    missing: list[str]
    manifest: Any


@dataclass(frozen=True)
class ClaimSpec:
    claim_id: str
    type: ClaimType
    scope: ClaimScope
    product_id: str | None
    value: dict[str, Any]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class CardSpec:
    product_id: str
    origin: CardOrigin
    applied_search_id: str | None


@dataclass(frozen=True)
class OptionSpec:
    option_id: str
    kind: Literal["PRODUCT", "CONSTRAINT_OPS", "POLICY_TOPIC"]
    label: str
    product_id: str | None = None
    constraint_ops: tuple[Any, ...] = ()
    policy_topic: str | None = None
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClarificationSpec:
    reason: ClarifyReason
    options: tuple[OptionSpec, ...]


@dataclass(frozen=True)
class SegmentClaim:
    claim_id: str


@dataclass(frozen=True)
class SegmentNF:
    kind: str  # NF-01..NF-14
    params: dict[str, Any] = field(default_factory=dict)


Segment = SegmentClaim | SegmentNF


@dataclass(frozen=True)
class StateUpdate:
    focus_product_id: str | None = None
    last_result_ids: tuple[str, ...] = ()
    pending_clarification: Any = None
    constraints_to_persist: ConstraintSet = ConstraintSet()


@dataclass(frozen=True)
class DecisionPlan:
    decision: Decision
    intent: Intent
    reason: ClarifyReason | RefusalReason | None
    segments: tuple[Segment, ...]
    claim_specs: tuple[ClaimSpec, ...]
    card_specs: tuple[CardSpec, ...]
    clarification: ClarificationSpec | None
    suggestions: tuple[tuple[str, tuple[Any, ...], str], ...]  # (claim_id, ops, label)
    manifests: tuple[Any, ...]
    constraints_after: ConstraintSet
    state_update: StateUpdate
    warnings: tuple[WarningCode, ...] = ()


@dataclass(frozen=True)
class PlanDigest:
    claim_ids: tuple[str, ...]
    types: tuple[ClaimType, ...]
    values: tuple[dict[str, Any], ...]
    evidence_ids: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class Violation:
    rule: str
    claim_id: str | None
    detail: str


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    violations: tuple[Violation, ...] = ()
