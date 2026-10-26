"""
Dedicated unit tests for the decision engine rules D-01..D-17 (G-AC-01).

These tests exercise ``app.decision.engine.decide`` in isolation: they feed a
``DecisionContext`` built from synthetic retrieval outcomes (ParsedQuery,
MergeResult, SearchResult, EntityResolution, PolicyResult) plus the trusted
ProductRepo, and assert on the resulting ``DecisionPlan`` (decision type, reason
code, clarification, refusal, claim handling, segments).

Determinism: no network calls, no paid APIs, no LLM. Rules that look up a
concrete product use real, explicitly-verified catalogue ids (asserted present
first) so the assertions reflect rule behaviour rather than accidental sample
alignment. D-17 is additionally tested directly through ``enforce_claim_grounding``
because its drop/refuse post-condition cannot be produced by the normal planner.
"""
from __future__ import annotations

import pytest

from app.container import build_container
from app.conversation.merger import MergeResult
from app.conversation.state import ConversationState
from app.decision.engine import DecisionContext, decide, enforce_claim_grounding
from app.domain.constraints import Conflict, ConstraintSet, PriceBound
from app.domain.enums import (
    CardOrigin,
    ClaimScope,
    ClaimType,
    ClarifyReason,
    Completeness,
    Decision,
    Intent,
    RefusalReason,
    RequestedField,
    WarningCode,
)
from app.domain.parsed_query import NormalizedText, ParsedQuery
from app.domain.plans import (
    Candidate,
    ClaimSpec,
    EntityResolution,
    PolicyResult,
    PolicySection,
    SearchResult,
    SegmentClaim,
    SegmentNF,
)
from app.schemas.chat import ConstraintOpIn, ConstraintView
from app.schemas.evidence import CatalogueSearchManifest, PolicySearchManifest, Relaxation
from app.settings import load_settings


# ---------------------------------------------------------------------------
# Fixtures & builders
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def settings():
    return load_settings()


@pytest.fixture(scope="module")
def container(settings):
    return build_container(settings)


@pytest.fixture(scope="module")
def repo(container):
    return container.product_repo


def pq(message: str = "", intent: Intent = Intent.SEARCH, **kw) -> ParsedQuery:
    return ParsedQuery(
        original=message,
        normalized=NormalizedText(original=message, text=message),
        intent=intent,
        **kw,
    )


def mr(constraints: ConstraintSet | None = None, conflicts=(), carried_over=False) -> MergeResult:
    return MergeResult(
        constraints=constraints or ConstraintSet(),
        conflicts=tuple(conflicts),
        carried_over=carried_over,
    )


def cat_manifest(turn_id=1, exact=0, completeness=Completeness.EXHAUSTIVE,
                 relaxations=None, applied=None) -> CatalogueSearchManifest:
    return CatalogueSearchManifest(
        search_id=f"s_{turn_id}",
        intent=Intent.SEARCH,
        query_text="shoes",
        completeness=completeness,
        eligible_count=exact,
        exact_match_count=exact,
        returned_count=exact,
        relaxations=relaxations or [],
        applied_constraints=applied or [],
    )


def policy_manifest(turn_id=1, topic=None, matched=None) -> PolicySearchManifest:
    return PolicySearchManifest(
        search_id=f"p_{turn_id}",
        topic=topic,
        matched_section_ids=matched or [],
        sections_checked=["1", "2", "3"],
    )


def make_ctx(parsed, repo, settings, *, merge=None, state=None, search=None,
             entity=None, policy=None, turn_id=1) -> DecisionContext:
    return DecisionContext(
        parsed=parsed,
        state_before=state or ConversationState(session_id="dec-test"),
        merge_res=merge or mr(),
        search=search,
        entity=entity,
        id_lookup=None,
        policy=policy,
        turn_id=turn_id,
        settings=settings,
        product_repo=repo,
    )


def claim(cid, ctype, value, evidence):
    return ClaimSpec(
        claim_id=cid, type=ctype, scope=ClaimScope.CATALOGUE,
        product_id=None, value=value, evidence_ids=tuple(evidence),
    )


def answer_plan(claims):
    from app.domain.plans import DecisionPlan, StateUpdate
    return DecisionPlan(
        decision=Decision.ANSWER,
        intent=Intent.SEARCH,
        reason=None,
        segments=tuple([SegmentNF(kind="NF-11")] + [SegmentClaim(c.claim_id) for c in claims]),
        claim_specs=tuple(claims),
        card_specs=(),
        clarification=None,
        suggestions=(),
        manifests=(),
        constraints_after=ConstraintSet(),
        state_update=StateUpdate(),
    )


def nonfactual_plan():
    from app.domain.plans import DecisionPlan, StateUpdate
    return DecisionPlan(
        decision=Decision.ANSWER,
        intent=Intent.GREETING_HELP,
        reason=None,
        segments=(SegmentNF(kind="NF-01"),),
        claim_specs=(),
        card_specs=(),
        clarification=None,
        suggestions=(),
        manifests=(),
        constraints_after=ConstraintSet(),
        state_update=StateUpdate(),
    )


# ---------------------------------------------------------------------------
# D-01 — GREETING_HELP / RESET
# ---------------------------------------------------------------------------

class TestD01NonFactual:
    def test_d01_greeting_answers_with_nonfactual_only(self, repo, settings):
        plan = decide(make_ctx(pq("hello", intent=Intent.GREETING_HELP), repo, settings))
        assert plan.decision == Decision.ANSWER
        assert plan.claim_specs == ()
        assert any(isinstance(s, SegmentNF) and s.kind == "NF-01" for s in plan.segments)

    def test_d01_reset_clears_state_and_constraints(self, repo, settings):
        st = ConversationState(session_id="r", focus_product_id="SH-BLK-001",
                               last_result_ids=("SH-BLK-001",),
                               constraints=ConstraintSet(price_max=PriceBound(200000, False)))
        plan = decide(make_ctx(pq("reset", intent=Intent.RESET), repo, settings, state=st))
        assert plan.decision == Decision.ANSWER
        assert plan.constraints_after.is_empty()
        assert plan.state_update.focus_product_id is None
        assert plan.state_update.constraints_to_persist.is_empty()
        assert any(isinstance(s, SegmentNF) and s.kind == "NF-03" for s in plan.segments)


# ---------------------------------------------------------------------------
# D-02 — CONTRADICTORY_CONSTRAINTS
# ---------------------------------------------------------------------------

class TestD02Contradiction:
    def test_d02_conflict_clarifies_with_two_options(self, repo, settings):
        conflict = Conflict(key="price_max", existing_value="Under 500", new_value="Over 1000",
                            reason="empty price range")
        plan = decide(make_ctx(
            pq("shoes", intent=Intent.SEARCH), repo, settings,
            merge=mr(conflicts=(conflict,)),
        ))
        assert plan.decision == Decision.CLARIFY
        assert plan.reason == ClarifyReason.CONTRADICTORY_CONSTRAINTS
        assert plan.clarification is not None
        assert len(plan.clarification.options) == 2
        assert plan.claim_specs == ()


# ---------------------------------------------------------------------------
# D-03 — UNRESOLVED_REFERENCE
# ---------------------------------------------------------------------------

class TestD03UnresolvedReference:
    def test_d03_no_history_open_question(self, repo, settings):
        plan = decide(make_ctx(
            pq("what is the price of it", intent=Intent.PRODUCT_FACT, references_focus=True),
            repo, settings,
        ))
        assert plan.decision == Decision.CLARIFY
        assert plan.reason == ClarifyReason.UNRESOLVED_REFERENCE
        assert any(isinstance(s, SegmentNF) and s.kind == "NF-05" for s in plan.segments)

    def test_d03_with_history_offers_last_results(self, repo, settings):
        assert repo.get("SH-BLK-001") is not None
        st = ConversationState(session_id="d3", focus_product_id=None,
                               last_result_ids=("SH-BLK-001", "SH-BLK-002"))
        plan = decide(make_ctx(
            pq("is it in stock", intent=Intent.PRODUCT_FACT, references_focus=True),
            repo, settings, state=st,
        ))
        assert plan.reason == ClarifyReason.UNRESOLVED_REFERENCE
        opts = {o.product_id for o in plan.clarification.options}
        assert {"SH-BLK-001", "SH-BLK-002"} <= opts
        # every option is evidenced so its label can be validated
        for o in plan.clarification.options:
            assert o.evidence_ids


# ---------------------------------------------------------------------------
# D-04 — UNDER_SPECIFIED_QUERY
# ---------------------------------------------------------------------------

class TestD04UnderSpecified:
    def test_d04_empty_query_clarifies(self, repo, settings):
        search = SearchResult(manifest=cat_manifest(), exact_ids=(), ranked=(), related=())
        plan = decide(make_ctx(
            pq("", intent=Intent.SEARCH), repo, settings,
            merge=mr(ConstraintSet()), search=search,
        ))
        assert plan.decision == Decision.CLARIFY
        assert plan.reason == ClarifyReason.UNDER_SPECIFIED_QUERY
        assert any(isinstance(s, SegmentNF) and s.kind == "NF-06" for s in plan.segments)

    def test_d04_not_fired_when_a_term_is_present(self, repo, settings):
        search = SearchResult(manifest=cat_manifest(exact=1), exact_ids=("SH-BLK-001",),
                              ranked=(Candidate("SH-BLK-001"),), related=())
        plan = decide(make_ctx(
            pq("black shoes", intent=Intent.SEARCH, entity_terms=("shoes",)), repo, settings,
            merge=mr(ConstraintSet(price_max=PriceBound(200000, False))), search=search,
        ))
        assert plan.reason != ClarifyReason.UNDER_SPECIFIED_QUERY


# ---------------------------------------------------------------------------
# D-05 — AMBIGUOUS_PRODUCT
# ---------------------------------------------------------------------------

class TestD05AmbiguousProduct:
    def test_d05_ties_clarify_with_labelled_options(self, repo, settings):
        assert repo.get("SH-BLK-001") and repo.get("SH-BLK-002")
        entity = EntityResolution(
            kind="AMBIGUOUS",
            candidates=(Candidate("SH-BLK-001"), Candidate("SH-BLK-002")),
            manifest=cat_manifest(),
        )
        plan = decide(make_ctx(
            pq("blue shoe", intent=Intent.PRODUCT_FACT, entity_terms=("shoe",)),
            repo, settings, entity=entity,
        ))
        assert plan.decision == Decision.CLARIFY
        assert plan.reason == ClarifyReason.AMBIGUOUS_PRODUCT
        assert len(plan.clarification.options) == 2
        for o in plan.clarification.options:
            assert o.kind == "PRODUCT"
            assert o.product_id in {"SH-BLK-001", "SH-BLK-002"}
        assert any(isinstance(s, SegmentNF) and s.kind == "NF-04" for s in plan.segments)

    def test_d05_options_capped_at_five(self, repo, settings):
        ids = [p for p in ("SH-BLK-001", "SH-BLK-002", "SH-BLK-003", "SH-BLK-004", "SH-BLK-005")]
        cands = tuple(Candidate(pid) for pid in ids + ["SH-BLK-001", "SH-BLK-002"])
        entity = EntityResolution(kind="AMBIGUOUS", candidates=cands, manifest=cat_manifest())
        plan = decide(make_ctx(
            pq("shoe", intent=Intent.PRODUCT_FACT, entity_terms=("shoe",)),
            repo, settings, entity=entity,
        ))
        assert len(plan.clarification.options) <= 5


# ---------------------------------------------------------------------------
# D-06 — AMBIGUOUS_POLICY_TOPIC
# ---------------------------------------------------------------------------

class TestD06AmbiguousPolicyTopic:
    def _policy(self, *, established=False, topics=("returns", "shipping", "warranty"),
                sections=()):
        return PolicyResult(
            manifest=policy_manifest(),
            sections=sections,
            established=established,
            available_topics=tuple(topics),
        )

    def test_d06_generic_question_clarifies_topics(self, repo, settings):
        plan = decide(make_ctx(
            pq("what's your policy?", intent=Intent.POLICY, policy_topic=None),
            repo, settings, policy=self._policy(),
        ))
        assert plan.decision == Decision.CLARIFY
        assert plan.reason == ClarifyReason.AMBIGUOUS_POLICY_TOPIC
        kinds = {o.kind for o in plan.clarification.options}
        assert kinds == {"POLICY_TOPIC"}
        assert {o.policy_topic for o in plan.clarification.options} == {"returns", "shipping", "warranty"}
        assert plan.claim_specs == ()
        assert any(isinstance(s, SegmentNF) and s.kind == "NF-08" for s in plan.segments)

    def test_d06_does_not_refuse_when_clarification_is_appropriate(self, repo, settings):
        # Same not-established outcome, but a generic (topic-less) question must
        # NOT be classified as unsupported (D-09); it must clarify (D-06).
        plan = decide(make_ctx(
            pq("tell me about policies", intent=Intent.POLICY, policy_topic=None),
            repo, settings, policy=self._policy(),
        ))
        assert plan.reason != RefusalReason.POLICY_NOT_ESTABLISHED

    def test_d06_falls_back_to_refuse_when_no_topics_exist(self, repo, settings):
        plan = decide(make_ctx(
            pq("what's your policy?", intent=Intent.POLICY, policy_topic=None),
            repo, settings, policy=self._policy(topics=()),
        ))
        assert plan.decision == Decision.REFUSE
        assert plan.reason == RefusalReason.POLICY_NOT_ESTABLISHED

    def test_d06_does_not_fire_when_topic_is_determinable(self, repo, settings):
        # A specific but unsupported topic (returns/shipping/etc. determinable)
        # stays D-09, not D-06.
        plan = decide(make_ctx(
            pq("is there a restocking fee", intent=Intent.POLICY, policy_topic="shipping"),
            repo, settings, policy=self._policy(),
        ))
        assert plan.decision == Decision.REFUSE
        assert plan.reason == RefusalReason.POLICY_NOT_ESTABLISHED


# ---------------------------------------------------------------------------
# D-07 — NO_CATALOGUE_MATCH
# ---------------------------------------------------------------------------

class TestD07NoCatalogueMatch:
    def test_d07_explicit_id_missing(self, repo, settings):
        assert repo.get("ZZZ-999") is None
        plan = decide(make_ctx(
            pq("price of ZZZ-999", intent=Intent.PRODUCT_FACT, product_ids=("ZZZ-999",)),
            repo, settings,
            merge=mr(ConstraintSet()),
        ))
        assert plan.decision == Decision.REFUSE
        assert plan.reason == RefusalReason.NO_CATALOGUE_MATCH
        absence = [c for c in plan.claim_specs if c.type == ClaimType.ABSENCE]
        assert len(absence) == 1
        assert absence[0].evidence_ids  # grounded by a zero-result manifest

    def test_d07_search_zero_exhaustive_with_suggestions(self, repo, settings):
        rel = Relaxation(dropped="stock", label="In stock only", exact_match_count=7)
        search = SearchResult(
            manifest=cat_manifest(exact=0, relaxations=[rel]),
            exact_ids=(), ranked=(), related=(),
        )
        plan = decide(make_ctx(
            pq("quantum widgets", intent=Intent.SEARCH, entity_terms=("quantum", "widgets")),
            repo, settings, merge=mr(ConstraintSet()), search=search,
        ))
        assert plan.reason == RefusalReason.NO_CATALOGUE_MATCH
        assert any(c.type == ClaimType.ABSENCE for c in plan.claim_specs)
        assert len(plan.suggestions) == 1
        assert plan.suggestions[0][1][0].key == "stock"

    def test_d07_absence_requires_exhaustive(self, repo, settings):
        # An INCOMPLETE zero-result search must NOT yield a definitive absence.
        search = SearchResult(
            manifest=cat_manifest(exact=0, completeness=Completeness.INCOMPLETE),
            exact_ids=(), ranked=(), related=(),
        )
        plan = decide(make_ctx(
            pq("quantum widgets", intent=Intent.SEARCH, entity_terms=("quantum", "widgets")),
            repo, settings, merge=mr(ConstraintSet()), search=search,
        ))
        assert plan.reason == RefusalReason.SEARCH_INCOMPLETE
        assert all(c.type != ClaimType.ABSENCE for c in plan.claim_specs)


# ---------------------------------------------------------------------------
# D-08 / D-08b — FIELD_MISSING
# ---------------------------------------------------------------------------

class TestD08FieldMissing:
    def test_d08_all_requested_fields_missing_refuses(self, repo, settings):
        p = repo.get("SH-NOP-001")
        assert p is not None and p.price_minor is None  # null-price product
        plan = decide(make_ctx(
            pq("price of SH-NOP-001", intent=Intent.PRODUCT_FACT,
               product_ids=("SH-NOP-001",), requested_fields=(RequestedField.PRICE,)),
            repo, settings, merge=mr(ConstraintSet()),
        ))
        assert plan.decision == Decision.REFUSE
        assert plan.reason == RefusalReason.FIELD_MISSING
        assert [c.type for c in plan.claim_specs] == [ClaimType.FIELD_UNAVAILABLE]

    def test_d08b_some_fields_available_answers(self, repo, settings):
        p = repo.get("SH-NOP-001")
        assert p.price_minor is None and p.category  # price missing, category present
        plan = decide(make_ctx(
            pq("price and category of SH-NOP-001", intent=Intent.PRODUCT_FACT,
               product_ids=("SH-NOP-001",),
               requested_fields=(RequestedField.PRICE, RequestedField.CATEGORY)),
            repo, settings, merge=mr(ConstraintSet()),
        ))
        assert plan.decision == Decision.ANSWER
        assert plan.reason is None
        types = {c.type for c in plan.claim_specs}
        assert ClaimType.FIELD_UNAVAILABLE in types
        assert ClaimType.CATEGORY in types


# ---------------------------------------------------------------------------
# D-09 — POLICY_NOT_ESTABLISHED
# ---------------------------------------------------------------------------

class TestD09PolicyNotEstablished:
    def test_d09_coverage_fails_for_determinate_topic(self, repo, settings):
        pol = PolicyResult(manifest=policy_manifest(topic="shipping"),
                           established=False, available_topics=("returns",))
        plan = decide(make_ctx(
            pq("what is the delivery time to Srinagar", intent=Intent.POLICY, policy_topic="shipping"),
            repo, settings, policy=pol,
        ))
        assert plan.decision == Decision.REFUSE
        assert plan.reason == RefusalReason.POLICY_NOT_ESTABLISHED
        absence = [c for c in plan.claim_specs if c.type == ClaimType.ABSENCE]
        assert absence and absence[0].scope == ClaimScope.GENERAL_POLICY


# ---------------------------------------------------------------------------
# D-10 — PRODUCT_WARRANTY (both named behaviours)
# ---------------------------------------------------------------------------

class TestD10Warranty:
    def test_d10_structured_warranty_answers(self, repo, settings):
        p = repo.get("SH-BLK-001")
        assert p is not None
        assert any(a["key_norm"] == "warranty" for a in repo.attributes(p.product_id))
        plan = decide(make_ctx(
            pq("does SH-BLK-001 have warranty", intent=Intent.PRODUCT_WARRANTY,
               product_ids=("SH-BLK-001",)),
            repo, settings, merge=mr(ConstraintSet()),
        ))
        assert plan.decision == Decision.ANSWER
        wclaim = [c for c in plan.claim_specs if c.type == ClaimType.ATTRIBUTE]
        assert wclaim and wclaim[0].value["key"] == "warranty"
        assert wclaim[0].scope == ClaimScope.PRODUCT
        assert plan.card_specs and plan.card_specs[0].origin == CardOrigin.LOOKUP

    def test_d10_product_silent_refuses_field_missing(self, repo, settings):
        p = repo.get("CL-JNS-001")
        assert p is not None
        assert not any(a["key_norm"] == "warranty" for a in repo.attributes(p.product_id))
        plan = decide(make_ctx(
            pq("warranty of CL-JNS-001", intent=Intent.PRODUCT_WARRANTY,
               product_ids=("CL-JNS-001",)),
            repo, settings, merge=mr(ConstraintSet()),
        ))
        assert plan.decision == Decision.REFUSE
        assert plan.reason == RefusalReason.FIELD_MISSING
        assert [c.type for c in plan.claim_specs] == [ClaimType.FIELD_UNAVAILABLE]


# ---------------------------------------------------------------------------
# D-11 — OUT_OF_SCOPE
# ---------------------------------------------------------------------------

class TestD11OutOfScope:
    def test_d11_out_of_scope_refuses_with_fixed_template(self, repo, settings):
        st = ConversationState(session_id="o", constraints=ConstraintSet(price_max=PriceBound(100000, False)))
        plan = decide(make_ctx(
            pq("track my order", intent=Intent.OUT_OF_SCOPE), repo, settings, state=st,
        ))
        assert plan.decision == Decision.REFUSE
        assert plan.reason == RefusalReason.OUT_OF_SCOPE
        assert plan.claim_specs == ()
        # out-of-scope refusal must not mutate the persisted constraints
        assert plan.constraints_after == st.constraints


# ---------------------------------------------------------------------------
# D-12 — INTERNAL_VALIDATION_FAILURE (engine fallback when no retrieval outcome)
# ---------------------------------------------------------------------------

class TestD12InternalFailure:
    def test_d12_missing_search_falls_back_to_safe_refusal(self, repo, settings):
        plan = decide(make_ctx(
            pq("black shoes", intent=Intent.SEARCH, entity_terms=("shoes",)), repo, settings,
            search=None,
        ))
        assert plan.decision == Decision.REFUSE
        assert plan.reason == RefusalReason.INTERNAL_VALIDATION_FAILURE
        assert any(isinstance(s, SegmentNF) and s.kind == "NF-13" for s in plan.segments)


# ---------------------------------------------------------------------------
# D-13 — PRODUCT_FACT ANSWER
# ---------------------------------------------------------------------------

class TestD13ProductFactAnswer:
    def test_d13_all_fields_available_answers(self, repo, settings):
        p = repo.get("SH-BLK-001")
        assert p.price_minor is not None and p.stock_status.value != "UNKNOWN"
        plan = decide(make_ctx(
            pq("price and stock of SH-BLK-001", intent=Intent.PRODUCT_FACT,
               product_ids=("SH-BLK-001",),
               requested_fields=(RequestedField.PRICE, RequestedField.STOCK)),
            repo, settings, merge=mr(ConstraintSet()),
        ))
        assert plan.decision == Decision.ANSWER
        types = {c.type for c in plan.claim_specs}
        assert types == {ClaimType.PRICE, ClaimType.STOCK}
        for c in plan.claim_specs:
            assert c.evidence_ids  # no answer claim is ungrounded


# ---------------------------------------------------------------------------
# D-14 / D-15 — EXISTENCE / SEARCH ANSWER
# ---------------------------------------------------------------------------

def _ranked_search(ids, turn_id=1, applied=None):
    return SearchResult(
        manifest=cat_manifest(turn_id=turn_id, exact=len(ids), applied=applied),
        exact_ids=tuple(ids),
        ranked=tuple(Candidate(pid) for pid in ids),
        related=(),
    )


class TestD14ExistenceAnswer:
    def test_d14_existence_returns_count_with_existence_context(self, repo, settings):
        ids = ["SH-BLK-001", "SH-BLK-002"]
        for pid in ids:
            assert repo.get(pid) is not None
        plan = decide(make_ctx(
            pq("do you sell black shoes", intent=Intent.EXISTENCE, entity_terms=("shoes",)),
            repo, settings, merge=mr(ConstraintSet()), search=_ranked_search(ids),
        ))
        assert plan.decision == Decision.ANSWER
        count = [c for c in plan.claim_specs if c.type == ClaimType.RESULT_COUNT]
        assert count and count[0].value["context"] == "EXISTENCE"
        assert count[0].value["count"] == len(ids)


class TestD14DescriptionOnly:
    """D-14 DESCRIPTION_ONLY / NF-14: an EXISTENCE query whose terms appear only in
    product descriptions (never a title/attribute) answers with the non-factual
    NF-14 lead-in plus verbatim DESCRIPTION_QUOTE claims and NO existence claim."""

    # EL-AUD-002 / SH-BLU-001 / SH-AMB-201 describe 'memory'/'foam'; none of these
    # products carry 'memory' or 'foam' in a title, category or attribute value.
    DESC_ONLY_IDS = ["EL-AUD-002", "SH-BLU-001", "SH-AMB-201"]

    def test_description_only_existence_quotes_descriptions(self, repo, settings):
        for pid in self.DESC_ONLY_IDS:
            assert repo.get(pid) is not None
        plan = decide(make_ctx(
            pq("do you sell memory foam", intent=Intent.EXISTENCE,
               entity_terms=("memory", "foam")),
            repo, settings, merge=mr(ConstraintSet()),
            search=_ranked_search(self.DESC_ONLY_IDS),
        ))
        assert plan.decision == Decision.ANSWER
        # NF-14 lead-in present exactly once
        assert sum(
            1 for s in plan.segments
            if isinstance(s, SegmentNF) and s.kind == "NF-14"
        ) == 1
        # every claim is a grounded DESCRIPTION_QUOTE; no existence/count claim
        assert plan.claim_specs
        assert all(c.type == ClaimType.DESCRIPTION_QUOTE for c in plan.claim_specs)
        assert not any(
            c.type in (ClaimType.PRODUCT_EXISTS, ClaimType.RESULT_COUNT)
            for c in plan.claim_specs
        )
        # no product cards: a description mention is not an existence assertion
        assert plan.card_specs == ()
        for c in plan.claim_specs:
            prod = repo.get(c.product_id)
            assert f"prod:{c.product_id}:description" in c.evidence_ids
            quote = c.value["quote"]
            assert quote and quote in prod.description  # V-03 verbatim

    def test_title_match_does_not_fire_the_branch(self, repo, settings):
        """Guard: when a query term is in a title the branch must not fire, and the
        normal D-14 primary (RESULT_COUNT + summaries) path is taken instead."""
        ids = ["SH-BLK-001", "SH-BLK-002"]
        plan = decide(make_ctx(
            pq("do you sell black shoes", intent=Intent.EXISTENCE, entity_terms=("shoes",)),
            repo, settings, merge=mr(ConstraintSet()), search=_ranked_search(ids),
        ))
        assert plan.decision == Decision.ANSWER
        assert not any(
            isinstance(s, SegmentNF) and s.kind == "NF-14" for s in plan.segments
        )
        assert not any(c.type == ClaimType.DESCRIPTION_QUOTE for c in plan.claim_specs)
        assert any(c.type == ClaimType.RESULT_COUNT for c in plan.claim_specs)

    def test_description_only_preempts_d05_ambiguity(self, repo, settings):
        """Ordering: retrieval hardcodes every candidate as a title tie, so the real
        orchestrator hands an AMBIGUOUS entity resolution for a description-only
        query. The D-14 branch runs before D-05 and must answer, not clarify."""
        ranked = tuple(Candidate(pid) for pid in self.DESC_ONLY_IDS)
        entity = EntityResolution(kind="AMBIGUOUS", candidates=ranked,
                                  manifest=cat_manifest(exact=len(ranked)))
        plan = decide(make_ctx(
            pq("do you sell memory foam", intent=Intent.EXISTENCE,
               entity_terms=("memory", "foam")),
            repo, settings, merge=mr(ConstraintSet()),
            search=_ranked_search(self.DESC_ONLY_IDS), entity=entity,
        ))
        assert plan.decision == Decision.ANSWER
        assert any(isinstance(s, SegmentNF) and s.kind == "NF-14" for s in plan.segments)
        assert plan.clarification is None

    def test_ambiguous_title_query_still_clarifies(self, repo, settings):
        """Conflict preserved: a genuine multi-title tie stays on D-05 (the branch is
        skipped because a title/attribute hit exists)."""
        ids = ["SH-BLK-001", "SH-BLK-002"]
        ranked = tuple(Candidate(pid) for pid in ids)
        entity = EntityResolution(kind="AMBIGUOUS", candidates=ranked,
                                  manifest=cat_manifest(exact=len(ids)))
        plan = decide(make_ctx(
            pq("do you sell black shoes", intent=Intent.EXISTENCE, entity_terms=("shoes",)),
            repo, settings, merge=mr(ConstraintSet()),
            search=_ranked_search(ids), entity=entity,
        ))
        assert plan.decision == Decision.CLARIFY
        assert plan.reason == ClarifyReason.AMBIGUOUS_PRODUCT
        assert not any(c.type == ClaimType.DESCRIPTION_QUOTE for c in plan.claim_specs)

    def test_no_safely_quotable_sentence_yields_no_quote(self, repo):
        """Insufficient/unsafe evidence fallback: a description mention that only
        appears inside a stock-word sentence is NOT quoted (V-11 would trip), so the
        branch produces no quote rather than fabricating a grounded-looking claim."""
        import dataclasses
        from app.decision.engine import _pick_description_quote, _product_title_attr_hit
        base = repo.get("EL-AUD-002")
        assert base is not None
        crafted = dataclasses.replace(base, description="Memory foam earcups are available.")
        attrs = repo.attributes("EL-AUD-002")
        # genuinely description-only (no title/attribute carries the term) ...
        assert not _product_title_attr_hit(crafted, attrs, ("memory", "foam"))
        # ... but the sole matching sentence uses a stock word, so nothing is quotable
        assert _pick_description_quote(crafted, ("memory", "foam")) is None


class TestD15SearchAnswer:
    def test_d15_search_returns_count_and_summaries(self, repo, settings):
        ids = ["SH-BLK-001", "SH-BLK-002", "SH-BLK-003"]
        for pid in ids:
            assert repo.get(pid) is not None
        plan = decide(make_ctx(
            pq("black shoes under 2000", intent=Intent.SEARCH, entity_terms=("shoes",)),
            repo, settings, merge=mr(ConstraintSet(price_max=PriceBound(200000, False))),
            search=_ranked_search(ids),
        ))
        assert plan.decision == Decision.ANSWER
        types = [c.type for c in plan.claim_specs]
        assert types[0] == ClaimType.RESULT_COUNT
        count_claim = plan.claim_specs[0]
        assert count_claim.value["context"] == "SEARCH"
        assert count_claim.value["count"] == len(ids)
        assert types.count(ClaimType.PRODUCT_SUMMARY) == len(ids)
        assert len(plan.card_specs) == len(ids)
        assert all(card.origin == CardOrigin.SEARCH_RESULT for card in plan.card_specs)

    def test_d15_carry_over_adds_note_and_warning(self, repo, settings):
        ids = ["SH-BLK-001"]
        applied = [ConstraintView(key="stock", label="In stock",
                                  op_to_remove=ConstraintOpIn(op="REMOVE", key="stock"))]
        plan = decide(make_ctx(
            pq("only in stock", intent=Intent.SEARCH, entity_terms=("shoes",)),
            repo, settings, merge=mr(ConstraintSet(stock=None), carried_over=True),
            search=_ranked_search(ids, applied=applied),
        ))
        assert WarningCode.CONSTRAINTS_CARRIED_OVER in plan.warnings
        assert any(isinstance(s, SegmentNF) and s.kind == "NF-11" for s in plan.segments)


# ---------------------------------------------------------------------------
# D-16 — POLICY ANSWER
# ---------------------------------------------------------------------------

class TestD16PolicyAnswer:
    def test_d16_established_sections_quote(self, repo, settings):
        secs = (
            PolicySection(section_id="1", ordinal=1, level=1, heading="Returns and Refunds",
                          body="Items may be returned within 30 days."),
            PolicySection(section_id="1.1", ordinal=2, level=2, heading="Return Window",
                          body="The return window is 30 days from delivery."),
            PolicySection(section_id="1.2", ordinal=3, level=2, heading="Refund Processing",
                          body="Refunds are processed within 5 days."),
        )
        pol = PolicyResult(manifest=policy_manifest(matched=["1", "1.1"]),
                           sections=secs, established=True, available_topics=("returns",))
        plan = decide(make_ctx(
            pq("what is the return policy", intent=Intent.POLICY, policy_topic="returns"),
            repo, settings, policy=pol,
        ))
        assert plan.decision == Decision.ANSWER
        quotes = [c for c in plan.claim_specs if c.type == ClaimType.POLICY_QUOTE]
        assert 1 <= len(quotes) <= 2  # top 1-2 sections only
        for q in quotes:
            assert q.scope == ClaimScope.GENERAL_POLICY
            assert q.evidence_ids[0].startswith("pol:")


# ---------------------------------------------------------------------------
# D-17 — claim lacks evidence / value -> drop (and refuse if nothing remains)
# ---------------------------------------------------------------------------

class TestD17ClaimGrounding:
    def test_drops_claim_with_no_evidence(self):
        good = claim("c1", ClaimType.RESULT_COUNT, {"count": 3, "context": "SEARCH"}, ["search:s_1"])
        bad = claim("c2", ClaimType.PRICE, {"price_minor": 100}, [])  # no evidence
        out = enforce_claim_grounding(answer_plan([good, bad]))
        assert out.decision == Decision.ANSWER
        assert [c.claim_id for c in out.claim_specs] == ["c1"]
        # dropped claim's segment is removed too
        assert SegmentClaim("c2") not in out.segments

    def test_drops_claim_with_missing_value(self):
        good = claim("c1", ClaimType.RESULT_COUNT, {"count": 2, "context": "SEARCH"}, ["search:s_1"])
        bad = claim("c2", ClaimType.PRICE, None, ["prod:X:price"])  # value is None
        out = enforce_claim_grounding(answer_plan([good, bad]))
        assert [c.claim_id for c in out.claim_specs] == ["c1"]

    def test_all_claims_dropped_refuses_insufficient_evidence(self):
        a = claim("c1", ClaimType.PRICE, {"price_minor": 100}, [])
        b = claim("c2", ClaimType.STOCK, {"stock_status": "IN_STOCK"}, [])
        out = enforce_claim_grounding(answer_plan([a, b]))
        assert out.decision == Decision.REFUSE
        assert out.reason == RefusalReason.INSUFFICIENT_EVIDENCE
        assert out.claim_specs == ()
        assert out.card_specs == ()
        # a missing citation must not become a factual answer -> fact-free text only
        assert all(isinstance(s, SegmentNF) for s in out.segments)

    def test_fully_grounded_answer_is_unchanged(self):
        plan = answer_plan([claim("c1", ClaimType.RESULT_COUNT,
                                  {"count": 1, "context": "SEARCH"}, ["search:s_1"])])
        assert enforce_claim_grounding(plan) is plan

    def test_nonfactual_answer_is_untouched(self):
        plan = nonfactual_plan()
        assert enforce_claim_grounding(plan) is plan

    def test_mixed_claims_preserve_valid_and_related_suggestions(self):
        good = claim("c1", ClaimType.RESULT_COUNT, {"count": 1, "context": "SEARCH"}, ["search:s_1"])
        bad = claim("c2", ClaimType.PRICE, {"price_minor": 5}, [])
        plan = answer_plan([good, bad])
        from app.domain.plans import DecisionPlan, StateUpdate
        plan = DecisionPlan(**{**plan.__dict__, "suggestions": (
            ("c1", (ConstraintOpIn(op="REMOVE", key="stock"),), "keep"),
            ("c2", (ConstraintOpIn(op="REMOVE", key="stock"),), "drop-me"),
        )})
        out = enforce_claim_grounding(plan)
        assert [c.claim_id for c in out.claim_specs] == ["c1"]
        assert [s[0] for s in out.suggestions] == ["c1"]

    def test_decide_applies_d17_to_a_valid_answer_without_dropping(self, repo, settings):
        # End-to-end: a normal, well-grounded SEARCH answer passes through decide()
        # unchanged (D-17 must not drop valid, evidence-backed claims).
        ids = ["SH-BLK-001", "SH-BLK-002"]
        plan = decide(make_ctx(
            pq("black shoes", intent=Intent.SEARCH, entity_terms=("shoes",)),
            repo, settings, merge=mr(ConstraintSet(price_max=PriceBound(200000, False))),
            search=_ranked_search(ids),
        ))
        assert plan.decision == Decision.ANSWER
        assert len(plan.claim_specs) == 1 + len(ids)  # count + summaries, none dropped
