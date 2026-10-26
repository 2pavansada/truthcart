import sqlite3

import pytest

from app.conversation.merger import merge
from app.domain.constraints import ConstraintSet
from app.domain.enums import Intent
from app.domain.plans import SearchSpec
from app.nlp.constraint_extractor import parse_query
from app.settings import load_settings
from app.store.product_repo import CatalogueVocab, ProductRepo
from app.retrieval.hybrid import HybridRetriever


@pytest.fixture
def settings():
    return load_settings()


def parse(message, vocab, settings):
    return parse_query(message, False, vocab, settings)


def test_catalogue_recognizes_multiple_values_with_their_keys(settings):
    vocab = CatalogueVocab(
        categories=frozenset(),
        attr_values_by_key={
            "material": frozenset({"bamboo fiber", "recycled nylon"}),
            "finish": frozenset({"matte"}),
        },
    )

    parsed = parse("Show bamboo fiber and RECYCLED NYLON, matte", vocab, settings)

    assert {(op.value["key"], op.value["value"]) for op in parsed.constraint_ops if op.key == "attribute"} == {
        ("material", "bamboo fiber"),
        ("material", "recycled nylon"),
        ("finish", "matte"),
    }
    assert {attr.value for attr in parsed.this_turn_constraints.attributes} == {
        "bamboo fiber", "recycled nylon", "matte"
    }
    assert any(op.value == {"key": "material", "value": "recycled nylon"} for op in parsed.constraint_ops)


def test_values_are_not_assigned_to_unrelated_keys(settings):
    vocab = CatalogueVocab(
        categories=frozenset(),
        attr_values_by_key={"material": frozenset({"cobalt"}), "finish": frozenset({"matte"})},
    )

    parsed = parse("cobalt matte", vocab, settings)

    assert {(a.key, a.value) for a in parsed.this_turn_constraints.attributes} == {
        ("material", "cobalt"), ("finish", "matte")
    }


def test_attribute_values_are_normalized_and_unknown_values_are_ignored(settings):
    vocab = CatalogueVocab(categories=frozenset(), attr_values_by_key={" Body  Material ": frozenset({"  Bamboo   Fiber "})})

    parsed = parse("BAMBOO   FIBER", vocab, settings)

    assert [(a.key, a.value) for a in parsed.this_turn_constraints.attributes] == [
        ("body material", "bamboo fiber")
    ]
    assert not parse("unobtainium", vocab, settings).this_turn_constraints.attributes


@pytest.mark.parametrize("vocab", [None, CatalogueVocab(categories=frozenset(), attr_values_by_key={})])
def test_missing_or_empty_vocabulary_is_safe(settings, vocab):
    parsed = parse_query("unlisted value", False, vocab, settings)
    assert not parsed.this_turn_constraints.attributes


def test_overlapping_values_and_multiple_candidate_values(settings):
    vocab = CatalogueVocab(
        categories=frozenset(),
        attr_values_by_key={"material": frozenset({"steel", "stainless steel", "aluminum"})},
    )

    overlap = parse("stainless steel", vocab, settings)
    competing = parse("steel or aluminum", vocab, settings)

    # Prefer the most specific non-overlapping catalogue phrase for a key.
    assert {a.value for a in overlap.this_turn_constraints.attributes} == {"stainless steel"}
    # Distinct explicit values are preserved with the contract's AND semantics.
    assert {a.value for a in competing.this_turn_constraints.attributes} == {"steel", "aluminum"}


def test_legacy_colour_material_and_carry_over_remain_compatible(settings):
    vocab = CatalogueVocab(categories=frozenset(), attr_values_by_key={"color": frozenset({"black"})})
    parsed = parse("black cotton", vocab, settings)

    assert {(a.key, a.value) for a in parsed.this_turn_constraints.attributes} == {
        ("color", "black"), ("material", "cotton")
    }
    carried = merge(ConstraintSet(), list(parsed.constraint_ops), vocab=vocab).constraints
    followed = merge(carried, [], vocab=vocab).constraints
    assert followed.attributes == carried.attributes


class _Lexical:
    has_fts = False

    def rank(self, terms, eligible_ids, k):
        return [(pid, 1.0) for pid in sorted(eligible_ids)[:k]]


def _synthetic_repo():
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
    for pid, color, material in [("SYN-1", "teal", "bamboo fiber"), ("SYN-2", "red", "cotton")]:
        conn.execute(
            "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1 if pid == "SYN-1" else 2, pid, f"Sample {pid}", None, "Accessories", "accessories", None,
             1000, "INR", None, None, "IN_STOCK", 1, 0, "", 1, "hash", "{}"),
        )
        for key, value in (("color", color), ("material", material)):
            conn.execute("INSERT INTO product_attributes VALUES (?, ?, ?, ?, ?, NULL, NULL)",
                         (pid, key, key, value, value))
    conn.commit()
    return ProductRepo(conn)


def test_extracted_structured_constraint_reaches_hard_filtered_results(settings):
    repo = _synthetic_repo()
    vocab = repo.vocab()
    parsed = parse("Show bamboo fiber", vocab, settings)
    merged = merge(ConstraintSet(), parsed.constraint_ops, vocab=vocab)
    search = HybridRetriever(repo, _Lexical()).search_catalogue(
        SearchSpec(intent=Intent.SEARCH, constraints=merged.constraints), turn_id=1
    )

    assert search.exact_ids == ("SYN-1",)
    assert search.manifest.applied_constraints[0].key == "attribute"
    assert search.manifest.applied_constraints[0].op_to_remove.value == {
        "key": "material", "value": "bamboo fiber"
    }
