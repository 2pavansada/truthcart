from dataclasses import dataclass, field
from typing import Any, Literal
from app.domain.enums import OpKind, StockStatus


@dataclass(frozen=True)
class PriceBound:
    amount_minor: int
    inclusive: bool


@dataclass(frozen=True)
class AttrConstraint:
    key: str | None  # None = any attribute / title / description
    value: str


@dataclass(frozen=True)
class SortSpec:
    field: Literal["price", "rating"]
    direction: Literal["asc", "desc"]


@dataclass(frozen=True)
class ConstraintSet:
    categories: frozenset[str] = field(default_factory=frozenset)
    price_min: PriceBound | None = None
    price_max: PriceBound | None = None
    stock: StockStatus | None = None
    rating_min: float | None = None
    attributes: tuple[AttrConstraint, ...] = ()
    terms: tuple[str, ...] = ()
    sort: SortSpec | None = None

    def is_empty(self) -> bool:
        return (
            len(self.categories) == 0
            and self.price_min is None
            and self.price_max is None
            and self.stock is None
            and self.rating_min is None
            and len(self.attributes) == 0
            and len(self.terms) == 0
            and self.sort is None
        )


@dataclass(frozen=True)
class ConstraintOp:
    op: OpKind
    key: str
    value: Any
    origin: Literal["USER_TEXT", "UI_ACTION", "CLARIFICATION"] = "USER_TEXT"


@dataclass(frozen=True)
class Conflict:
    key: str
    existing_value: Any
    new_value: Any
    reason: str
