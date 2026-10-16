from dataclasses import dataclass
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
    parsed = ctx.parsed
    merge_res = ctx.merge_res
    state_before = ctx.state_before
    turn_id = ctx.turn_id
    repo = ctx.product_repo

    warnings: list[WarningCode] = []
    if merge_res.carried_over:
        warnings.append(WarningCode.CONSTRAINTS_CARRIED_OVER)

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
        segments.append(SegmentNF(kind="NF-01"))
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
        segments.append(SegmentNF(kind="NF-09"))
        return DecisionPlan(
            decision=Decision.REFUSE,
            intent=Intent.OUT_OF_SCOPE,
            reason=RefusalReason.OUT_OF_SCOPE,
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
    if parsed.references_focus and not state_before.focus_product_id and parsed.intent != Intent.SEARCH:
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
        target_pid = parsed.product_ids[0] if parsed.product_ids else state_before.focus_product_id
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

    # D-04: Under-specified query check
    if merge_res.constraints.is_empty() and not parsed.entity_terms and not parsed.original.strip():
        segments.append(SegmentNF(kind="NF-06"))
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

    # D-07: Search produces 0 exact matches -> REFUSE NO_CATALOGUE_MATCH with ABSENCE claim
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
