from typing import Mapping
from app.domain.plans import CardSpec, DecisionPlan
from app.schemas.claims import CardFact, ProductCard
from app.schemas.evidence import Evidence, ProductFieldEvidence
from app.store.product_repo import ProductRepo


def build_cards(
    plan: DecisionPlan,
    bundle: Mapping[str, Evidence],
    product_repo: ProductRepo,
) -> list[ProductCard]:
    cards = []

    for spec in plan.card_specs:
        pid = spec.product_id
        prod = product_repo.get(pid)
        if not prod:
            continue

        title_ev_id = f"prod:{pid}:title"
        title = prod.title
        title_ev = bundle.get(title_ev_id)
        if isinstance(title_ev, ProductFieldEvidence) and title_ev.display_value:
            title = title_ev.display_value

        facts = [
            CardFact(
                field="price",
                label="Price",
                value=prod.price_minor,
                display=prod.price_display or "Price not available",
                available=prod.price_minor is not None,
                evidence_id=f"prod:{pid}:price",
            ),
            CardFact(
                field="stock",
                label="Stock",
                value=prod.stock_status.value,
                display=prod.stock_label,
                available=prod.stock_status.value != "UNKNOWN",
                evidence_id=f"prod:{pid}:stock",
            ),
        ]

        cards.append(ProductCard(
            product_id=pid,
            title=title,
            title_evidence_id=title_ev_id,
            origin=spec.origin,
            applied_search_id=spec.applied_search_id,
            facts=facts,
        ))

    return cards
