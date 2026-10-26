import sqlite3

import pytest

from app.container import build_container
from app.conversation.merger import merge
from app.domain.constraints import AttrConstraint, ConstraintSet, PriceBound
from app.domain.enums import Decision, Intent, StockStatus, ClarifyReason
from app.domain.plans import SearchSpec
from app.nlp.constraint_extractor import parse_query
from app.orchestrator import Orchestrator
from app.retrieval.hybrid import HybridRetriever
from app.schemas.chat import ChatRequest
from app.settings import load_settings
from app.store.product_repo import ProductRepo


@pytest.fixture(scope="module")
def settings():
    return load_settings()


def _parse(message, settings):
    return parse_query(message, False, None, settings)


@pytest.mark.parametrize("message", ["electronics and shoes", "electronics or shoes"])
def test_same_turn_categories_form_or_set_independent_of_coordinator(message, settings):
    parsed = _parse(message, settings)
    merged = merge(ConstraintSet(), parsed.constraint_ops, additive_cue=parsed.additive_category_cue)

    assert merged.constraints.categories == frozenset({"electronics", "footwear"})
    assert parsed.additive_category_cue is False


@pytest.mark.parametrize(
    "message",
    [
        "also show me electronics",
        "also black shoes",
        "electronics also",
        "add electronics",
        "include electronics",
        "in addition to footwear, electronics",
        "along with footwear",
        "as well as footwear",
        "plus electronics",
        "footwear plus electronics",
        "footwear as well as electronics",
    ],
)
def test_supported_category_additive_phrases(message, settings):
    assert _parse(message, settings).additive_category_cue is True


def test_unrelated_or_ambiguous_wording_is_not_an_additive_cue(settings):
    assert _parse("electronics", settings).additive_category_cue is False
    assert _parse("electronics and shoes", settings).additive_category_cue is False
    # "also" is related to the stock condition, not the category phrase.
    assert _parse("also only in stock electronics", settings).additive_category_cue is False


def test_followup_replaces_categories_and_preserves_other_constraints(settings):
    current = ConstraintSet(
        categories=frozenset({"clothing"}),
        price_max=PriceBound(200000, False),
        stock=StockStatus.IN_STOCK,
        attributes=(AttrConstraint("material", "cotton"),),
    )
    parsed = _parse("electronics and shoes", settings)

    merged = merge(current, parsed.constraint_ops, additive_cue=parsed.additive_category_cue)

    assert merged.constraints.categories == frozenset({"electronics", "footwear"})
    assert merged.constraints.price_max == current.price_max
    assert merged.constraints.stock == current.stock
    assert merged.constraints.attributes == current.attributes


def test_explicit_addition_expands_existing_category_or_set(settings):
    current = ConstraintSet(categories=frozenset({"footwear"}))
    parsed = _parse("also show electronics", settings)

    merged = merge(current, parsed.constraint_ops, additive_cue=parsed.additive_category_cue)

    assert merged.constraints.categories == frozenset({"electronics", "footwear"})


@pytest.mark.parametrize("message", ["replace shoes with electronics", "no, I meant electronics"])
def test_explicit_corrections_replace_old_category_set(message, settings):
    current = ConstraintSet(categories=frozenset({"footwear"}))
    parsed = _parse(message, settings)

    merged = merge(current, parsed.constraint_ops, additive_cue=parsed.additive_category_cue)

    assert parsed.this_turn_constraints.categories == frozenset({"electronics"})
    assert merged.constraints.categories == frozenset({"electronics"})


def test_negated_categories_are_not_added_and_ambiguous_scope_flags_clarification(settings):
    negated = _parse("no electronics", settings)
    ambiguous = _parse("not electronics and shoes", settings)

    assert negated.this_turn_constraints.categories == frozenset()
    assert not [op for op in negated.constraint_ops if op.key == "category"]
    assert negated.category_ambiguous is True
    assert ambiguous.this_turn_constraints.categories == frozenset()
    assert ambiguous.category_ambiguous is True


class _Lexical:
    has_fts = False

    def rank(self, terms, eligible_ids, k):
        return [(pid, 1.0) for pid in sorted(eligible_ids)[:k]]


def _category_repo():
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
        ("SYN-E", "Electronics", "electronics"),
        ("SYN-F", "Footwear", "footwear"),
        ("SYN-C", "Clothing", "clothing"),
    ]
    for i, (pid, category, category_norm) in enumerate(rows, 1):
        conn.execute(
            "INSERT INTO products VALUES (?, ?, ?, NULL, ?, ?, NULL, 150000, 'INR', NULL, NULL, 'IN_STOCK', 1, 0, '', 1, 'h', '{}')",
            (i, pid, f"Sample {category}", category, category_norm),
        )
    conn.commit()
    return ProductRepo(conn)


def test_resulting_category_or_set_is_enforced_by_hard_filter(settings):
    repo = _category_repo()
    current = ConstraintSet(
        categories=frozenset({"clothing"}),
        price_max=PriceBound(200000, False),
        stock=StockStatus.IN_STOCK,
    )
    parsed = _parse("electronics and shoes", settings)
    merged = merge(current, parsed.constraint_ops, additive_cue=parsed.additive_category_cue)
    result = HybridRetriever(repo, _Lexical()).search_catalogue(
        SearchSpec(intent=Intent.SEARCH, constraints=merged.constraints), turn_id=1
    )

    assert result.exact_ids == ("SYN-E", "SYN-F")
    assert result.manifest.eligible_count == 2


@pytest.fixture(scope="module")
def orchestrator():
    return Orchestrator(build_container(load_settings()))


def _say(orch, session, message):
    return orch.handle(ChatRequest(session_id=session, message=message), request_id=f"bug04-{session}")


def test_real_followup_category_replacement_preserves_budget_and_filters(orchestrator):
    session = "bug04cat"
    orchestrator.reset(session)
    first = _say(orchestrator, session, "show me shoes under 2000")
    assert first.decision == Decision.ANSWER

    second = _say(orchestrator, session, "electronics")

    active = {(c.key, c.label) for c in second.active_constraints}
    assert any(key == "category" and label == "Category: Electronics" for key, label in active)
    assert not any(key == "category" and label == "Category: Footwear" for key, label in active)
    assert any(key == "price_max" for key, _ in active)
    for card in second.products:
        product = orchestrator.container.product_repo.get(card.product_id)
        assert product.category_norm == "electronics"
        assert product.price_minor is not None and product.price_minor < 200000


def test_ambiguous_negated_category_uses_clarification_and_keeps_state(orchestrator):
    session = "bug04amb"
    orchestrator.reset(session)
    first = _say(orchestrator, session, "show me shoes under 2000")
    assert first.decision == Decision.ANSWER

    second = _say(orchestrator, session, "not electronics and shoes")

    assert second.decision == Decision.CLARIFY
    assert second.clarification.reason == ClarifyReason.UNDER_SPECIFIED_QUERY
    assert any(c.key == "category" and c.label == "Category: Footwear" for c in second.active_constraints)
    assert any(c.key == "price_max" for c in second.active_constraints)
