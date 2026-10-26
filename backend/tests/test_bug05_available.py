from __future__ import annotations

import pytest

from app.container import build_container
from app.domain.enums import ClarifyReason, Decision, StockStatus
from app.nlp.constraint_extractor import parse_query
from app.orchestrator import Orchestrator
from app.schemas.chat import ChatRequest
from app.settings import load_settings


@pytest.fixture(scope="module")
def container():
    return build_container(load_settings())


@pytest.fixture(scope="module")
def orch(container):
    return Orchestrator(container)


def _parse(text, container, *, focus=False):
    return parse_query(
        text, focus, container.vocab, container.settings, container.product_repo
    )


def _say(orch, session, message):
    return orch.handle(
        ChatRequest(session_id=session, message=message), request_id=f"bug05-{session}"
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Only in stock", "IN_STOCK"), ("Show out of stock products", "OUT_OF_STOCK")],
)
def test_explicit_stock_wording_produces_stock_constraint(text, expected, container):
    parsed = _parse(text, container)
    stock_ops = [op for op in parsed.constraint_ops if op.key == "stock"]
    assert [op.value for op in stock_ops] == [expected]
    assert not parsed.available_ambiguous


@pytest.mark.parametrize(
    "text",
    ["Is this product available?", "Show me available products", "Is it available?"],
)
def test_ambiguous_available_clarifies_without_stock_op(text, container, orch):
    parsed = _parse(text, container)
    assert parsed.available_ambiguous
    assert not [op for op in parsed.constraint_ops if op.key == "stock"]

    orch.reset("bug05-ambig")
    response = _say(orch, "bug05-ambig", text)
    assert response.decision == Decision.CLARIFY
    assert "listed in the catalogue or currently in stock" in response.answer_text
    assert not any(c.key == "stock" for c in response.active_constraints)
    assert response.metrics.validator_trips == 0


@pytest.mark.parametrize("text", ["Do you sell headphones?", "Do you have this product?"])
def test_catalogue_presence_question_does_not_add_stock_filter(text, container):
    parsed = _parse(text, container)
    assert not parsed.available_ambiguous
    assert not [op for op in parsed.constraint_ops if op.key == "stock"]


def test_attribute_presence_question_uses_attribute_filter_not_stock(container, orch):
    parsed = _parse("Does this product have Bluetooth?", container)
    assert not [op for op in parsed.constraint_ops if op.key == "stock"]
    assert any(op.key == "attribute" and op.value["value"] == "bluetooth"
               for op in parsed.constraint_ops)
    orch.reset("bug05-attribute-question")
    response = _say(orch, "bug05-attribute-question", "Does this product have Bluetooth?")
    assert not any(c.key == "stock" for c in response.active_constraints)
    assert any(c.key == "attribute" for c in response.active_constraints)


def test_ambiguous_clarification_preserves_budget_category_and_material(orch):
    orch.reset("bug05-preserve")
    first = _say(orch, "bug05-preserve", "show black cotton shoes under 2000")
    second = _say(orch, "bug05-preserve", "show available products")
    assert second.decision == Decision.CLARIFY
    active = {c.key for c in second.active_constraints}
    assert {"price_max", "category", "attribute"} <= active
    assert "stock" not in active


def test_stock_context_resolves_available_follow_up_without_weakening_filter(orch, container):
    orch.reset("bug05-stockctx")
    first = _say(orch, "bug05-stockctx", "show black shoes under 2000 and only in stock")
    assert first.decision == Decision.ANSWER
    second = _say(orch, "bug05-stockctx", "show available products")
    assert second.decision == Decision.ANSWER
    assert any(c.key == "stock" for c in second.active_constraints)
    assert second.products
    assert all(container.product_repo.get(card.product_id).stock_status == StockStatus.IN_STOCK
               for card in second.products)


def test_catalogue_context_resolves_available_follow_up_as_presence(orch):
    orch.reset("bug05-presence-context")
    first = _say(orch, "bug05-presence-context", "do you sell headphones")
    assert first.decision in {Decision.ANSWER, Decision.REFUSE, Decision.CLARIFY}
    second = _say(orch, "bug05-presence-context", "is it available?")
    assert second.decision != Decision.CLARIFY or second.clarification.reason == ClarifyReason.AMBIGUOUS_PRODUCT
    assert not any(c.key == "stock" for c in second.active_constraints)


def test_attribute_context_resolves_available_follow_up_without_stock(orch):
    orch.reset("bug05-attribute-context")
    first = _say(orch, "bug05-attribute-context", "does this product have Bluetooth")
    assert first.decision in {Decision.ANSWER, Decision.REFUSE}
    second = _say(orch, "bug05-attribute-context", "is it available?")
    assert second.decision != Decision.CLARIFY
    assert not any(c.key == "stock" for c in second.active_constraints)


def test_explicit_stock_hard_filter_excludes_out_of_stock_and_unknown(orch, container):
    unknown = container.product_repo.get("SH-TRK-001")
    assert unknown is not None and unknown.stock_status == StockStatus.UNKNOWN
    orch.reset("bug05-hardfilter")
    response = _say(orch, "bug05-hardfilter", "show shoes under 2000 only in stock")
    assert response.decision == Decision.ANSWER
    assert response.products
    for card in response.products:
        product = container.product_repo.get(card.product_id)
        assert product.stock_status == StockStatus.IN_STOCK
