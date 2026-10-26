"""End-to-end routing tests for the Phase-3 fixes (real orchestrator pipeline).

These drive the actual service interface (Orchestrator.handle) rather than the
decision layer in isolation, so they prove the routing behaviour reaches the user.
The isolated decision-engine tests (test_decision_engine.py) are kept unchanged.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.decision import engine as decision_engine
from app.container import build_container
from app.domain.enums import (
    ClaimScope, ClaimType, ClarifyReason, Completeness, Decision, Intent, RefusalReason,
)
from app.domain.plans import Candidate, SearchResult, SegmentClaim, ValidationReport, Violation
from app.nlp.constraint_extractor import parse_query
from app.orchestrator import Orchestrator
from app.schemas.chat import ChatRequest, Resolution
from app.schemas.evidence import ProductFieldEvidence
from app.settings import load_settings


@pytest.fixture(scope="module")
def orch() -> Orchestrator:
    return Orchestrator(build_container(load_settings()))


def _say(orch, session, message, resolution=None):
    req = ChatRequest(session_id=session, message=message, resolution=resolution)
    return orch.handle(req, request_id=f"t-{session}")


def _reason(resp):
    if resp.refusal is not None:
        return resp.refusal.reason
    if resp.clarification is not None:
        return resp.clarification.reason
    return None


def test_d01_help_returns_contract_help_template_e2e(orch):
    """A direct help request gets the non-factual HELP template end to end."""
    session = "d01-help-template"
    orch.reset(session)

    response = _say(orch, session, "help")

    assert response.decision == Decision.ANSWER
    assert response.answer_text == (
        "I can look up product prices, stock, ratings and details, "
        "search with filters, and quote the supplied policy."
    )
    assert response.claims == []
    assert response.evidence == []
    assert response.products == []
    assert response.clarification is None
    assert response.refusal is None
    assert response.suggestions == []
    assert response.metrics.validator_trips == 0


@pytest.mark.requirement_coverage(test_id="FI-D03-unresolved-reference-with-history")
def test_d03_unresolved_reference_uses_grounded_prior_results_e2e(
    orch, record_requirement_coverage
):
    """An anaphoric follow-up without focus clarifies from prior results, not search."""
    session = "d03-history-no-focus"
    orch.reset(session)
    prior_result_ids = (
        "SH-BLK-001", "SH-BLK-002", "SH-BLK-003", "SH-BLK-004",
        "SH-BLK-005", "SH-TRK-001",
    )
    state = orch.container.sessions.get_or_create(session)
    assert state.focus_product_id is None
    assert all(orch.container.product_repo.get(pid) for pid in prior_result_ids)
    state.last_result_ids = prior_result_ids
    orch.container.sessions.save(state)

    response = _say(orch, session, "What about this one?")

    assert response.decision == Decision.CLARIFY
    assert response.clarification is not None
    assert response.clarification.reason == ClarifyReason.UNRESOLVED_REFERENCE
    assert [option.product_id for option in response.clarification.options] == list(
        prior_result_ids[:5]
    )
    assert len(response.clarification.options) == 5
    assert response.claims == []
    assert response.products == []
    assert not any(pid in response.answer_text for pid in prior_result_ids)
    assert response.metrics.validator_trips == 0
    assert orch.container.sessions.get_or_create(session).focus_product_id is None

    evidence_by_id = {item.evidence_id: item for item in response.evidence}
    for option in response.clarification.options:
        product = orch.container.product_repo.get(option.product_id)
        assert product is not None and product.price_display is not None
        title_id = f"prod:{product.product_id}:title"
        price_id = f"prod:{product.product_id}:price"
        assert option.kind == "PRODUCT"
        assert option.evidence_ids == [title_id, price_id]
        title_evidence = evidence_by_id[title_id]
        price_evidence = evidence_by_id[price_id]
        assert isinstance(title_evidence, ProductFieldEvidence)
        assert isinstance(price_evidence, ProductFieldEvidence)
        assert title_evidence.product_id == product.product_id
        assert title_evidence.field == "title"
        assert title_evidence.available
        assert title_evidence.display_value == product.title
        assert price_evidence.product_id == product.product_id
        assert price_evidence.field == "price"
        assert price_evidence.available
        assert price_evidence.display_value == product.price_display
        assert option.label == (
            f"{product.title} — {product.price_display} ({product.product_id})"
        )

    record_requirement_coverage(
        "FI-D03-unresolved-reference-with-history", "D-03",
        "D-03.unresolved-reference-with-history",
        "clarify-with-up-to-five-grounded-prior-result-options",
    )


@pytest.mark.requirement_coverage(test_id="FI-D05-ambiguous-warranty-tie")
def test_d05_ambiguous_warranty_entity_tie_clarifies_with_grounded_options_e2e(
    orch, monkeypatch, record_requirement_coverage
):
    """Equal text matches with different warranties must resolve before D-10 answers."""
    orch.reset("d05-warranty-tie")
    product_ids = ("SH-AMB-201", "SH-AMB-202")
    products = [orch.container.product_repo.get(pid) for pid in product_ids]
    assert all(products)
    warranty_values = {
        pid: next(
            attr["value_raw"] for attr in orch.container.product_repo.attributes(pid)
            if attr["key_norm"] == "warranty"
        )
        for pid in product_ids
    }
    assert warranty_values[product_ids[0]] != warranty_values[product_ids[1]]

    real_search = orch.container.retriever.search_catalogue
    injected_candidates = tuple(
        Candidate(product_id=pid, term_coverage=1.0, fused_score=0.9)
        for pid in product_ids
    )
    intercepted = []
    entity_resolution_calls = []

    def search_with_equal_warranty_candidates(spec, turn_id):
        result = real_search(spec, turn_id)
        if spec.intent == Intent.PRODUCT_WARRANTY:
            intercepted.append((spec, turn_id))
            manifest = result.manifest.model_copy(update={
                "eligible_count": 2,
                "exact_match_count": 2,
                "returned_count": 2,
                "tied_count": 2,
            })
            return SearchResult(
                manifest=manifest,
                exact_ids=product_ids,
                ranked=injected_candidates,
                related=(),
            )
        return result

    real_resolve_entity = orch.container.retriever.resolve_entity

    def observe_entity_resolution(*args, **kwargs):
        entity_resolution_calls.append((args, kwargs))
        return real_resolve_entity(*args, **kwargs)

    monkeypatch.setattr(
        orch.container.retriever,
        "search_catalogue",
        search_with_equal_warranty_candidates,
    )
    monkeypatch.setattr(
        orch.container.retriever, "resolve_entity", observe_entity_resolution
    )
    resp = _say(orch, "d05-warranty-tie", "What warranty does the shoe have?")

    assert entity_resolution_calls
    assert entity_resolution_calls[0][0][0] == Intent.PRODUCT_WARRANTY
    assert len(intercepted) >= 2  # one entity resolution search and the normal retrieval search
    assert all(spec.entity_terms == ("warranty",) for spec, _ in intercepted)
    assert len(injected_candidates) == 2
    assert injected_candidates[0].term_coverage == injected_candidates[1].term_coverage
    assert injected_candidates[0].fused_score == injected_candidates[1].fused_score
    assert resp.decision == Decision.CLARIFY
    assert resp.clarification.reason == ClarifyReason.AMBIGUOUS_PRODUCT
    assert len(resp.clarification.options) == 2
    assert {option.product_id for option in resp.clarification.options} == set(product_ids)
    assert resp.claims == []
    assert resp.products == []
    assert not any(value in resp.answer_text for value in warranty_values.values())
    assert resp.metrics.validator_trips == 0

    evidence_by_id = {evidence.evidence_id: evidence for evidence in resp.evidence}
    for option in resp.clarification.options:
        product = orch.container.product_repo.get(option.product_id)
        assert product is not None
        title_evidence_id = f"prod:{product.product_id}:title"
        price_evidence_id = f"prod:{product.product_id}:price"
        assert set(option.evidence_ids) == {title_evidence_id, price_evidence_id}
        assert title_evidence_id in evidence_by_id
        assert price_evidence_id in evidence_by_id
        assert evidence_by_id[title_evidence_id].display_value == product.title
        assert evidence_by_id[price_evidence_id].display_value == product.price_display
        assert option.label == f"{product.title} — {product.price_display} ({product.product_id})"
    assert not any(
        evidence.evidence_id.endswith(":attr.warranty") for evidence in resp.evidence
    )

    record_requirement_coverage(
        "FI-D05-ambiguous-warranty-tie", "D-05",
        "D-05.ambiguous-warranty-tie",
        "clarify-with-grounded-options-no-warranty-claim-no-cards",
    )


def test_d10_unique_warranty_entity_match_still_answers_e2e(orch, monkeypatch):
    """A uniquely resolved warranty entity continues into the grounded D-10 answer."""
    orch.reset("d10-warranty-entity-unique")
    product_id = "SH-WARR-2Y"
    product = orch.container.product_repo.get(product_id)
    assert product is not None
    warranty = next(
        attr["value_raw"] for attr in orch.container.product_repo.attributes(product_id)
        if attr["key_norm"] == "warranty"
    )
    real_search = orch.container.retriever.search_catalogue

    def search_with_unique_warranty_candidate(spec, turn_id):
        result = real_search(spec, turn_id)
        if spec.intent != Intent.PRODUCT_WARRANTY:
            return result
        return SearchResult(
            manifest=result.manifest.model_copy(update={
                "eligible_count": 1,
                "exact_match_count": 1,
                "returned_count": 1,
                "tied_count": 0,
            }),
            exact_ids=(product_id,),
            ranked=(Candidate(product_id=product_id, term_coverage=1.0, fused_score=1.0),),
            related=(),
        )

    monkeypatch.setattr(
        orch.container.retriever,
        "search_catalogue",
        search_with_unique_warranty_candidate,
    )
    resp = _say(orch, "d10-warranty-entity-unique", "What warranty does the shoe have?")

    assert resp.decision == Decision.ANSWER
    warranty_claims = [claim for claim in resp.claims if claim.type == ClaimType.ATTRIBUTE]
    assert len(warranty_claims) == 1
    claim = warranty_claims[0]
    assert claim.product_id == product_id
    assert claim.value["key"] == "warranty"
    assert claim.value["value"] == warranty
    assert f"prod:{product_id}:attr.warranty" in claim.evidence_ids
    assert any(card.product_id == product_id for card in resp.products)
    assert resp.metrics.validator_trips == 0


def test_d10_focused_warranty_does_not_create_entity_tie_e2e(orch, monkeypatch):
    """An established focus remains the warranty subject instead of the generic term."""
    orch.reset("d10-warranty-focus")
    product_id = "SH-WARR-2Y"
    first = _say(orch, "d10-warranty-focus", f"price of {product_id}")
    assert first.decision == Decision.ANSWER

    resolve_calls = []
    real_resolve_entity = orch.container.retriever.resolve_entity

    def observe_resolution(*args, **kwargs):
        resolve_calls.append((args, kwargs))
        return real_resolve_entity(*args, **kwargs)

    monkeypatch.setattr(orch.container.retriever, "resolve_entity", observe_resolution)
    second = _say(orch, "d10-warranty-focus", "warranty of it")

    assert second.decision == Decision.ANSWER
    assert any(
        claim.type == ClaimType.ATTRIBUTE and claim.product_id == product_id
        for claim in second.claims
    )
    assert resolve_calls == []
    assert second.metrics.validator_trips == 0


# --------------------------------------------------------------------------
# D-04 — unconstrained browse reaches UNDER_SPECIFIED_QUERY (was SEARCH/ANSWER)
# --------------------------------------------------------------------------

def test_d04_under_specified_clarifies_e2e(orch):
    orch.reset("d04")
    resp = _say(orch, "d04", "i want to buy something")
    assert resp.decision == Decision.CLARIFY
    assert resp.clarification.reason == ClarifyReason.UNDER_SPECIFIED_QUERY
    assert resp.metrics.validator_trips == 0


@pytest.mark.requirement_coverage(test_id="FI-D04-vague-price-not-a-constraint")
def test_d04_approximate_price_only_clarifies_without_price_filter_e2e(
    orch, monkeypatch, record_requirement_coverage
):
    """Around/about amounts do not become filters or false product search terms."""
    real_search = orch.container.retriever.search_catalogue
    observed_specs = []

    def observe_search(spec, turn_id):
        observed_specs.append(spec)
        return real_search(spec, turn_id)

    monkeypatch.setattr(orch.container.retriever, "search_catalogue", observe_search)

    for cue in ("around", "about"):
        session = f"d04-vague-price-{cue}"
        orch.reset(session)
        message = f"{cue} ₹500"
        parsed = parse_query(
            message,
            view_has_focus=False,
            vocab=orch.container.vocab,
            settings=orch.container.settings,
            product_repo=orch.container.product_repo,
        )
        assert parsed.entity_terms == ()
        assert not [
            op for op in parsed.constraint_ops if op.key in {"price_min", "price_max"}
        ]
        assert any(
            item.kind == "PRICE_APPROXIMATION_IGNORED"
            and item.before == message
            for item in parsed.normalized.trace
        )

        response = _say(orch, session, message)

        assert response.decision == Decision.CLARIFY
        assert response.clarification is not None
        assert response.clarification.reason == ClarifyReason.UNDER_SPECIFIED_QUERY
        assert response.claims == []
        assert response.products == []
        assert response.active_constraints == []
        assert response.metrics.validator_trips == 0

        state = orch.container.sessions.get_or_create(session)
        assert state.constraints.is_empty()
        assert state.constraints.price_min is None
        assert state.constraints.price_max is None
        assert not state.constraints.categories

    assert len(observed_specs) == 2
    for spec in observed_specs:
        assert spec.entity_terms == ()
        assert spec.constraints.price_min is None
        assert spec.constraints.price_max is None
        assert not spec.constraints.categories

    record_requirement_coverage(
        "FI-D04-vague-price-not-a-constraint", "D-04",
        "D-04.non-constraint-only-input",
        "clarify-without-price-hard-filter",
    )


def test_d04_does_not_fire_for_a_real_constrained_search(orch):
    """Guard: a constrained browse must still ANSWER, not over-clarify."""
    orch.reset("d04b")
    resp = _say(orch, "d04b", "show me black shoes under 2000")
    assert resp.decision == Decision.ANSWER
    assert resp.products, "expected at least one product card"


# --------------------------------------------------------------------------
# D-06 — generic policy question clarifies, and the choice replays end-to-end
# --------------------------------------------------------------------------

def test_d06_generic_policy_clarifies_e2e(orch):
    orch.reset("d06")
    resp = _say(orch, "d06", "what's your policy?")
    assert resp.decision == Decision.CLARIFY
    assert resp.clarification.reason == ClarifyReason.AMBIGUOUS_POLICY_TOPIC
    topic_opts = [o for o in resp.clarification.options if o.kind == "POLICY_TOPIC"]
    assert len(topic_opts) >= 2
    assert resp.metrics.validator_trips == 0


def test_d06_clarification_choice_replays_to_policy_quote_e2e(orch):
    """Selecting a policy topic must resolve through the real pipeline to a quote."""
    orch.reset("d06r")
    first = _say(orch, "d06r", "what's your policy?")
    assert first.decision == Decision.CLARIFY
    chosen = next(o for o in first.clarification.options if o.kind == "POLICY_TOPIC")
    resolution = Resolution(
        clarification_id=first.clarification.clarification_id,
        option_id=chosen.option_id,
    )
    second = _say(orch, "d06r", "", resolution=resolution)
    assert second.decision == Decision.ANSWER
    assert any(c.type == ClaimType.POLICY_QUOTE for c in second.claims)
    assert second.metrics.validator_trips == 0


# --------------------------------------------------------------------------
# D-09 — determinable-but-unestablished policy topic refuses (no fabrication)
# --------------------------------------------------------------------------

def test_d09_policy_not_established_e2e(orch):
    orch.reset("d09")
    resp = _say(orch, "d09", "what is your price match policy")
    assert resp.decision == Decision.REFUSE
    assert resp.refusal.reason == RefusalReason.POLICY_NOT_ESTABLISHED
    # Must carry a policy-scope ABSENCE claim, not a fabricated answer.
    absence = [c for c in resp.claims
               if c.type == ClaimType.ABSENCE and c.scope == ClaimScope.GENERAL_POLICY]
    assert absence
    assert resp.metrics.validator_trips == 0


# --------------------------------------------------------------------------
# D-08 / D-08b / D-10 — null-field responses no longer trip G-AC-03
# --------------------------------------------------------------------------

def test_d08_null_price_refuses_field_missing_e2e(orch):
    orch.reset("d08")
    resp = _say(orch, "d08", "price of SH-NOP-001")
    assert resp.decision == Decision.REFUSE
    assert resp.refusal.reason == RefusalReason.FIELD_MISSING
    assert any(c.type == ClaimType.FIELD_UNAVAILABLE for c in resp.claims)
    assert resp.metrics.validator_trips == 0


def test_d08b_partial_fields_answers_with_rating_e2e(orch):
    orch.reset("d08b")
    resp = _say(orch, "d08b", "price and rating of SH-NOP-001")
    assert resp.decision == Decision.ANSWER
    assert any(c.product_id == "SH-NOP-001" for c in resp.claims)
    types = {c.type for c in resp.claims}
    assert ClaimType.RATING in types and ClaimType.FIELD_UNAVAILABLE in types
    assert resp.metrics.validator_trips == 0


def test_d10_warranty_silent_refuses_field_missing_e2e(orch):
    orch.reset("d10")
    resp = _say(orch, "d10", "warranty of CL-JNS-001")
    assert resp.decision == Decision.REFUSE
    assert resp.refusal.reason == RefusalReason.FIELD_MISSING
    assert resp.metrics.validator_trips == 0


# --------------------------------------------------------------------------
# D-07 — full-handler no-match paths and incomplete-search guard
# --------------------------------------------------------------------------

def _assert_grounded_catalogue_absence(resp):
    assert resp.decision == Decision.REFUSE
    assert resp.refusal.reason == RefusalReason.NO_CATALOGUE_MATCH
    absence = [claim for claim in resp.claims if claim.type == ClaimType.ABSENCE]
    assert len(absence) == 1
    assert absence[0].evidence_ids
    manifests = [e.search for e in resp.evidence if e.kind == "SEARCH_MANIFEST"]
    assert len(manifests) == 1
    manifest = manifests[0]
    assert manifest.completeness == Completeness.EXHAUSTIVE
    assert manifest.exact_match_count == 0
    assert resp.products == []
    assert resp.metrics.validator_trips == 0


def test_d07_unknown_explicit_id_refuses_with_grounded_absence_e2e(orch):
    orch.reset("d07-unknown-id")
    resp = _say(orch, "d07-unknown-id", "price of ZZZ-999")

    _assert_grounded_catalogue_absence(resp)
    assert "ZZZ-999" in resp.answer_text
    assert any("ZZZ-999" in str(claim.value) for claim in resp.claims)


def test_d07_entity_resolution_none_refuses_through_full_handler_e2e(orch, monkeypatch):
    orch.reset("d07-entity-none")
    real_resolve = orch.container.retriever.resolve_entity
    observed = {}

    def observe_resolution(*args, **kwargs):
        result = real_resolve(*args, **kwargs)
        observed["result"] = result
        return result

    monkeypatch.setattr(orch.container.retriever, "resolve_entity", observe_resolution)
    resp = _say(orch, "d07-entity-none", "price of qzxvplmno")

    assert observed["result"].kind == "NONE"
    _assert_grounded_catalogue_absence(resp)
    assert "qzxvplmno" in resp.answer_text


def test_d07_search_zero_verified_refuses_and_incomplete_search_stays_nondefinitive_e2e(
    orch, monkeypatch
):
    orch.reset("d07-search-zero")
    resp = _say(orch, "d07-search-zero", "show me qzxvplmno")
    _assert_grounded_catalogue_absence(resp)
    assert "qzxvplmno" in resp.answer_text

    orch.reset("d07-incomplete-zero")
    real_search = orch.container.retriever.search_catalogue

    def return_incomplete_zero(spec, turn_id):
        result = real_search(spec, turn_id)
        assert result.exact_ids == ()
        manifest = result.manifest.model_copy(update={
            "completeness": Completeness.INCOMPLETE,
            "incomplete_reasons": ["INJECTED_TEST_INCOMPLETE"],
        })
        return replace(result, manifest=manifest)

    monkeypatch.setattr(orch.container.retriever, "search_catalogue", return_incomplete_zero)
    incomplete = _say(orch, "d07-incomplete-zero", "show me qzxvplmno")
    assert incomplete.decision == Decision.REFUSE
    assert incomplete.refusal.reason == RefusalReason.SEARCH_INCOMPLETE
    assert not any(claim.type == ClaimType.ABSENCE for claim in incomplete.claims)
    assert incomplete.products == []
    assert incomplete.metrics.validator_trips == 0
    assert any(
        e.kind == "SEARCH_MANIFEST"
        and e.search.completeness == Completeness.INCOMPLETE
        and e.search.exact_match_count == 0
        for e in incomplete.evidence
    )


# --------------------------------------------------------------------------
# Regression guards: previously-working routes must be untouched
# --------------------------------------------------------------------------

def test_d16_established_policy_still_answers_e2e(orch):
    orch.reset("d16")
    resp = _say(orch, "d16", "what is the shipping time")
    assert resp.decision == Decision.ANSWER
    assert any(c.type == ClaimType.POLICY_QUOTE for c in resp.claims)
    assert resp.metrics.validator_trips == 0


def test_d11_unsupported_action_still_refuses_e2e(orch):
    orch.reset("d11")
    resp = _say(orch, "d11", "track my order")
    assert resp.decision == Decision.REFUSE
    assert resp.refusal.reason == RefusalReason.OUT_OF_SCOPE


def _assert_d11_fact_free_refusal(resp, *, reason, answer_text):
    assert resp.decision == Decision.REFUSE
    assert resp.refusal.reason == reason
    assert resp.answer_text == answer_text
    assert resp.claims == []
    assert resp.evidence == []
    assert resp.products == []
    assert resp.metrics.validator_trips == 0


def test_d11_placing_order_is_fact_free_refusal_e2e(orch):
    orch.reset("d11-place-order")
    resp = _say(orch, "d11-place-order", "Please place an order for SH-BLK-001")
    _assert_d11_fact_free_refusal(
        resp,
        reason=RefusalReason.UNSUPPORTED_REQUEST,
        answer_text="I can't place orders, process payments or track shipments.",
    )


def test_d11_processing_payment_is_fact_free_refusal_e2e(orch):
    orch.reset("d11-process-payment")
    resp = _say(orch, "d11-process-payment", "Please process a payment for SH-BLK-001")
    _assert_d11_fact_free_refusal(
        resp,
        reason=RefusalReason.UNSUPPORTED_REQUEST,
        answer_text="I can't place orders, process payments or track shipments.",
    )


def test_d11_cancel_order_preempts_policy_routing_e2e(orch, monkeypatch):
    orch.reset("d11-cancel-order")
    real_policy_search = orch.container.policy_retriever.search
    policy_calls = []

    def observe_policy_search(*args, **kwargs):
        policy_calls.append((args, kwargs))
        return real_policy_search(*args, **kwargs)

    monkeypatch.setattr(orch.container.policy_retriever, "search", observe_policy_search)
    resp = _say(orch, "d11-cancel-order", "Cancel my order for SH-BLK-001")
    _assert_d11_fact_free_refusal(
        resp,
        reason=RefusalReason.UNSUPPORTED_REQUEST,
        answer_text="I can only help with products in this catalogue and its returns, shipping and warranty policy.",
    )
    assert policy_calls == []


def test_d11_personalized_advice_is_fact_free_refusal_e2e(orch):
    orch.reset("d11-personal-advice")
    resp = _say(
        orch,
        "d11-personal-advice",
        "Give me personalized advice about which product is right for me",
    )
    _assert_d11_fact_free_refusal(
        resp,
        reason=RefusalReason.UNSUPPORTED_REQUEST,
        answer_text="I can only help with products in this catalogue and its returns, shipping and warranty policy.",
    )


def test_budget_and_stock_multi_turn_constraints_enforced_e2e(orch):
    """Phase-4 safety: inherited multi-turn constraints remain hard filters."""
    orch.reset("carry")
    first = _say(orch, "carry", "show me black shoes under 2000")
    assert first.decision == Decision.ANSWER
    second = _say(orch, "carry", "only in stock")
    assert second.decision == Decision.ANSWER
    assert second.products
    assert all(c.product_id not in {"SH-BLK-004", "SH-TRK-001", "SH-BLK-005"}
               for c in second.products)


# --------------------------------------------------------------------------
# D-14 DESCRIPTION_ONLY / NF-14 -- existence lead-in over description mentions
# --------------------------------------------------------------------------

def test_d14_description_only_existence_answers_with_quote_e2e(orch):
    orch.reset("d14")
    resp = _say(orch, "d14", "do you sell memory foam")
    assert resp.decision == Decision.ANSWER
    assert resp.metrics.validator_trips == 0
    # non-factual NF-14 lead-in present verbatim
    assert "No product is titled this, but a description mentions it:" in resp.answer_text
    # every factual assertion is a grounded DESCRIPTION_QUOTE; no existence/count claim
    assert resp.claims and all(c.type == ClaimType.DESCRIPTION_QUOTE for c in resp.claims)
    assert not any(
        c.type in (ClaimType.PRODUCT_EXISTS, ClaimType.RESULT_COUNT) for c in resp.claims
    )
    # each quote cites real description evidence that resolves in the bundle
    ev_ids = {e.evidence_id for e in resp.evidence}
    for c in resp.claims:
        assert f"prod:{c.product_id}:description" in c.evidence_ids
        assert f"prod:{c.product_id}:description" in ev_ids


def test_d14_does_not_fire_when_a_title_matches_e2e(orch):
    """Guard: a term that appears in a product title stays on the normal path --
    no NF-14 lead-in and no description-only quote are produced."""
    orch.reset("d14b")
    resp = _say(orch, "d14b", "do you sell rfid")
    assert "No product is titled this" not in resp.answer_text
    assert not any(c.type == ClaimType.DESCRIPTION_QUOTE for c in resp.claims)
    assert resp.metrics.validator_trips == 0


# --------------------------------------------------------------------------
# D-12 / D-17 -- injected post-condition failures reach the service response
# --------------------------------------------------------------------------

@pytest.mark.requirement_coverage(test_id="FI-D12-validator-rejection")
def test_d12_validator_rejection_becomes_fact_free_refusal_e2e(
    orch, monkeypatch, record_requirement_coverage
):
    """An injected rejection follows Orchestrator.handle's production D-12 path."""
    orch.reset("d12-injected")
    real_validate = orch.container.validator.validate

    def reject_otherwise_valid_draft(draft, digest):
        real_report = real_validate(draft, digest)
        assert real_report.ok, "the injected request must be valid before fault injection"
        return ValidationReport(
            ok=False,
            violations=(Violation(rule="V-01", claim_id=None, detail="injected rejection"),),
        )

    monkeypatch.setattr(
        orch.container.validator, "validate", reject_otherwise_valid_draft
    )
    resp = _say(orch, "d12-injected", "price of SH-BLK-001")

    assert resp.decision == Decision.REFUSE
    assert resp.refusal.reason == RefusalReason.INTERNAL_VALIDATION_FAILURE
    assert resp.answer_text == "I couldn't produce a verified answer to that. Please try rephrasing."
    assert resp.claims == []
    assert resp.evidence == []
    assert resp.products == []
    assert "SH-BLK-001" not in resp.answer_text
    assert resp.metrics.validator_trips == 1
    assert "validate" in resp.metrics.stages
    record_requirement_coverage(
        "FI-D12-validator-rejection", "D-12",
        "D-12.validator-rejection-refusal", "fact-free-refusal-trip-once",
    )


@pytest.mark.requirement_coverage(test_id="FI-D17-all-claims-unusable")
def test_d17_unsupported_planned_claim_refuses_through_pipeline_e2e(
    orch, monkeypatch, record_requirement_coverage
):
    """Inject at the core-planning seam, retaining production D-17 enforcement."""
    orch.reset("d17-injected")
    real_core = decision_engine._decide_core
    real_enforcer = decision_engine.enforce_claim_grounding
    observed = {}

    def plan_without_evidence(ctx):
        plan = real_core(ctx)
        assert plan.decision == Decision.ANSWER
        assert plan.claim_specs
        unsupported = replace(
            plan,
            claim_specs=tuple(replace(claim, evidence_ids=()) for claim in plan.claim_specs),
        )
        observed["injected_plan"] = unsupported
        return unsupported

    def observe_production_enforcement(plan):
        observed["before_enforcement"] = plan
        result = real_enforcer(plan)
        observed["after_enforcement"] = result
        return result

    monkeypatch.setattr(decision_engine, "_decide_core", plan_without_evidence)
    monkeypatch.setattr(
        decision_engine, "enforce_claim_grounding", observe_production_enforcement
    )
    resp = _say(orch, "d17-injected", "price of SH-BLK-001")

    assert observed["injected_plan"].claim_specs
    assert all(not claim.evidence_ids for claim in observed["injected_plan"].claim_specs)
    enforced = observed["after_enforcement"]
    assert enforced.decision == Decision.REFUSE
    assert enforced.reason == RefusalReason.INSUFFICIENT_EVIDENCE
    assert enforced.claim_specs == ()
    assert enforced.card_specs == ()
    assert resp.decision == Decision.REFUSE
    assert resp.refusal.reason == RefusalReason.INSUFFICIENT_EVIDENCE
    assert resp.answer_text == "I couldn't produce a verified answer to that. Please try rephrasing."
    assert resp.claims == []
    assert resp.evidence == []
    assert resp.products == []
    assert "SH-BLK-001" not in resp.answer_text
    assert resp.metrics.validator_trips == 0
    record_requirement_coverage(
        "FI-D17-all-claims-unusable", "D-17",
        "D-17.all-claims-unusable-refusal", "fact-free-insufficient-evidence-refusal",
    )


@pytest.mark.requirement_coverage(test_id="FI-D17-null-value-claims")
def test_d17_null_value_claims_refuse_through_pipeline_e2e(
    orch, monkeypatch, record_requirement_coverage
):
    """Null-valued planned claims are removed by the real D-17 post-condition."""
    orch.reset("d17-null-value")
    real_core = decision_engine._decide_core
    real_enforcer = decision_engine.enforce_claim_grounding
    observed = {}

    def plan_with_null_values(ctx):
        plan = real_core(ctx)
        assert plan.decision == Decision.ANSWER
        assert plan.claim_specs
        injected = replace(
            plan,
            claim_specs=tuple(replace(claim, value=None) for claim in plan.claim_specs),
        )
        observed["injected_plan"] = injected
        return injected

    def observe_production_enforcement(plan):
        observed["before_enforcement"] = plan
        result = real_enforcer(plan)
        observed["after_enforcement"] = result
        return result

    monkeypatch.setattr(decision_engine, "_decide_core", plan_with_null_values)
    monkeypatch.setattr(
        decision_engine, "enforce_claim_grounding", observe_production_enforcement
    )
    resp = _say(orch, "d17-null-value", "price of SH-BLK-001")

    assert observed["injected_plan"].claim_specs
    assert all(claim.value is None for claim in observed["injected_plan"].claim_specs)
    enforced = observed["after_enforcement"]
    assert enforced.decision == Decision.REFUSE
    assert enforced.reason == RefusalReason.INSUFFICIENT_EVIDENCE
    assert enforced.claim_specs == ()
    assert enforced.card_specs == ()
    assert resp.decision == Decision.REFUSE
    assert resp.refusal.reason == RefusalReason.INSUFFICIENT_EVIDENCE
    assert resp.claims == []
    assert resp.evidence == []
    assert resp.products == []
    assert "SH-BLK-001" not in resp.answer_text
    assert resp.metrics.validator_trips == 0
    record_requirement_coverage(
        "FI-D17-null-value-claims", "D-17",
        "D-17.missing-value", "fact-free-insufficient-evidence-refusal",
    )


@pytest.mark.requirement_coverage(test_id="FI-D17-mixed-claim-card-filter")
def test_d17_mixed_claims_remove_only_unsupported_product_card_e2e(
    orch, monkeypatch, record_requirement_coverage
):
    """Cards follow surviving product claims through the real response pipeline."""
    orch.reset("d17-mixed-card")
    real_core = decision_engine._decide_core
    real_enforcer = decision_engine.enforce_claim_grounding
    observed = {}

    def plan_with_one_unsupported_product(ctx):
        plan = real_core(ctx)
        assert plan.decision == Decision.ANSWER
        summaries = [
            claim for claim in plan.claim_specs
            if claim.type == ClaimType.PRODUCT_SUMMARY and claim.product_id
        ]
        assert len(summaries) >= 2
        supported = summaries[0]
        rejected = replace(summaries[1], evidence_ids=())
        injected = replace(
            plan,
            claim_specs=(supported, rejected),
            segments=(SegmentClaim(supported.claim_id), SegmentClaim(rejected.claim_id)),
        )
        observed["supported_product_id"] = supported.product_id
        observed["rejected_product_id"] = rejected.product_id
        observed["injected_plan"] = injected
        return injected

    def observe_production_enforcement(plan):
        observed["before_enforcement"] = plan
        result = real_enforcer(plan)
        observed["after_enforcement"] = result
        return result

    monkeypatch.setattr(decision_engine, "_decide_core", plan_with_one_unsupported_product)
    monkeypatch.setattr(
        decision_engine, "enforce_claim_grounding", observe_production_enforcement
    )
    resp = _say(orch, "d17-mixed-card", "show me black shoes under 2000")

    injected = observed["injected_plan"]
    supported_id = observed["supported_product_id"]
    rejected_id = observed["rejected_product_id"]
    rejected_claim = next(
        claim for claim in observed["injected_plan"].claim_specs
        if claim.product_id == rejected_id
    )
    rejected_title = rejected_claim.value.get("title")
    assert supported_id != rejected_id
    assert any(c.product_id == supported_id and c.evidence_ids for c in injected.claim_specs)
    assert any(c.product_id == rejected_id and not c.evidence_ids for c in injected.claim_specs)
    assert {card.product_id for card in injected.card_specs} >= {supported_id, rejected_id}

    enforced = observed["after_enforcement"]
    assert enforced.decision == Decision.ANSWER
    assert [claim.claim_id for claim in enforced.claim_specs] == [
        next(c.claim_id for c in injected.claim_specs if c.product_id == supported_id)
    ]
    assert [segment.claim_id for segment in enforced.segments if isinstance(segment, SegmentClaim)] == [
        next(c.claim_id for c in injected.claim_specs if c.product_id == supported_id)
    ]
    assert {card.product_id for card in enforced.card_specs} == {supported_id}

    assert resp.decision == Decision.ANSWER
    assert {claim.product_id for claim in resp.claims} == {supported_id}
    assert {product.product_id for product in resp.products} == {supported_id}
    assert rejected_id not in resp.answer_text
    assert not rejected_title or rejected_title not in resp.answer_text
    assert all(
        getattr(evidence, "product_id", None) != rejected_id
        for evidence in resp.evidence
    )
    assert resp.metrics.validator_trips == 0
    record_requirement_coverage(
        "FI-D17-mixed-claim-card-filter", "D-17",
        "D-17.mixed-valid-invalid-claims",
        "invalid-claim-and-card-removed-valid-claim-and-card-preserved",
    )
