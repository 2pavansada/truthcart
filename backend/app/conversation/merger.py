from dataclasses import dataclass
from typing import Sequence
from app.domain.constraints import AttrConstraint, Conflict, ConstraintOp, ConstraintSet, PriceBound, SortSpec
from app.domain.enums import OpKind, StockStatus
from app.store.product_repo import CatalogueVocab


@dataclass(frozen=True)
class MergeResult:
    constraints: ConstraintSet
    conflicts: tuple[Conflict, ...] = ()
    topic_switch: bool = False
    carried_over: bool = False


def merge(
    current: ConstraintSet,
    ops: Sequence[ConstraintOp],
    *,
    additive_cue: bool = True,
    vocab: CatalogueVocab | None = None,
) -> MergeResult:
    categories = set(current.categories)
    price_min = current.price_min
    price_max = current.price_max
    stock = current.stock
    rating_min = current.rating_min
    attributes = list(current.attributes)
    terms = list(current.terms)
    sort = current.sort

    carried_over = False
    conflicts: list[Conflict] = []

    for op in ops:
        if op.op == OpKind.RESET or op.key == "all":
            return MergeResult(constraints=ConstraintSet(), conflicts=(), topic_switch=True, carried_over=False)

        if op.op == OpKind.SET:
            if op.key == "price_min":
                if isinstance(op.value, dict):
                    price_min = PriceBound(op.value["amount_minor"], op.value.get("inclusive", False))
                elif isinstance(op.value, (int, float)):
                    price_min = PriceBound(int(op.value), False)
            elif op.key == "price_max":
                if isinstance(op.value, dict):
                    price_max = PriceBound(op.value["amount_minor"], op.value.get("inclusive", False))
                elif isinstance(op.value, (int, float)):
                    price_max = PriceBound(int(op.value), False)
            elif op.key == "stock":
                stock = StockStatus(op.value) if op.value else None
            elif op.key == "rating_min":
                rating_min = float(op.value) if op.value is not None else None
            elif op.key == "sort":
                if isinstance(op.value, dict):
                    sort = SortSpec(op.value["field"], op.value["direction"])

        elif op.op == OpKind.ADD:
            if op.key == "category":
                # If changing to an entirely new category and additive_cue is False, could replace;
                # but standard ADD expands or sets category
                cat_norm = str(op.value).lower().strip()
                categories.add(cat_norm)
            elif op.key == "attribute":
                if isinstance(op.value, dict):
                    k = op.value.get("key")
                    v = op.value.get("value", "")
                    attr_obj = AttrConstraint(key=k, value=str(v).lower())
                    if attr_obj not in attributes:
                        attributes.append(attr_obj)
                elif isinstance(op.value, str):
                    attr_obj = AttrConstraint(key=None, value=op.value.lower())
                    if attr_obj not in attributes:
                        attributes.append(attr_obj)
            elif op.key in ("term", "text_term"):
                t = str(op.value).lower().strip()
                if t and t not in terms:
                    terms.append(t)

        elif op.op == OpKind.REMOVE:
            if op.key == "price_min":
                price_min = None
            elif op.key == "price_max":
                price_max = None
            elif op.key == "stock":
                stock = None
            elif op.key == "rating_min":
                rating_min = None
            elif op.key == "sort":
                sort = None
            elif op.key == "category":
                if op.value:
                    categories.discard(str(op.value).lower().strip())
                else:
                    categories.clear()
            elif op.key == "attribute":
                if op.value and isinstance(op.value, dict):
                    target_k = op.value.get("key")
                    target_v = op.value.get("value")
                    attributes = [a for a in attributes if not (a.key == target_k and a.value == target_v)]
                else:
                    attributes.clear()
            elif op.key in ("term", "text_term"):
                if op.value:
                    terms = [t for t in terms if t != str(op.value).lower().strip()]
                else:
                    terms.clear()

    # Check price conflict
    if price_min and price_max and price_min.amount_minor > price_max.amount_minor:
        conflicts.append(Conflict(
            key="price",
            existing_value=f"min {price_min.amount_minor}",
            new_value=f"max {price_max.amount_minor}",
            reason="Minimum price exceeds maximum price",
        ))

    merged_cs = ConstraintSet(
        categories=frozenset(categories),
        price_min=price_min,
        price_max=price_max,
        stock=stock,
        rating_min=rating_min,
        attributes=tuple(attributes),
        terms=tuple(terms),
        sort=sort,
    )

    if not current.is_empty() and len(ops) > 0:
        # Check if any prior constraint was preserved
        if (
            (current.price_max and merged_cs.price_max == current.price_max)
            or (current.price_min and merged_cs.price_min == current.price_min)
            or (current.stock and merged_cs.stock == current.stock)
            or (current.categories and any(c in merged_cs.categories for c in current.categories))
            or (current.attributes and any(a in merged_cs.attributes for a in current.attributes))
        ):
            carried_over = True

    return MergeResult(
        constraints=merged_cs,
        conflicts=tuple(conflicts),
        topic_switch=False,
        carried_over=carried_over,
    )
