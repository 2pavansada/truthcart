from __future__ import annotations

import sqlite3

from app.domain.constraints import AttrConstraint, ConstraintSet
from app.container import build_container
from app.domain.enums import Completeness, Decision, Intent, RefusalReason, StockStatus
from app.domain.plans import SearchSpec
from app.nlp.constraint_extractor import parse_query
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.lexical import LexicalError
from app.orchestrator import Orchestrator
from app.schemas.chat import ChatRequest
from app.settings import load_settings
from app.store.product_repo import ProductRepo


class SyntheticLexical:
    has_fts = False

    def __init__(self, term_hits, order=None, fail_matching=False):
        self.term_hits = term_hits
        self.order = order
        self.fail_matching = fail_matching

    def match_terms(self, terms):
        if self.fail_matching:
            raise LexicalError("synthetic full-catalogue matching failure")
        return {term: frozenset(self.term_hits.get(term, ())) for term in terms}

    def rank(self, terms, allowed, k):
        order = self.order or sorted(allowed)
        return [(pid, float(len(order) - i)) for i, pid in enumerate(order) if pid in allowed][:k]


def _repo():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE products (
          pid INTEGER PRIMARY KEY, product_id TEXT, title TEXT, description TEXT,
          category TEXT, category_norm TEXT, category_path TEXT, price_minor INTEGER,
          currency TEXT, rating REAL, rating_count INTEGER, stock_status TEXT,
          stock_quantity INTEGER, suspicious_text INTEGER, attributes_text TEXT,
          source_row INTEGER, content_hash TEXT, raw_json TEXT
        );
        CREATE TABLE product_attributes (
          product_id TEXT, key_raw TEXT, key_norm TEXT, value_raw TEXT,
          value_norm TEXT, value_num REAL, unit TEXT
        );
    """)
    rows = [
        ("P-1", "Trail Runner", "Footwear", "footwear", "IN_STOCK", "Bluetooth"),
        ("P-2", "City Sneaker", "Footwear", "footwear", "IN_STOCK", "Bluetooth"),
        ("P-3", "Desk Speaker", "Electronics", "electronics", "IN_STOCK", "Bluetooth"),
        ("P-4", "Trail Boot", "Footwear", "footwear", "UNKNOWN", "No Bluetooth"),
    ]
    for i, (pid, title, category, norm, stock, attr) in enumerate(rows, 1):
        conn.execute(
            "INSERT INTO products VALUES (?, ?, ?, NULL, ?, ?, NULL, 150000, 'INR', 4.0, 1, ?, 1, 0, ?, 1, 'h', '{}')",
            (i, pid, title, category, norm, stock, attr),
        )
        if attr == "Bluetooth":
            conn.execute(
                "INSERT INTO product_attributes VALUES (?, 'Connectivity', 'connectivity', 'Bluetooth', 'bluetooth', NULL, NULL)",
                (pid,),
            )
    conn.commit()
    return ProductRepo(conn)


def _search(repo, lexical, *, constraints=None, terms=(), top_k=10, threshold=0.67):
    return HybridRetriever(repo, lexical, threshold).search_catalogue(
        SearchSpec(
            intent=Intent.SEARCH,
            constraints=constraints or ConstraintSet(),
            entity_terms=tuple(terms),
            top_k=top_k,
        ),
        turn_id=1,
    )


def test_broad_browse_uses_hard_eligible_set_without_lexical_terms():
    repo = _repo()
    result = _search(
        repo,
        SyntheticLexical({}),
        constraints=ConstraintSet(categories=frozenset({"footwear"})),
    )
    assert result.exact_ids == ("P-1", "P-2", "P-4")
    assert result.manifest.term_requirements == []
    assert result.manifest.ranking["lexical"] is False


def test_exact_product_or_category_terms_and_structured_attributes_gate_membership():
    repo = _repo()
    lexical = SyntheticLexical({"trail": {"P-1", "P-4"}})
    result = _search(
        repo,
        lexical,
        constraints=ConstraintSet(
            categories=frozenset({"footwear"}),
            attributes=(AttrConstraint("connectivity", "bluetooth"),),
        ),
        terms=("trail",),
    )
    # P-4 passes the lexical term, but fails the authoritative attribute filter.
    assert result.exact_ids == ("P-1",)
    assert result.manifest.eligible_count == 3
    assert result.manifest.exact_match_count == 1


def test_unknown_term_does_not_admit_otherwise_eligible_products():
    result = _search(
        _repo(),
        SyntheticLexical({"qzxvplmno": set()}),
        constraints=ConstraintSet(categories=frozenset({"footwear"})),
        terms=("qzxvplmno",),
    )
    assert result.manifest.eligible_count == 3
    assert result.exact_ids == ()
    assert result.manifest.exact_match_count == 0
    assert result.manifest.completeness == Completeness.EXHAUSTIVE


def test_partial_term_coverage_uses_configured_threshold():
    lexical = SyntheticLexical({
        "trail": {"P-1"}, "runner": {"P-1", "P-2"},
        "shoe": {"P-2"}, "footwear": {"P-1", "P-2"},
    })
    result = _search(_repo(), lexical, terms=("trail", "runner", "shoe", "footwear"))
    assert result.exact_ids == ("P-1", "P-2")  # 3/4 coverage meets 0.67
    assert {c.product_id: c.term_coverage for c in result.ranked} == {"P-1": 0.75, "P-2": 0.75}


def test_no_matching_products_has_exhaustive_zero_membership():
    result = _search(_repo(), SyntheticLexical({"unlisted": set()}), terms=("unlisted",))
    assert result.exact_ids == ()
    assert result.manifest.exact_match_count == 0
    assert result.manifest.relaxations == []
    assert result.manifest.completeness == Completeness.EXHAUSTIVE


def test_unique_spelling_correction_terms_use_catalogue_membership():
    # Spell correction is covered directly by NLP tests; here assert that its
    # corrected catalogue term participates in exact membership.
    result = _search(
        _repo(), SyntheticLexical({"sneakers": {"P-2"}}), terms=("sneakers",)
    )
    assert result.exact_ids == ("P-2",)
    assert result.manifest.term_requirements == ["sneakers"]


def test_unknown_or_ambiguous_correction_candidates_remain_nonmatches():
    result = _search(
        _repo(), SyntheticLexical({"chamger": set(), "qzxvplmno": set()}),
        terms=("chamger", "qzxvplmno"),
    )
    assert result.exact_ids == ()


def test_hard_filters_and_query_membership_are_both_required():
    result = _search(
        _repo(),
        SyntheticLexical({"trail": {"P-1", "P-3"}}),
        constraints=ConstraintSet(
            categories=frozenset({"footwear"}), stock=StockStatus.IN_STOCK
        ),
        terms=("trail",),
    )
    assert result.exact_ids == ("P-1",)
    assert "P-3" not in result.exact_ids  # query hit outside the hard-filter-eligible set
    assert "P-4" not in result.exact_ids  # hard-filter failure despite being a lexical hit


def test_membership_matching_failure_is_incomplete_and_admits_no_unverified_rows():
    result = _search(
        _repo(), SyntheticLexical({}, fail_matching=True), terms=("trail",)
    )
    assert result.exact_ids == ()
    assert result.manifest.completeness == Completeness.INCOMPLETE
    assert result.manifest.incomplete_reasons == ["LEXICAL_BACKEND_ERROR"]


def test_ranking_order_does_not_change_membership():
    repo = _repo()
    hits = {"runner": {"P-1", "P-2"}}
    first = _search(repo, SyntheticLexical(hits, order=["P-1", "P-2"]), terms=("runner",))
    second = _search(repo, SyntheticLexical(hits, order=["P-2", "P-1"]), terms=("runner",))
    assert first.exact_ids == second.exact_ids == ("P-1", "P-2")
    assert [c.product_id for c in first.ranked] != [c.product_id for c in second.ranked]


def test_unknown_content_term_produces_grounded_absence_not_catalogue_wide_answer():
    orch = Orchestrator(build_container(load_settings()))
    orch.reset("retrieval-membership")
    response = orch.handle(
        ChatRequest(session_id="retrieval-membership", message="show me qzxvplmno"),
        request_id="retrieval-membership",
    )
    manifest = next(e.search for e in response.evidence if e.kind == "SEARCH_MANIFEST")
    assert response.decision == Decision.REFUSE
    assert response.refusal.reason == RefusalReason.NO_CATALOGUE_MATCH
    assert manifest.term_requirements == ["qzxvplmno"]
    assert manifest.exact_match_count == 0
    assert manifest.completeness == Completeness.EXHAUSTIVE
    assert any(claim.type.value == "ABSENCE" for claim in response.claims)
    assert response.products == []


def test_intent_and_question_cues_are_not_required_lexical_terms():
    container = build_container(load_settings())
    existence = parse_query(
        "do you sell memory foam", False, container.vocab,
        container.settings, container.product_repo,
    )
    fact = parse_query(
        "what is the price of the blue running shoe", False, container.vocab,
        container.settings, container.product_repo,
    )
    named_product = parse_query(
        "do you sell the RoadRun Men's Black Running Shoes", False,
        container.vocab, container.settings, container.product_repo,
    )
    assert existence.entity_terms == ("memory", "foam")
    assert fact.entity_terms == ("running",)
    assert fact.intent == Intent.PRODUCT_FACT
    assert named_product.entity_terms == ("roadrun", "running")
