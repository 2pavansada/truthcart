from typing import Annotated, Literal, Union
from pydantic import Field
from app.domain.enums import Completeness, Intent
from app.schemas.common import ContractModel
from app.schemas.chat import ConstraintView


class ProductFieldEvidence(ContractModel):
    evidence_id: str
    kind: Literal["PRODUCT_FIELD"] = "PRODUCT_FIELD"
    product_id: str
    field: str
    value: str | int | float | None
    display_value: str | None
    available: bool
    excerpt: str | None = None


class PolicyEvidence(ContractModel):
    evidence_id: str
    kind: Literal["POLICY_SECTION"] = "POLICY_SECTION"
    section_id: str
    heading: str
    excerpt: str


class Relaxation(ContractModel):
    dropped: str
    label: str
    exact_match_count: int


class CatalogueSearchManifest(ContractModel):
    search_id: str
    scope: Literal["CATALOGUE"] = "CATALOGUE"
    intent: Intent
    query_text: str
    lookup_ids: list[str] = Field(default_factory=list)
    applied_constraints: list[ConstraintView] = Field(default_factory=list)
    term_requirements: list[str] = Field(default_factory=list)
    term_min_coverage: float = 0.67
    completeness: Completeness = Completeness.EXHAUSTIVE
    incomplete_reasons: list[str] = Field(default_factory=list)
    eligible_count: int = 0
    exact_match_count: int = 0
    tied_count: int | None = None
    returned_count: int = 0
    excluded_unknown_stock: int = 0
    relaxations: list[Relaxation] = Field(default_factory=list)
    ranking: dict[str, bool] = Field(default_factory=lambda: {"lexical": True, "semantic": False})
    related_count: int = 0


class PolicySearchManifest(ContractModel):
    search_id: str
    scope: Literal["POLICY"] = "POLICY"
    query_terms: list[str] = Field(default_factory=list)
    topic: str | None = None
    sections_checked: list[str] = Field(default_factory=list)
    min_coverage: float = 0.5
    best_coverage: float | None = None
    matched_section_ids: list[str] = Field(default_factory=list)
    completeness: Completeness = Completeness.EXHAUSTIVE
    incomplete_reasons: list[str] = Field(default_factory=list)


class SearchEvidence(ContractModel):
    evidence_id: str
    kind: Literal["SEARCH_MANIFEST"] = "SEARCH_MANIFEST"
    search: CatalogueSearchManifest | PolicySearchManifest


Evidence = Annotated[
    Union[ProductFieldEvidence, PolicyEvidence, SearchEvidence],
    Field(discriminator="kind")
]
