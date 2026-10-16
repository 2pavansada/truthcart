from dataclasses import dataclass
from typing import Any
from app.domain.plans import DecisionPlan, PlanDigest
from app.generation.card_builder import build_cards
from app.generation.citation import render_answer_text
from app.generation.evidence_builder import build_evidence
from app.generation.templates import render_claim
from app.schemas.claims import (
    Claim,
    ClarificationBlock,
    ClarificationOption,
    ProductCard,
    RefusalBlock,
    Suggestion,
)
from app.schemas.evidence import Evidence
from app.store.policy_repo import PolicyRepo
from app.store.product_repo import ProductRepo


@dataclass(frozen=True)
class DraftResponse:
    decision: Any
    answer_text: str
    claims: list[Claim]
    evidence: list[Evidence]
    products: list[ProductCard]
    clarification: ClarificationBlock | None
    refusal: RefusalBlock | None
    suggestions: list[Suggestion]


def generate_response(
    plan: DecisionPlan,
    product_repo: ProductRepo,
    policy_repo: PolicyRepo,
    show_search_marker: bool = True,
) -> tuple[DraftResponse, PlanDigest]:
    # 1. Build evidence bundle
    bundle = build_evidence(plan, product_repo, policy_repo)

    # 2. Render each claim sentence
    rendered_claims: list[Claim] = []
    claim_sentences: dict[str, str] = {}

    for spec in plan.claim_specs:
        sentence = render_claim(spec, bundle)
        claim_sentences[spec.claim_id] = sentence
        rendered_claims.append(Claim(
            claim_id=spec.claim_id,
            type=spec.type,
            scope=spec.scope,
            product_id=spec.product_id,
            value=spec.value,
            sentence=sentence,
            evidence_ids=list(spec.evidence_ids),
        ))

    # 3. Assemble answer text
    answer_text = render_answer_text(
        plan,
        claim_sentences,
        bundle,
        show_search_marker=show_search_marker,
    )

    # 4. Build clarification block if needed
    clarification_block = None
    if plan.clarification:
        opts = [
            ClarificationOption(
                option_id=o.option_id,
                kind=o.kind,
                label=o.label,
                product_id=o.product_id,
                constraint_ops=list(o.constraint_ops),
                policy_topic=o.policy_topic,
                evidence_ids=list(o.evidence_ids),
            )
            for o in plan.clarification.options
        ]
        clarification_block = ClarificationBlock(
            clarification_id=f"clar_{plan.decision.value}",
            reason=plan.clarification.reason,
            options=opts,
        )

    # 5. Build refusal block if needed
    refusal_block = None
    if plan.reason and hasattr(plan.reason, "value") and plan.decision.value == "REFUSE":
        refusal_block = RefusalBlock(reason=plan.reason)

    # 6. Build cards
    cards = build_cards(plan, bundle, product_repo)

    # 7. Build suggestions
    suggs = [
        Suggestion(claim_id=s[0], constraint_ops=list(s[1]), label=s[2])
        for s in plan.suggestions
    ]

    # 8. Create PlanDigest
    digest = PlanDigest(
        claim_ids=tuple(c.claim_id for c in plan.claim_specs),
        types=tuple(c.type for c in plan.claim_specs),
        values=tuple(c.value for c in plan.claim_specs),
        evidence_ids=tuple(c.evidence_ids for c in plan.claim_specs),
    )

    draft = DraftResponse(
        decision=plan.decision,
        answer_text=answer_text,
        claims=rendered_claims,
        evidence=list(bundle.values()),
        products=cards,
        clarification=clarification_block,
        refusal=refusal_block,
        suggestions=suggs,
    )

    return draft, digest
