from typing import Any, Literal
from app.domain.enums import StockStatus
from app.schemas.common import ContractModel


class ProductDetail(ContractModel):
    product_id: str
    title: str
    description: str | None
    category: str | None
    price_minor: int | None
    currency: str | None
    price_display: str | None
    rating: float | None
    rating_count: int | None
    stock_status: StockStatus
    stock_label: str
    stock_quantity: int | None
    attributes: list[dict[str, Any]]
    unavailable_fields: list[str]
    flags: dict[str, bool]


class PolicySectionDetail(ContractModel):
    section_id: str
    heading: str
    body: str
    parent_id: str | None


class CatalogueMeta(ContractModel):
    product_count: int
    quarantined_count: int
    field_issue_counts: dict[str, int]
    categories: list[dict[str, Any]]
    price_range: dict[str, Any]
    stock_distribution: dict[str, int]
    policy_section_count: int
    built_at: str
    manifest_hash: str
    embedder: str | None = None
    embedding_dim: int | None = None
    semantic_available: bool = False
    lexical_backend: Literal["FTS5", "PY_BM25"] = "FTS5"
