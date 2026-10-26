from typing import Any, Sequence
from app.domain.enums import AttrBasis, ClaimScope, ClaimType
from app.domain.plans import Candidate, ClaimSpec
from app.store.product_repo import Product, ProductRepo


def plan_product_fact_claims(
    product: Product,
    req_fields: Sequence[str],
    claim_counter: list[int],
) -> list[ClaimSpec]:
    specs = []
    pid = product.product_id

    for f in req_fields:
        claim_counter[0] += 1
        cid = f"c{claim_counter[0]}"

        if f == "price":
            if product.price_minor is not None:
                specs.append(ClaimSpec(
                    claim_id=cid,
                    type=ClaimType.PRICE,
                    scope=ClaimScope.PRODUCT,
                    product_id=pid,
                    value={"price_minor": product.price_minor, "currency": product.currency},
                    evidence_ids=(f"prod:{pid}:price", f"prod:{pid}:title"),
                ))
            else:
                specs.append(ClaimSpec(
                    claim_id=cid,
                    type=ClaimType.FIELD_UNAVAILABLE,
                    scope=ClaimScope.PRODUCT,
                    product_id=pid,
                    value={"field": "price"},
                    evidence_ids=(f"prod:{pid}:price", f"prod:{pid}:title"),
                ))

        elif f == "stock":
            if product.stock_status.value != "UNKNOWN":
                specs.append(ClaimSpec(
                    claim_id=cid,
                    type=ClaimType.STOCK,
                    scope=ClaimScope.PRODUCT,
                    product_id=pid,
                    value={"stock_status": product.stock_status.value},
                    evidence_ids=(f"prod:{pid}:stock", f"prod:{pid}:title"),
                ))
            else:
                specs.append(ClaimSpec(
                    claim_id=cid,
                    type=ClaimType.FIELD_UNAVAILABLE,
                    scope=ClaimScope.PRODUCT,
                    product_id=pid,
                    value={"field": "stock"},
                    evidence_ids=(f"prod:{pid}:stock", f"prod:{pid}:title"),
                ))

        elif f == "rating":
            if product.rating is not None:
                specs.append(ClaimSpec(
                    claim_id=cid,
                    type=ClaimType.RATING,
                    scope=ClaimScope.PRODUCT,
                    product_id=pid,
                    value={"rating": product.rating, "rating_count": product.rating_count},
                    evidence_ids=(f"prod:{pid}:rating", f"prod:{pid}:title"),
                ))
            else:
                specs.append(ClaimSpec(
                    claim_id=cid,
                    type=ClaimType.FIELD_UNAVAILABLE,
                    scope=ClaimScope.PRODUCT,
                    product_id=pid,
                    value={"field": "rating"},
                    evidence_ids=(f"prod:{pid}:rating", f"prod:{pid}:title"),
                ))

        elif f == "category":
            if product.category:
                specs.append(ClaimSpec(
                    claim_id=cid,
                    type=ClaimType.CATEGORY,
                    scope=ClaimScope.PRODUCT,
                    product_id=pid,
                    value={"category": product.category},
                    evidence_ids=(f"prod:{pid}:category", f"prod:{pid}:title"),
                ))
            else:
                specs.append(ClaimSpec(
                    claim_id=cid,
                    type=ClaimType.FIELD_UNAVAILABLE,
                    scope=ClaimScope.PRODUCT,
                    product_id=pid,
                    value={"field": "category"},
                    evidence_ids=(f"prod:{pid}:category", f"prod:{pid}:title"),
                ))

    return specs


def plan_summary_claim(
    product: Product,
    claim_id: str,
) -> ClaimSpec:
    pid = product.product_id
    stock_val = product.stock_status.value if product.stock_status.value != "UNKNOWN" else None
    evidence_ids = [f"prod:{pid}:price", f"prod:{pid}:stock", f"prod:{pid}:title"]
    # Include rating evidence whenever the summary sentence may mention a rating (V-11).
    if product.rating is not None:
        evidence_ids.append(f"prod:{pid}:rating")
    return ClaimSpec(
        claim_id=claim_id,
        type=ClaimType.PRODUCT_SUMMARY,
        scope=ClaimScope.PRODUCT,
        product_id=pid,
        value={
            "price_minor": product.price_minor,
            "currency": product.currency,
            "stock_status": stock_val,
            "rating": product.rating,
        },
        evidence_ids=tuple(evidence_ids),
    )
