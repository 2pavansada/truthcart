"""Tests for the M-10 evaluation dev-set dataset (schema + honest coverage)."""
from __future__ import annotations

import json

from eval.dataset import DEFAULT_DEV_SET, ExpectedBehaviour, HardFilters, load_cases
from eval.metrics import GATED_RULES

RAW = json.loads(DEFAULT_DEV_SET.read_text(encoding="utf-8"))
CASES = load_cases(DEFAULT_DEV_SET)


def test_dataset_file_is_valid_json_with_cases_key():
    assert isinstance(RAW, dict) and "cases" in RAW
    assert len(RAW["cases"]) > 0


def test_every_case_has_required_fields_and_expected():
    for c in CASES:
        assert c.case_id
        assert c.turns, f"{c.case_id} has no turns"
        assert all(t.message for t in c.turns), f"{c.case_id} has an empty turn message"
        assert isinstance(c.expected, ExpectedBehaviour)
        assert c.status in ("active", "known_gap")


def test_case_ids_unique():
    ids = [c.case_id for c in CASES]
    assert len(ids) == len(set(ids))


def test_known_gap_cases_carry_honest_note():
    for c in CASES:
        if c.status == "known_gap":
            assert c.gap_note and len(c.gap_note) > 20, f"{c.case_id} gap must have a note"


def test_all_gated_rules_have_an_end_to_end_scenario():
    """G-AC-01 coverage half: every D-01..D-17 appears in at least one case."""
    covered = {r for c in CASES for r in c.rules}
    missing = [d for d in GATED_RULES if d not in covered]
    assert not missing, f"rules with no e2e scenario: {missing}"


def test_unique_existence_case_is_scoped_to_d14():
    case = next(c for c in CASES if c.case_id == "TC-D14-unique-existence-match")
    assert case.rules == ("D-14",)
    assert case.expected.decision == "ANSWER"
    assert case.expected.exact_products == ("SH-BLK-001",)
    assert "D-05 does not apply" in case.rationale


def test_expected_decisions_and_reasons_are_valid_strings():
    valid_decisions = {"ANSWER", "CLARIFY", "REFUSE"}
    for c in CASES:
        if c.expected.decision is not None:
            assert c.expected.decision in valid_decisions


def test_hard_filters_dataclass_parses_from_dict():
    hf = HardFilters.from_dict(
        {"category": "footwear", "price_max_minor": 200000,
         "attributes": [{"key": "color", "value": "black"}]}
    )
    assert hf.category == "footwear"
    assert hf.price_max_minor == 200000
    assert hf.attributes == (("color", "black"),)
    assert HardFilters.from_dict(None) is None


def test_budget_and_stock_multiturn_scenario_is_present_and_active():
    """The explicitly required multi-turn carry-over scenario."""
    match = [c for c in CASES
             if len(c.turns) >= 2
             and "black shoes under 2000" in c.turns[0].message.lower()
             and "in stock" in c.turns[-1].message.lower()]
    assert match, "required budget-and-stock multi-turn scenario missing"
    case = match[0]
    assert case.status == "active"
    # carry-over must exclude the out-of-stock and unknown-stock black shoes
    assert "SH-BLK-004" in case.expected.must_exclude_products
    assert "SH-TRK-001" in case.expected.must_exclude_products


def test_incomplete_absence_cases_are_grounded():
    """A catalogue-absence case must expect a grounded ABSENCE claim (G-AC-06)."""
    absence = [c for c in CASES if c.expected.expect_absence]
    assert absence, "no grounded-absence case present"
    for c in absence:
        assert c.expected.no_definitive_absence is False
