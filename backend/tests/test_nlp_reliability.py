from __future__ import annotations

import pytest

from app.container import build_container
from app.domain.enums import Decision, Intent, RefusalReason, ClarifyReason
from app.domain.parsed_query import NormalizedText
from app.nlp.intent import classify_intent
from app.nlp import normalizer as normalizer_module
from app.nlp.normalizer import normalize
from app.nlp.spellfix import spellfix
from app.nlp.constraint_extractor import parse_query
from app.orchestrator import Orchestrator
from app.schemas.chat import ChatRequest
from app.settings import load_settings
from app.store.product_repo import CatalogueVocab


@pytest.fixture(scope="module")
def container():
    return build_container(load_settings())


@pytest.fixture(scope="module")
def orch(container):
    return Orchestrator(container)


def _say(orch, session, message):
    return orch.handle(
        ChatRequest(session_id=session, message=message), request_id=f"nlp-{session}"
    )


def test_intent_classifier_preserves_unknown_for_unclassified_text():
    nt = NormalizedText(original="mystery", text="mystery")
    assert classify_intent(nt).intent == Intent.UNKNOWN


def test_contentful_unknown_follows_documented_search_route(container):
    parsed = parse_query(
        "qzxvplmno", False, container.vocab, container.settings, container.product_repo
    )
    assert parsed.intent == Intent.SEARCH
    assert parsed.entity_terms == ("qzxvplmno",)


def test_content_free_unknown_clarifies_end_to_end(orch):
    orch.reset("nlp-unknown-empty")
    parsed = parse_query(
        "???", False, orch.container.vocab, orch.container.settings,
        orch.container.product_repo,
    )
    assert parsed.intent == Intent.UNKNOWN
    response = _say(orch, "nlp-unknown-empty", "???")
    assert response.decision == Decision.CLARIFY
    assert response.clarification.reason == ClarifyReason.UNDER_SPECIFIED_QUERY
    assert response.metrics.validator_trips == 0


def test_explicit_unsupported_question_keeps_out_of_scope_route(orch):
    orch.reset("nlp-out-of-scope")
    response = _say(orch, "nlp-out-of-scope", "tell me a joke")
    assert response.decision == Decision.REFUSE
    assert response.refusal.reason == RefusalReason.OUT_OF_SCOPE


def test_catalogue_typo_is_corrected_before_search_and_reported(orch):
    orch.reset("nlp-spell-e2e")
    response = _say(orch, "nlp-spell-e2e", "show me sneakres")
    assert any(warning.value == "SPELLING_CORRECTED" for warning in response.warnings)
    manifest = next(e.search for e in response.evidence if e.kind == "SEARCH_MANIFEST")
    # The current parser maps this catalogue synonym to the structured
    # footwear category, which is enforced by the existing hard-filter path.
    assert any(
        constraint.key == "category" and "Footwear" in constraint.label
        for constraint in manifest.applied_constraints
    )
    assert manifest.eligible_count > 0
    assert response.metrics.validator_trips == 0


@pytest.fixture
def vocab():
    return CatalogueVocab(
        categories=frozenset({"footwear"}),
        attr_values_by_key={
            "material": frozenset({"bamboo fiber"}),
            "connectivity": frozenset({"bluetooth"}),
        },
        title_tokens=frozenset({"sneakers", "charger", "charmer"}),
    )


def test_exact_catalogue_match_is_unchanged(vocab):
    corrected, trace = spellfix("sneakers", vocab)
    assert corrected == "sneakers"
    assert trace == ()


def test_unique_clear_typo_is_corrected_with_trace(vocab):
    corrected, trace = spellfix("Sneakres,", vocab)
    assert corrected == "Sneakers,"
    assert [(t.kind, t.before, t.after) for t in trace] == [
        ("SPELLFIX", "Sneakres,", "Sneakers,")
    ]


def test_ambiguous_best_candidates_are_not_corrected():
    ambiguous = CatalogueVocab(
        categories=frozenset(), attr_values_by_key={},
        title_tokens=frozenset({"charger", "charmer"}),
    )
    corrected, trace = spellfix("chamger", ambiguous)
    assert corrected == "chamger"
    assert trace == ()


def test_unknown_word_is_not_corrected(vocab):
    corrected, trace = spellfix("qzxvplmno", vocab)
    assert corrected == "qzxvplmno"
    assert trace == ()


def test_attribute_vocabulary_is_used_and_numbers_ids_remain_unchanged(vocab):
    corrected, trace = spellfix("bluetooh SH-BLK-001 2000", vocab)
    assert corrected == "bluetooth SH-BLK-001 2000"
    assert trace and trace[0].after == "bluetooth"


def test_corrected_catalogue_attribute_becomes_keyed_constraint(vocab):
    settings = load_settings()
    parsed = parse_query("show bluetooh", False, vocab, settings)
    assert parsed.normalized.text == "show bluetooth"
    assert any(
        op.key == "attribute"
        and op.value == {"key": "connectivity", "value": "bluetooth"}
        for op in parsed.constraint_ops
    )


def test_normalizer_spellfix_is_idempotent(vocab):
    once = normalize("Sneakres shoes", vocab=vocab)
    twice = normalize(once.text, vocab=vocab)
    assert once.text == "Sneakers shoes"
    assert twice.text == once.text
    assert any(t.kind == "SPELLFIX" for t in once.trace)
    assert not any(t.kind == "SPELLFIX" for t in twice.trace)


def test_currency_marker_is_not_rewritten_inside_catalogue_words():
    first = normalize("Sneakers shoes")
    assert first.text == "Sneakers shoes"
    assert normalize(first.text).text == first.text


def test_normalization_failure_uses_sanitized_original_and_trace(monkeypatch, vocab):
    def fail_spellfix(*args, **kwargs):
        raise RuntimeError("controlled normalization failure")

    monkeypatch.setattr(normalizer_module, "spellfix", fail_spellfix)
    raw = "Rs 2k\x00\x85\nshow Sneakres"

    result = normalize(raw, vocab=vocab)

    assert result.original == raw
    assert result.text == "Rs 2k show Sneakres"
    assert "\x00" not in result.text
    assert "\x85" not in result.text
    assert "\n" not in result.text
    assert [(item.kind, item.after) for item in result.trace] == [
        ("NORMALIZE_SKIPPED", "Rs 2k show Sneakres")
    ]


def test_normalization_failure_trace_survives_query_parsing(monkeypatch, vocab):
    def fail_spellfix(*args, **kwargs):
        raise RuntimeError("controlled normalization failure")

    monkeypatch.setattr(normalizer_module, "spellfix", fail_spellfix)
    parsed = parse_query("show Sneakres", False, vocab, load_settings())

    assert parsed.normalized.text == "show Sneakres"
    assert [item.kind for item in parsed.normalized.trace] == ["NORMALIZE_SKIPPED"]


def test_successful_normalization_does_not_emit_skipped_trace(vocab):
    result = normalize("Sneakres shoes", vocab=vocab)

    assert result.text == "Sneakers shoes"
    assert any(item.kind == "SPELLFIX" for item in result.trace)
    assert not any(item.kind == "NORMALIZE_SKIPPED" for item in result.trace)
