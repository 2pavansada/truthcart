from dataclasses import dataclass
import re
from typing import Any, Sequence
from app.decision.claim_planner import plan_product_fact_claims, plan_summary_claim
from app.domain.constraints import ConstraintOp, ConstraintSet
from app.domain.enums import (
    AttrBasis,
    CardOrigin,
    ClaimScope,
    ClaimType,
    ClarifyReason,
    Completeness,
    Decision,
    Intent,
    OpKind,
    RefusalReason,
    WarningCode,
)
from app.domain.parsed_query import ParsedQuery
from app.domain.plans import (
    CardSpec,
    ClaimSpec,
    ClarificationSpec,
    DecisionPlan,
    EntityResolution,
    IdLookupResult,
    OptionSpec,
    PolicyResult,
    SearchResult,
    SegmentClaim,
    SegmentNF,
    StateUpdate,
)
from app.schemas.evidence import CatalogueSearchManifest
from app.schemas.chat import ConstraintOpIn
from app.settings import Settings
from app.store.product_repo import Product, ProductRepo

# Terms that carry no product-search signal for D-04. An under-specified browse
# ("i want to buy something") names only these; a concrete-but-absent query
# ("quantum widgets") names content words and must fall through to D-07 so we
# report a grounded absence rather than wrongly asking the user to clarify.
# This list is deliberately scoped to the D-04 routing rule only.
_GENERIC_BROWSE_TERMS = frozenset({
    "buy", "want", "need", "like", "looking", "show", "see", "browse", "shop",
    "shopping", "something", "anything", "everything", "nothing", "stuff",
    "thing", "things", "item", "items", "product", "products", "gift", "nice",
    "good", "great", "cool", "best", "new", "popular", "trending", "interesting",
    "any", "some", "few", "me", "you", "your", "please", "recommend",
    "recommendation", "suggestion", "suggestions", "deal", "deals", "offer",
    "offers", "available", "there", "options", "choice", "choices",
})


def _has_search_signal(entity_terms: Sequence[str], repo: ProductRepo) -> bool:
    """True if any term names a real catalogue dimension or a concrete product
    word (i.e. anything other than generic browse filler)."""
    vocab = repo.vocab()
    return any(
        vocab.has_term(t) or t.lower() not in _GENERIC_BROWSE_TERMS
        for t in entity_terms
    )


# Words that V-11 requires be backed by STOCK/RATING evidence on the *same*
# claim. A verbatim description quote is data, so a sentence that happens to use
# one of them is skipped rather than quoted -- we never want a template-mode
# validator trip, and we never invent a stock/rating assertion to justify it.
_QUOTE_UNSAFE_WORDS_RE = re.compile(
    r"\b(?:in stock|out of stock|sold out|available|unavailable|rated|rating|ratings)\b",
    re.IGNORECASE,
)


def _term_matches(term: str, text: str | None) -> bool:
    """Whole-word containment on the same token normalisation the lexical ranker
    uses (strip punctuation, word boundaries), so a query term counting as a
    title/attribute hit is decided by the trusted record, not by retrieval."""
    clean = re.sub(r"[^\w]", "", term or "").lower()
    if not clean:
        return False
    return re.search(r"\b" + re.escape(clean) + r"\b", (text or "").lower()) is not None


def _product_title_attr_hit(prod: Product, attr_rows: list[dict[str, Any]], terms: Sequence[str]) -> bool:
    """True if any query term appears in the product's title, category or a
    structured attribute value (a real 'titled this' match)."""
    for t in terms:
        if _term_matches(t, prod.title) or _term_matches(t, prod.category):
            return True
        for a in attr_rows:
            if _term_matches(t, a.get("value_raw")) or _term_matches(t, a.get("value_norm")):
                return True
    return False


def _pick_description_quote(prod: Product, terms: Sequence[str]) -> str | None:
    """Return the first verbatim description sentence containing a query term that
    is free of stock/rating words, else None. The returned text is an exact
    substring of ``prod.description`` so V-03 (verbatim quote) holds."""
    if not prod.description:
        return None
    for sentence in re.split(r"(?<=[.!?])\s+", prod.description):
        s = sentence.strip()
        if not s:
            continue
        if not any(_term_matches(t, s) for t in terms):
            continue
        if _QUOTE_UNSAFE_WORDS_RE.search(s):
            continue
        # Re-anchor to the original description to guarantee a verbatim substring.
        if s in prod.description:
            return s
    return None


@dataclass(frozen=True)
class DecisionContext:
    parsed: ParsedQuery
    state_before: Any
    merge_res: Any
    search: SearchResult | None
    entity: EntityResolution | None
    id_lookup: IdLookupResult | None
    policy: PolicyResult | None
    turn_id: int
    settings: Settings
    product_repo: ProductRepo


def decide(ctx: DecisionContext) -> DecisionPlan:
    """Apply the decision rules, then the D-17 claim-grounding post-condition.

    ``_decide_core`` selects the decision and plans claims; ``decide`` then drops
    any planned claim that lacks evidence or a value (D-17) so an ungrounded fact
    can never silently reach the user as an answer.
    """
    return enforce_claim_grounding(_decide_core(ctx))


def enforce_claim_grounding(plan: DecisionPlan) -> DecisionPlan:
    """D-17: after claim planning, drop any claim that lacks evidence or a value.

    A claim is only kept when it carries >=1 evidence id (V-01) and a non-null
    typed value. Dropped claims and their answer-text segments are removed; a
    suggestion whose claim was dropped is removed too. If an ANSWER loses every
    planned claim, it is downgraded to a fact-free REFUSE with reason
    ``INSUFFICIENT_EVIDENCE`` -- a missing citation must not become a definitive
    answer. Answers that never planned claims (pure non-factual templates such as
    GREETING/HELP/RESET) and refusals/clarifications are left unchanged.
    """
    if not plan.claim_specs:
        return plan

    kept = [c for c in plan.claim_specs if c.evidence_ids and c.value is not None]
    if len(kept) == len(plan.claim_specs):
        return plan  # every planned claim is grounded

    kept_ids = {c.claim_id for c in kept}

    # Nothing grounded remains for a factual answer -> safe, fact-free refusal.
    if plan.decision == Decision.ANSWER and not kept:
        return DecisionPlan(
            decision=Decision.REFUSE,
            intent=plan.intent,
            reason=RefusalReason.INSUFFICIENT_EVIDENCE,
            segments=(SegmentNF(kind="NF-13"),),
            claim_specs=(),
            card_specs=(),
            clarification=None,
            suggestions=(),
            manifests=plan.manifests,
            constraints_after=plan.constraints_after,
            state_update=plan.state_update,
            warnings=plan.warnings,
        )

    segments = tuple(
        s for s in plan.segments
        if not isinstance(s, SegmentClaim) or s.claim_id in kept_ids
    )
    kept_suggs = tuple(s for s in plan.suggestions if s[0] in kept_ids)
    # Product cards are product-scoped in both models: ClaimSpec.product_id
    # identifies the product supporting a claim, and CardSpec.product_id
    # identifies the product whose card is rendered. Keep a card only when
    # at least one surviving claim still supports that product. This is an
    # explicit ID association, not an assumed positional relationship.
    kept_product_ids = {claim.product_id for claim in kept if claim.product_id}
    kept_cards = tuple(
        card for card in plan.card_specs if card.product_id in kept_product_ids
    )

    return DecisionPlan(
        decision=plan.decision,
        intent=plan.intent,
        reason=plan.reason,
        segments=segments,
        claim_specs=tuple(kept),
        card_specs=kept_cards,
        clarification=plan.clarification,
        suggestions=kept_suggs,
        manifests=plan.manifests,
        constraints_after=plan.constraints_after,
        state_update=plan.state_update,
        warnings=plan.warnings,
    )


def _decide_core(ctx: DecisionContext) -> DecisionPlan:
    parsed = ctx.parsed
    merge_res = ctx.merge_res
    state_before = ctx.state_before
    turn_id = ctx.turn_id
    repo = ctx.product_repo

    warnings: list[WarningCode] = []
    if merge_res.carried_over:
        warnings.append(WarningCode.CONSTRAINTS_CARRIED_OVER)
    if any(item.kind == "SPELLFIX" for item in parsed.normalized.trace):
        warnings.append(WarningCode.SPELLING_CORRECTED)

    claim_counter = [0]
    planned_claims: list[ClaimSpec] = []
    segments: list[Any] = []
    card_specs: list[CardSpec] = []
    clarification: ClarificationSpec | None = None
    refusal_reason: RefusalReason | None = None
    suggestions: list[tuple[str, tuple[ConstraintOpIn, ...], str]] = []
    manifests: list[Any] = []

    # D-01: GREETING_HELP or RESET
    if parsed.intent == Intent.GREETING_HELP:
        first_token = parsed.normalized.text.casefold().strip().split(maxsplit=1)[:1]
        template = (
            "NF-02"
            if first_token and first_token[0].strip(".,?!;:-") == "help"
            else "NF-01"
        )
        segments.append(SegmentNF(kind=template))
        return DecisionPlan(
            decision=Decision.ANSWER,
            intent=Intent.GREETING_HELP,
            reason=None,
            segments=tuple(segments),
            claim_specs=(),
            card_specs=(),
            clarification=None,
            suggestions=(),
            manifests=(),
            constraints_after=merge_res.constraints,
            state_update=StateUpdate(
                focus_product_id=state_before.focus_product_id,
                last_result_ids=state_before.last_result_ids,
                constraints_to_persist=merge_res.constraints,
            ),
            warnings=tuple(warnings),
        )

    if parsed.intent == Intent.RESET:
        segments.append(SegmentNF(kind="NF-03"))
        return DecisionPlan(
            decision=Decision.ANSWER,
            intent=Intent.RESET,
            reason=None,
            segments=tuple(segments),
            claim_specs=(),
            card_specs=(),
            clarification=None,
            suggestions=(),
            manifests=(),
            constraints_after=ConstraintSet(),
            state_update=StateUpdate(
                focus_product_id=None,
                last_result_ids=(),
                constraints_to_persist=ConstraintSet(),
            ),
            warnings=(),
        )

    # D-11: OUT_OF_SCOPE / unsupported
    if parsed.intent == Intent.OUT_OF_SCOPE:
        is_unsupported_action = parsed.unsupported_action is not None
        # NF-10 names the known order/payment limitations. Other unsupported
        # D-11 actions use the generic fact-free scope refusal, not a policy
        # answer or a claim about capabilities outside the contract.
        template = (
            "NF-10"
            if parsed.unsupported_action in {"order", "payment"}
            else "NF-09"
        )
        segments.append(SegmentNF(kind=template))
        return DecisionPlan(
            decision=Decision.REFUSE,
            intent=Intent.OUT_OF_SCOPE,
            reason=(
                RefusalReason.UNSUPPORTED_REQUEST
                if is_unsupported_action
                else RefusalReason.OUT_OF_SCOPE
            ),
            segments=tuple(segments),
            claim_specs=(),
            card_specs=(),
            clarification=None,
            suggestions=(),
            manifests=(),
            constraints_after=state_before.constraints,
            state_update=StateUpdate(
                focus_product_id=state_before.focus_product_id,
                last_result_ids=state_before.last_result_ids,
                constraints_to_persist=state_before.constraints,
            ),
            warnings=(),
        )

    # D-02: Contradictory constraints
    if merge_res.conflicts:
        c_conf = merge_res.conflicts[0]
        segments.append(SegmentNF(
            kind="NF-07",
            params={"label_a": str(c_conf.existing_value), "label_b": str(c_conf.new_value)},
        ))
        clarification = ClarificationSpec(
            reason=ClarifyReason.CONTRADICTORY_CONSTRAINTS,
            options=(
                OptionSpec(
                    option_id="opt_keep_a",
                    kind="CONSTRAINT_OPS",
                    label=f"Keep {c_conf.existing_value}",
                    constraint_ops=(),
                ),
                OptionSpec(
                    option_id="opt_keep_b",
                    kind="CONSTRAINT_OPS",
                    label=f"Keep {c_conf.new_value}",
                    constraint_ops=(),
                ),
            ),
        )
        return DecisionPlan(
            decision=Decision.CLARIFY,
            intent=parsed.intent,
            reason=ClarifyReason.CONTRADICTORY_CONSTRAINTS,
            segments=tuple(segments),
            claim_specs=(),
            card_specs=(),
            clarification=clarification,
            suggestions=(),
            manifests=(),
            constraints_after=state_before.constraints,
            state_update=StateUpdate(
                focus_product_id=state_before.focus_product_id,
                last_result_ids=state_before.last_result_ids,
                constraints_to_persist=state_before.constraints,
            ),
            warnings=(),
        )

    # D-03: Unresolved reference ("it", "this", "that") with no focus product
    availability_followup_resolved = (
        state_before.availability_context is not None
        and "available" in parsed.normalized.text.lower()
    )
    # Dedicated availability/attribute questions keep their established routes;
    # this branch handles only a generic unresolved product reference.
    if (
        parsed.references_focus
        and not state_before.focus_product_id
        and not parsed.available_ambiguous
        and not parsed.attribute_question
        and not availability_followup_resolved
    ):
        segments.append(SegmentNF(kind="NF-05"))
        opts = []
        if state_before.last_result_ids:
            for pid in state_before.last_result_ids[:5]:
                p = repo.get(pid)
                if p:
                    opts.append(OptionSpec(
                        option_id=f"opt_{pid}",
                        kind="PRODUCT",
                        label=f"{p.title} — {p.price_display or 'Price unavailable'} ({p.product_id})",
                        product_id=pid,
                        evidence_ids=(f"prod:{pid}:title", f"prod:{pid}:price"),
                    ))
        clarification = ClarificationSpec(
            reason=ClarifyReason.UNRESOLVED_REFERENCE,
            options=tuple(opts),
        )
        return DecisionPlan(
            decision=Decision.CLARIFY,
            intent=parsed.intent,
            reason=ClarifyReason.UNRESOLVED_REFERENCE,
            segments=tuple(segments),
            claim_specs=(),
            card_specs=(),
            clarification=clarification,
            suggestions=(),
            manifests=(),
            constraints_after=merge_res.constraints,
            state_update=StateUpdate(
                focus_product_id=None,
                last_result_ids=state_before.last_result_ids,
                constraints_to_persist=merge_res.constraints,
            ),
            warnings=tuple(warnings),
        )

    # D-14 (DESCRIPTION_ONLY branch): An EXISTENCE query whose search candidates
    # mention the query terms ONLY inside product descriptions -- never in a title,
    # category or structured attribute value -- is answered with the non-factual
    # NF-14 lead-in plus verbatim DESCRIPTION_QUOTE claims. No RESULT_COUNT /
    # PRODUCT_EXISTS claim is planned, because a description mention does not
    # establish that a product is *titled* this. It runs before D-05 so a genuine
    # description-only mention is not mis-routed into an ambiguity clarification,
    # and it is skipped (falling through to the normal D-05 / D-14-primary path)
    # whenever any candidate carries a real title/attribute hit or no clean, safely
    # quotable description sentence exists (contract fallback, never a fabrication).
    if (
        parsed.intent == Intent.EXISTENCE
        and parsed.entity_terms
        and ctx.search
        and ctx.search.ranked
    ):
        terms = tuple(parsed.entity_terms)
        ranked_ids = [c.product_id for c in ctx.search.ranked]
        products_map = repo.get_many(ranked_ids)
        title_attr_present = False
        desc_quotes: list[tuple[Product, str]] = []
        for cid in ranked_ids:
            prod = products_map.get(cid)
            if not prod:
                continue
            if _product_title_attr_hit(prod, repo.attributes(prod.product_id), terms):
                title_attr_present = True
                break
            quote = _pick_description_quote(prod, terms)
            if quote is not None:
                desc_quotes.append((prod, quote))

        if not title_attr_present and desc_quotes:
            segments.append(SegmentNF(kind="NF-14"))
            for prod, quote in desc_quotes:
                claim_counter[0] += 1
                dq_cid = f"c{claim_counter[0]}"
                dq_claim = ClaimSpec(
                    claim_id=dq_cid,
                    type=ClaimType.DESCRIPTION_QUOTE,
                    scope=ClaimScope.PRODUCT,
                    product_id=prod.product_id,
                    value={"quote": quote},
                    evidence_ids=(
                        f"prod:{prod.product_id}:description",
                        f"prod:{prod.product_id}:title",
                    ),
                )
                planned_claims.append(dq_claim)
                segments.append(SegmentClaim(dq_cid))
            return DecisionPlan(
                decision=Decision.ANSWER,
                intent=Intent.EXISTENCE,
                reason=None,
                segments=tuple(segments),
                claim_specs=tuple(planned_claims),
                card_specs=(),
                clarification=None,
                suggestions=(),
                manifests=(),
                constraints_after=merge_res.constraints,
                state_update=StateUpdate(
                    focus_product_id=state_before.focus_product_id,
                    last_result_ids=state_before.last_result_ids,
                    constraints_to_persist=merge_res.constraints,
                ),
                warnings=tuple(warnings),
            )

    # D-05: Entity resolution ambiguous
    if ctx.entity and ctx.entity.kind == "AMBIGUOUS":
        manifests.append(ctx.entity.manifest)
        entity_name = " ".join(parsed.entity_terms) or "the product"
        segments.append(SegmentNF(kind="NF-04", params={"entity": entity_name}))
        opts = []
        for cand in ctx.entity.candidates[:5]:
            p = repo.get(cand.product_id)
            if p:
                opts.append(OptionSpec(
                    option_id=f"opt_{p.product_id}",
                    kind="PRODUCT",
                    label=f"{p.title} — {p.price_display or 'Price not available'} ({p.product_id})",
                    product_id=p.product_id,
                    evidence_ids=(f"prod:{p.product_id}:title", f"prod:{p.product_id}:price"),
                ))
        clarification = ClarificationSpec(
            reason=ClarifyReason.AMBIGUOUS_PRODUCT,
            options=tuple(opts),
        )
        return DecisionPlan(
            decision=Decision.CLARIFY,
            intent=parsed.intent,
            reason=ClarifyReason.AMBIGUOUS_PRODUCT,
            segments=tuple(segments),
            claim_specs=(),
            card_specs=(),
            clarification=clarification,
            suggestions=(),
            manifests=tuple(manifests),
            constraints_after=merge_res.constraints,
            state_update=StateUpdate(
                focus_product_id=None,
                last_result_ids=state_before.last_result_ids,
                constraints_to_persist=merge_res.constraints,
            ),
            warnings=tuple(warnings),
        )

    # POLICY Intent handling
    if parsed.intent == Intent.POLICY:
        pol_res = ctx.policy
        if pol_res:
            manifests.append(pol_res.manifest)
            # D-06: POLICY with no determinable topic on a generic question ->
            # clarify the policy topics that exist rather than refuse as
            # unsupported (only when there is at least one topic to offer).
            if (
                not pol_res.established
                and parsed.policy_topic is None
                and pol_res.available_topics
            ):
                segments.append(SegmentNF(kind="NF-08"))
                max_opts = ctx.settings.thresholds.clarify_max_options
                opts = tuple(
                    OptionSpec(
                        option_id=f"opt_topic_{i}",
                        kind="POLICY_TOPIC",
                        label=t.replace("_", " ").capitalize(),
                        policy_topic=t,
                    )
                    for i, t in enumerate(pol_res.available_topics[:max_opts])
                )
                clarification = ClarificationSpec(
                    reason=ClarifyReason.AMBIGUOUS_POLICY_TOPIC,
                    options=opts,
                )
                return DecisionPlan(
                    decision=Decision.CLARIFY,
                    intent=Intent.POLICY,
                    reason=ClarifyReason.AMBIGUOUS_POLICY_TOPIC,
                    segments=tuple(segments),
                    claim_specs=(),
                    card_specs=(),
                    clarification=clarification,
                    suggestions=(),
                    manifests=tuple(manifests),
                    constraints_after=merge_res.constraints,
                    state_update=StateUpdate(
                        focus_product_id=state_before.focus_product_id,
                        last_result_ids=state_before.last_result_ids,
                        constraints_to_persist=merge_res.constraints,
                    ),
                    warnings=tuple(warnings),
                )
            if not pol_res.established:
                # D-09: REFUSE POLICY_NOT_ESTABLISHED
                claim_counter[0] += 1
                cid = f"c{claim_counter[0]}"
                topic_searched = parsed.policy_topic or parsed.original
                claim = ClaimSpec(
                    claim_id=cid,
                    type=ClaimType.ABSENCE,
                    scope=ClaimScope.GENERAL_POLICY,
                    product_id=None,
                    value={"searched": topic_searched},
                    evidence_ids=(f"search:p_{turn_id}",),
                )
                planned_claims.append(claim)
                segments.append(SegmentClaim(cid))
                return DecisionPlan(
                    decision=Decision.REFUSE,
                    intent=Intent.POLICY,
                    reason=RefusalReason.POLICY_NOT_ESTABLISHED,
                    segments=tuple(segments),
                    claim_specs=tuple(planned_claims),
                    card_specs=(),
                    clarification=None,
                    suggestions=(),
                    manifests=tuple(manifests),
                    constraints_after=merge_res.constraints,
                    state_update=StateUpdate(
                        focus_product_id=state_before.focus_product_id,
                        last_result_ids=state_before.last_result_ids,
                        constraints_to_persist=merge_res.constraints,
                    ),
                    warnings=tuple(warnings),
                )
            else:
                # D-16: ANSWER with POLICY_QUOTE claims
                for sec in pol_res.sections[:2]:
                    # Take first relevant sentence from body or body itself
                    sentences = [s.strip() for s in sec.body.split("\n") if s.strip()]
                    quote = sentences[0] if sentences else sec.heading
                    claim_counter[0] += 1
                    cid = f"c{claim_counter[0]}"
                    claim = ClaimSpec(
                        claim_id=cid,
                        type=ClaimType.POLICY_QUOTE,
                        scope=ClaimScope.GENERAL_POLICY,
                        product_id=None,
                        value={"quote": quote, "section_id": sec.section_id},
                        evidence_ids=(f"pol:{sec.section_id}",),
                    )
                    planned_claims.append(claim)
                    segments.append(SegmentClaim(cid))

                return DecisionPlan(
                    decision=Decision.ANSWER,
                    intent=Intent.POLICY,
                    reason=None,
                    segments=tuple(segments),
                    claim_specs=tuple(planned_claims),
                    card_specs=(),
                    clarification=None,
                    suggestions=(),
                    manifests=tuple(manifests),
                    constraints_after=merge_res.constraints,
                    state_update=StateUpdate(
                        focus_product_id=state_before.focus_product_id,
                        last_result_ids=state_before.last_result_ids,
                        constraints_to_persist=merge_res.constraints,
                    ),
                    warnings=tuple(warnings),
                )

    # PRODUCT_WARRANTY Intent handling
    if parsed.intent == Intent.PRODUCT_WARRANTY:
        resolved_entity_pid = (
            ctx.entity.candidates[0].product_id
            if ctx.entity and ctx.entity.kind == "RESOLVED" and ctx.entity.candidates
            else None
        )
        target_pid = (
            parsed.product_ids[0]
            if parsed.product_ids
            else state_before.focus_product_id or resolved_entity_pid
        )
        prod = repo.get(target_pid) if target_pid else None
        if prod:
            attrs = repo.attributes(prod.product_id)
            w_attr = next((a for a in attrs if a["key_norm"] == "warranty"), None)
            if w_attr:
                # D-10: ANSWER with ATTRIBUTE claim
                claim_counter[0] += 1
                cid = f"c{claim_counter[0]}"
                claim = ClaimSpec(
                    claim_id=cid,
                    type=ClaimType.ATTRIBUTE,
                    scope=ClaimScope.PRODUCT,
                    product_id=prod.product_id,
                    value={"key": "warranty", "value": w_attr["value_raw"], "basis": AttrBasis.STRUCTURED},
                    evidence_ids=(f"prod:{prod.product_id}:attr.warranty", f"prod:{prod.product_id}:title"),
                )
                planned_claims.append(claim)
                segments.append(SegmentClaim(cid))
                return DecisionPlan(
                    decision=Decision.ANSWER,
                    intent=Intent.PRODUCT_WARRANTY,
                    reason=None,
                    segments=tuple(segments),
                    claim_specs=tuple(planned_claims),
                    card_specs=(CardSpec(product_id=prod.product_id, origin=CardOrigin.LOOKUP, applied_search_id=None),),
                    clarification=None,
                    suggestions=(),
                    manifests=(),
                    constraints_after=merge_res.constraints,
                    state_update=StateUpdate(
                        focus_product_id=prod.product_id,
                        last_result_ids=(prod.product_id,),
                        constraints_to_persist=merge_res.constraints,
                    ),
                    warnings=tuple(warnings),
                )
            else:
                # D-10: REFUSE FIELD_MISSING
                claim_counter[0] += 1
                cid = f"c{claim_counter[0]}"
                claim = ClaimSpec(
                    claim_id=cid,
                    type=ClaimType.FIELD_UNAVAILABLE,
                    scope=ClaimScope.PRODUCT,
                    product_id=prod.product_id,
                    value={"field": "warranty"},
                    evidence_ids=(f"prod:{prod.product_id}:attr.warranty", f"prod:{prod.product_id}:title"),
                )
                planned_claims.append(claim)
                segments.append(SegmentClaim(cid))
                return DecisionPlan(
                    decision=Decision.REFUSE,
                    intent=Intent.PRODUCT_WARRANTY,
                    reason=RefusalReason.FIELD_MISSING,
                    segments=tuple(segments),
                    claim_specs=tuple(planned_claims),
                    card_specs=(),
                    clarification=None,
                    suggestions=(),
                    manifests=(),
                    constraints_after=merge_res.constraints,
                    state_update=StateUpdate(
                        focus_product_id=prod.product_id,
                        last_result_ids=(prod.product_id,),
                        constraints_to_persist=merge_res.constraints,
                    ),
                    warnings=tuple(warnings),
                )

    # PRODUCT_FACT Intent handling (by explicit ID or resolved focus)
    if parsed.intent == Intent.PRODUCT_FACT:
        target_pid = parsed.product_ids[0] if parsed.product_ids else state_before.focus_product_id
        if target_pid:
            prod = repo.get(target_pid)
            if not prod:
                # D-07: REFUSE NO_CATALOGUE_MATCH
                manifest = CatalogueSearchManifest(
                    search_id=f"s_{turn_id}",
                    scope="CATALOGUE",
                    intent=Intent.PRODUCT_FACT,
                    query_text=target_pid,
                    lookup_ids=[target_pid],
                    applied_constraints=[],
                    term_requirements=[],
                    term_min_coverage=1.0,
                    completeness=Completeness.EXHAUSTIVE,
                    incomplete_reasons=[],
                    eligible_count=0,
                    exact_match_count=0,
                    returned_count=0,
                    excluded_unknown_stock=0,
                )
                manifests.append(manifest)
                claim_counter[0] += 1
                cid = f"c{claim_counter[0]}"
                claim = ClaimSpec(
                    claim_id=cid,
                    type=ClaimType.ABSENCE,
                    scope=ClaimScope.CATALOGUE,
                    product_id=None,
                    value={"searched": target_pid},
                    evidence_ids=(f"search:s_{turn_id}",),
                )
                planned_claims.append(claim)
                segments.append(SegmentClaim(cid))
                return DecisionPlan(
                    decision=Decision.REFUSE,
                    intent=Intent.PRODUCT_FACT,
                    reason=RefusalReason.NO_CATALOGUE_MATCH,
                    segments=tuple(segments),
                    claim_specs=tuple(planned_claims),
                    card_specs=(),
                    clarification=None,
                    suggestions=(),
                    manifests=tuple(manifests),
                    constraints_after=merge_res.constraints,
                    state_update=StateUpdate(
                        focus_product_id=None,
                        last_result_ids=state_before.last_result_ids,
                        constraints_to_persist=merge_res.constraints,
                    ),
                    warnings=tuple(warnings),
                )
            else:
                # Target product found
                req_fields = [rf.value.lower() for rf in parsed.requested_fields] or ["price", "stock"]
                fact_claims = plan_product_fact_claims(prod, req_fields, claim_counter)
                planned_claims.extend(fact_claims)
                for fc in fact_claims:
                    segments.append(SegmentClaim(fc.claim_id))

                has_available = any(fc.type != ClaimType.FIELD_UNAVAILABLE for fc in fact_claims)
                all_missing = all(fc.type == ClaimType.FIELD_UNAVAILABLE for fc in fact_claims)

                dec = Decision.REFUSE if all_missing else Decision.ANSWER
                reas = RefusalReason.FIELD_MISSING if all_missing else None

                card_specs.append(CardSpec(
                    product_id=prod.product_id,
                    origin=CardOrigin.LOOKUP,
                    applied_search_id=None,
                ))

                return DecisionPlan(
                    decision=dec,
                    intent=Intent.PRODUCT_FACT,
                    reason=reas,
                    segments=tuple(segments),
                    claim_specs=tuple(planned_claims),
                    card_specs=tuple(card_specs),
                    clarification=None,
                    suggestions=(),
                    manifests=(),
                    constraints_after=merge_res.constraints,
                    state_update=StateUpdate(
                        focus_product_id=prod.product_id,
                        last_result_ids=(prod.product_id,),
                        constraints_to_persist=merge_res.constraints,
                    ),
                    warnings=tuple(warnings),
                )

    # SEARCH and EXISTENCE Intent handling
    search_res = ctx.search
    if not search_res:
        segments.append(SegmentNF(kind="NF-13"))
        return DecisionPlan(
            decision=Decision.REFUSE,
            intent=parsed.intent,
            reason=RefusalReason.INTERNAL_VALIDATION_FAILURE,
            segments=tuple(segments),
            claim_specs=(),
            card_specs=(),
            clarification=None,
            suggestions=(),
            manifests=(),
            constraints_after=merge_res.constraints,
            state_update=StateUpdate(
                focus_product_id=state_before.focus_product_id,
                last_result_ids=state_before.last_result_ids,
                constraints_to_persist=merge_res.constraints,
            ),
            warnings=tuple(warnings),
        )

    manifests.append(search_res.manifest)

    # D-04: Under-specified query. A SEARCH with no hard constraint and no search
    # term carrying a real search signal (a catalogue lexical hit or a concrete
    # product word) is an unconstrained browse -- clarify the request (NF-06)
    # instead of answering with a default result set. A concrete-but-absent query
    # ("quantum widgets") does carry a search signal, so it is left to fall through
    # to D-07 for a grounded absence rather than being mis-routed to a clarification.
    # UNKNOWN with no extracted content is also under-specified per RAG_PIPELINE
    # §3; UNKNOWN with content is converted to SEARCH by parse_query. Product,
    # existence, and policy routes remain independent.
    _under_spec_search = (
        parsed.intent in (Intent.SEARCH, Intent.UNKNOWN)
        and (
            parsed.category_ambiguous
            or parsed.available_ambiguous
            or parsed.intent == Intent.UNKNOWN
            or (
                merge_res.constraints.is_empty()
                and not _has_search_signal(parsed.entity_terms, repo)
            )
        )
    )
    if _under_spec_search:
        segments.append(SegmentNF(kind="NF-15" if parsed.available_ambiguous else "NF-06"))
        clarification = ClarificationSpec(
            reason=ClarifyReason.UNDER_SPECIFIED_QUERY,
            options=(),
        )
        return DecisionPlan(
            decision=Decision.CLARIFY,
            intent=parsed.intent,
            reason=ClarifyReason.UNDER_SPECIFIED_QUERY,
            segments=tuple(segments),
            claim_specs=(),
            card_specs=(),
            clarification=clarification,
            suggestions=(),
            manifests=tuple(manifests),
            constraints_after=merge_res.constraints,
            state_update=StateUpdate(
                focus_product_id=state_before.focus_product_id,
                last_result_ids=state_before.last_result_ids,
                constraints_to_persist=merge_res.constraints,
            ),
            warnings=tuple(warnings),
        )

    # Incomplete search must not produce an ABSENCE claim (CONTRACTS §1 completeness).
    if (
        search_res.manifest.completeness == Completeness.INCOMPLETE
        and len(search_res.exact_ids) == 0
    ):
        warnings.append(WarningCode.SEARCH_INCOMPLETE)
        segments.append(SegmentNF(kind="NF-13"))
        return DecisionPlan(
            decision=Decision.REFUSE,
            intent=parsed.intent,
            reason=RefusalReason.SEARCH_INCOMPLETE,
            segments=tuple(segments),
            claim_specs=(),
            card_specs=(),
            clarification=None,
            suggestions=(),
            manifests=tuple(manifests),
            constraints_after=merge_res.constraints,
            state_update=StateUpdate(
                focus_product_id=state_before.focus_product_id,
                last_result_ids=state_before.last_result_ids,
                constraints_to_persist=merge_res.constraints,
            ),
            warnings=tuple(warnings),
        )

    # D-07: Exhaustive search with 0 exact matches -> REFUSE NO_CATALOGUE_MATCH + ABSENCE
    if len(search_res.exact_ids) == 0:
        claim_counter[0] += 1
        cid = f"c{claim_counter[0]}"
        searched_term = " ".join(parsed.entity_terms) or parsed.original.strip() or "specified criteria"
        claim = ClaimSpec(
            claim_id=cid,
            type=ClaimType.ABSENCE,
            scope=ClaimScope.CATALOGUE,
            product_id=None,
            value={"searched": searched_term},
            evidence_ids=(f"search:s_{turn_id}",),
        )
        planned_claims.append(claim)
        segments.append(SegmentClaim(cid))

        # Build suggestions from relaxations
        suggs = []
        for rel in search_res.manifest.relaxations:
            # Op to remove that constraint
            remove_op = ConstraintOpIn(op="REMOVE", key=rel.dropped)
            suggs.append((
                cid,
                (remove_op,),
                f"Without {rel.label.lower()}: {rel.exact_match_count} products match",
            ))

        return DecisionPlan(
            decision=Decision.REFUSE,
            intent=parsed.intent,
            reason=RefusalReason.NO_CATALOGUE_MATCH,
            segments=tuple(segments),
            claim_specs=tuple(planned_claims),
            card_specs=(),
            clarification=None,
            suggestions=tuple(suggs),
            manifests=tuple(manifests),
            constraints_after=merge_res.constraints,
            state_update=StateUpdate(
                focus_product_id=state_before.focus_product_id,
                last_result_ids=state_before.last_result_ids,
                constraints_to_persist=merge_res.constraints,
            ),
            warnings=tuple(warnings),
        )

    # D-15 / D-14: Search produces >= 1 verified matches -> ANSWER
    if merge_res.carried_over:
        c_views = search_res.manifest.applied_constraints
        c_labels = ", ".join(cv.label for cv in c_views)
        segments.append(SegmentNF(kind="NF-11", params={"constraints": c_labels}))

    # 1. RESULT_COUNT claim
    claim_counter[0] += 1
    c_count_id = f"c{claim_counter[0]}"
    count_claim = ClaimSpec(
        claim_id=c_count_id,
        type=ClaimType.RESULT_COUNT,
        scope=ClaimScope.CATALOGUE,
        product_id=None,
        value={
            "count": len(search_res.exact_ids),
            "context": "SEARCH" if parsed.intent == Intent.SEARCH else "EXISTENCE",
            "relaxed_constraint": None,
        },
        evidence_ids=(f"search:s_{turn_id}",),
    )
    planned_claims.append(count_claim)
    segments.append(SegmentClaim(c_count_id))

    # 2. Product summary claims for top results
    result_pids = [c.product_id for c in search_res.ranked]
    for pid in result_pids:
        prod = repo.get(pid)
        if prod:
            claim_counter[0] += 1
            cid = f"c{claim_counter[0]}"
            p_claim = plan_summary_claim(prod, cid)
            planned_claims.append(p_claim)
            segments.append(SegmentClaim(cid))
            card_specs.append(CardSpec(
                product_id=prod.product_id,
                origin=CardOrigin.SEARCH_RESULT,
                applied_search_id=f"s_{turn_id}",
            ))

    new_focus = result_pids[0] if len(result_pids) == 1 else state_before.focus_product_id

    return DecisionPlan(
        decision=Decision.ANSWER,
        intent=parsed.intent,
        reason=None,
        segments=tuple(segments),
        claim_specs=tuple(planned_claims),
        card_specs=tuple(card_specs),
        clarification=None,
        suggestions=(),
        manifests=tuple(manifests),
        constraints_after=merge_res.constraints,
        state_update=StateUpdate(
            focus_product_id=new_focus,
            last_result_ids=tuple(result_pids),
            constraints_to_persist=merge_res.constraints,
        ),
        warnings=tuple(warnings),
    )
