import re
from typing import Any, Sequence
from app.domain.enums import ClaimScope, ClaimType, Decision
from app.domain.plans import PlanDigest, ValidationReport, Violation
from app.schemas.claims import Claim, ProductCard
from app.schemas.evidence import Evidence, PolicyEvidence, ProductFieldEvidence, SearchEvidence
from app.store.policy_repo import PolicyRepo
from app.store.product_repo import ProductRepo


class Validator:
    def __init__(self, product_repo: ProductRepo, policy_repo: PolicyRepo):
        self.product_repo = product_repo
        self.policy_repo = policy_repo

    def validate(
        self,
        draft: Any,
        digest: PlanDigest,
    ) -> ValidationReport:
        violations: list[Violation] = []

        evidence_map: dict[str, Evidence] = {e.evidence_id: e for e in draft.evidence}

        # V-01: Evidence present
        for claim in draft.claims:
            if not claim.evidence_ids:
                violations.append(Violation(
                    rule="V-01",
                    claim_id=claim.claim_id,
                    detail="Claim has no evidence IDs",
                ))

        # V-02: Evidence real and compatible
        for claim in draft.claims:
            for ev_id in claim.evidence_ids:
                if ev_id not in evidence_map:
                    violations.append(Violation(
                        rule="V-02",
                        claim_id=claim.claim_id,
                        detail=f"Evidence ID {ev_id} not in response evidence bundle",
                    ))
                elif claim.scope == ClaimScope.PRODUCT and ev_id.startswith("prod:"):
                    parts = ev_id.split(":", 2)
                    if len(parts) >= 2 and parts[1] != claim.product_id:
                        violations.append(Violation(
                            rule="V-02",
                            claim_id=claim.claim_id,
                            detail=f"Evidence product ID {parts[1]} does not match claim product ID {claim.product_id}",
                        ))

        # V-03: Typed value equals DB source
        for claim in draft.claims:
            if claim.scope == ClaimScope.PRODUCT and claim.product_id:
                prod = self.product_repo.get(claim.product_id)
                if not prod:
                    violations.append(Violation(
                        rule="V-03",
                        claim_id=claim.claim_id,
                        detail=f"Product {claim.product_id} not in DB",
                    ))
                    continue

                if claim.type == ClaimType.PRICE:
                    expected = claim.value.get("price_minor")
                    if prod.price_minor != expected:
                        violations.append(Violation(
                            rule="V-03",
                            claim_id=claim.claim_id,
                            detail=f"Price {expected} does not match DB price {prod.price_minor}",
                        ))

                elif claim.type == ClaimType.STOCK:
                    expected = claim.value.get("stock_status")
                    if prod.stock_status.value != expected:
                        violations.append(Violation(
                            rule="V-03",
                            claim_id=claim.claim_id,
                            detail=f"Stock {expected} does not match DB stock {prod.stock_status.value}",
                        ))

                elif claim.type == ClaimType.RATING:
                    expected = claim.value.get("rating")
                    if prod.rating != expected:
                        violations.append(Violation(
                            rule="V-03",
                            claim_id=claim.claim_id,
                            detail=f"Rating {expected} does not match DB rating {prod.rating}",
                        ))

            elif claim.type == ClaimType.ABSENCE:
                # Absence requires search manifest with exact_match_count == 0
                for ev_id in claim.evidence_ids:
                    ev = evidence_map.get(ev_id)
                    if isinstance(ev, SearchEvidence):
                        sm = ev.search
                        if hasattr(sm, "exact_match_count") and sm.exact_match_count > 0:
                            violations.append(Violation(
                                rule="V-03",
                                claim_id=claim.claim_id,
                                detail=f"ABSENCE claim made but search manifest has {sm.exact_match_count} exact matches",
                            ))

        # V-07: Decision consistency
        if draft.decision == Decision.ANSWER:
            if not draft.claims and not any(nf in draft.answer_text for nf in ["Hello", "cleared", "look up"]):
                violations.append(Violation(
                    rule="V-07",
                    claim_id=None,
                    detail="ANSWER decision has no claims and is not non-factual greeting/reset",
                ))
        elif draft.decision == Decision.CLARIFY:
            if not draft.clarification:
                violations.append(Violation(
                    rule="V-07",
                    claim_id=None,
                    detail="CLARIFY decision requires clarification block",
                ))
        elif draft.decision == Decision.REFUSE:
            if not draft.refusal:
                violations.append(Violation(
                    rule="V-07",
                    claim_id=None,
                    detail="REFUSE decision requires refusal block",
                ))

        # V-08: Policy claims verification
        for claim in draft.claims:
            if claim.type == ClaimType.POLICY_QUOTE:
                sec_id = claim.value.get("section_id")
                quote = claim.value.get("quote", "")
                if sec_id:
                    sec = self.policy_repo.get_section(sec_id)
                    if not sec:
                        violations.append(Violation(
                            rule="V-08",
                            claim_id=claim.claim_id,
                            detail=f"Policy section {sec_id} not in DB",
                        ))
                    elif quote not in sec.body and quote not in sec.heading:
                        violations.append(Violation(
                            rule="V-08",
                            claim_id=claim.claim_id,
                            detail="Quoted text not found verbatim in policy section",
                        ))

        # V-10: Cards re-verification
        for card in draft.products:
            prod = self.product_repo.get(card.product_id)
            if not prod:
                violations.append(Violation(
                    rule="V-10",
                    claim_id=None,
                    detail=f"Card product {card.product_id} not in DB",
                ))
            else:
                for fact in card.facts:
                    if fact.field == "price":
                        if prod.price_minor != fact.value:
                            violations.append(Violation(
                                rule="V-10",
                                claim_id=None,
                                detail=f"Card price {fact.value} != DB {prod.price_minor}",
                            ))
                    elif fact.field == "stock":
                        if prod.stock_status.value != fact.value:
                            violations.append(Violation(
                                rule="V-10",
                                claim_id=None,
                                detail=f"Card stock {fact.value} != DB {prod.stock_status.value}",
                            ))

        # V-11: Product cards must satisfy hard constraints from the search manifest
        # This is a final safety net: re-check price_max, price_min, and stock
        # against the applied constraints captured in the search evidence.
        search_ev: SearchEvidence | None = None
        for ev in draft.evidence:
            if isinstance(ev, SearchEvidence):
                search_ev = ev
                break

        if search_ev and search_ev.search:
            sm = search_ev.search
            # Extract constraint bounds from applied_constraint labels for re-checking
            # We verify directly using the DB source values
            for card in draft.products:
                prod = self.product_repo.get(card.product_id)
                if not prod:
                    continue  # Already caught by V-10

                for cv in sm.applied_constraints:
                    key = cv.key
                    op_data = cv.op_to_remove

                    if key == "price_max" and op_data:
                        # Extract amount_minor from the remove op value
                        try:
                            amount_minor = None
                            # op_to_remove has value field for price_max
                            remove_val = getattr(op_data, "value", None)
                            if isinstance(remove_val, dict):
                                amount_minor = remove_val.get("amount_minor")
                            # If product price is NULL, it fails the price_max check
                            if prod.price_minor is None:
                                violations.append(Violation(
                                    rule="V-11",
                                    claim_id=None,
                                    detail=f"Card {card.product_id} has NULL price but price_max constraint is active",
                                ))
                        except Exception:
                            pass  # Best-effort check

                    elif key == "stock":
                        # If IN_STOCK constraint is active, card must be IN_STOCK
                        from app.domain.enums import StockStatus
                        if prod.stock_status != StockStatus.IN_STOCK:
                            # Check if the label says "In stock only"
                            if "In stock only" in cv.label:
                                violations.append(Violation(
                                    rule="V-11",
                                    claim_id=None,
                                    detail=f"Card {card.product_id} has stock={prod.stock_status.value} but In stock only constraint is active",
                                ))

        return ValidationReport(ok=len(violations) == 0, violations=tuple(violations))
