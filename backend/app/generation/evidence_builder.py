from typing import Any
from app.domain.plans import DecisionPlan
from app.schemas.evidence import (
    CatalogueSearchManifest,
    Evidence,
    PolicyEvidence,
    PolicySearchManifest,
    ProductFieldEvidence,
    SearchEvidence,
)
from app.store.policy_repo import PolicyRepo
from app.store.product_repo import ProductRepo


def build_evidence(
    plan: DecisionPlan,
    product_repo: ProductRepo,
    policy_repo: PolicyRepo,
) -> dict[str, Evidence]:
    bundle: dict[str, Evidence] = {}

    # Register search manifests
    for m in plan.manifests:
        if isinstance(m, CatalogueSearchManifest):
            ev_id = f"search:{m.search_id}"
            bundle[ev_id] = SearchEvidence(evidence_id=ev_id, search=m)
        elif isinstance(m, PolicySearchManifest):
            ev_id = f"search:{m.search_id}"
            bundle[ev_id] = SearchEvidence(evidence_id=ev_id, search=m)

    # Collect all evidence IDs required by claims and options
    needed_ids: set[str] = set()
    for claim in plan.claim_specs:
        needed_ids.update(claim.evidence_ids)

    if plan.clarification:
        for opt in plan.clarification.options:
            needed_ids.update(opt.evidence_ids)

    for ev_id in needed_ids:
        if ev_id in bundle:
            continue

        if ev_id.startswith("prod:"):
            parts = ev_id.split(":", 2)
            if len(parts) == 3:
                pid = parts[1]
                field_name = parts[2]
                prod = product_repo.get(pid)
                if not prod:
                    continue

                val = None
                display_val = None
                available = False
                excerpt = None

                if field_name == "title":
                    val = prod.title
                    display_val = prod.title
                    available = True
                elif field_name == "price":
                    val = prod.price_minor
                    display_val = prod.price_display
                    available = prod.price_minor is not None
                elif field_name == "stock":
                    val = prod.stock_status.value
                    display_val = prod.stock_label
                    available = prod.stock_status.value != "UNKNOWN"
                elif field_name == "rating":
                    val = prod.rating
                    display_val = f"{prod.rating}★" if prod.rating is not None else None
                    available = prod.rating is not None
                elif field_name == "category":
                    val = prod.category
                    display_val = prod.category
                    available = prod.category is not None
                elif field_name == "description":
                    val = prod.description
                    display_val = prod.description
                    available = prod.description is not None
                elif field_name.startswith("attr."):
                    attr_key = field_name[5:]
                    attrs = product_repo.attributes(pid)
                    matched_attr = next((a for a in attrs if a["key_norm"] == attr_key), None)
                    if matched_attr:
                        val = matched_attr["value_norm"]
                        display_val = matched_attr["value_raw"]
                        available = True

                bundle[ev_id] = ProductFieldEvidence(
                    evidence_id=ev_id,
                    product_id=pid,
                    field=field_name,
                    value=val,
                    display_value=display_val,
                    available=available,
                    excerpt=excerpt,
                )

        elif ev_id.startswith("pol:"):
            sec_id = ev_id[4:]
            sec = policy_repo.get_section(sec_id)
            if sec:
                bundle[ev_id] = PolicyEvidence(
                    evidence_id=ev_id,
                    section_id=sec.section_id,
                    heading=sec.heading,
                    excerpt=sec.body,
                )

    return bundle
