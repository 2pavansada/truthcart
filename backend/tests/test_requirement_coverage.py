from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from eval.dataset import DEFAULT_DEV_SET, load_cases
from eval.metrics import evaluate_gates
from eval.report import render_markdown
from eval.requirement_coverage import audit_coverage, load_inventory, validate_inventory


@pytest.fixture
def inventory():
    return load_inventory()


@pytest.fixture
def cases():
    return load_cases(DEFAULT_DEV_SET)


def _result(case_id, *, status="pass", decision="ANSWER", reason=None, trips=0):
    return SimpleNamespace(
        case_id=case_id,
        rules=(),
        status=status,
        final_decision=decision,
        final_reason=reason,
        validator_trips=trips,
        returned_products=[],
        findings=[],
    )


def _d12_branch(inventory):
    return next(
        branch
        for requirement in inventory["requirements"]
        if requirement["requirement_id"] == "D-12"
        for branch in requirement["branches"]
    )


def _report(inventory, cases, results=(), evidence=None, run_id="run-1"):
    return audit_coverage(
        inventory, cases, list(results), fault_test_evidence=evidence,
        expected_run_id=run_id,
    )


def test_inventory_has_unique_requirements_branches_and_valid_case_refs(inventory, cases):
    req_ids = [r["requirement_id"] for r in inventory["requirements"]]
    branch_ids = [b["branch_id"] for r in inventory["requirements"] for b in r["branches"]]
    assert req_ids == [f"D-{i:02d}" for i in range(1, 18)]
    assert len(req_ids) == len(set(req_ids))
    assert len(branch_ids) == len(set(branch_ids))
    assert validate_inventory(inventory, cases) == []


def test_inventory_rejects_duplicate_requirement_and_branch_ids(inventory, cases):
    bad = deepcopy(inventory)
    duplicate = deepcopy(bad["requirements"][0])
    duplicate["branches"][0]["branch_id"] = bad["requirements"][0]["branches"][0]["branch_id"]
    bad["requirements"].append(duplicate)

    issues = validate_inventory(bad, cases)

    assert any("duplicate requirement_id" in issue for issue in issues)
    assert any("duplicate branch_id" in issue for issue in issues)


def test_uncovered_branch_must_be_described(inventory, cases):
    bad = deepcopy(inventory)
    branch = bad["requirements"][0]["branches"][0]
    branch["ordinary_cases"] = []
    branch["description"] = ""
    assert any("uncovered branch needs a description" in issue
               for issue in validate_inventory(bad, cases))


def test_inventory_rejects_missing_and_duplicate_ordinary_case_refs(inventory, cases):
    bad = deepcopy(inventory)
    refs = bad["requirements"][0]["branches"][0]["ordinary_cases"]
    refs[0]["case_id"] = "missing-case"
    issues = validate_inventory(bad, cases)
    assert any("unresolved ordinary case missing-case" in issue for issue in issues)

    duplicate_cases = [*cases, cases[0]]
    issues = validate_inventory(inventory, duplicate_cases)
    assert any("ambiguous ordinary case TC-D01-greeting" in issue for issue in issues)

    conflict = deepcopy(inventory)
    conflict["requirements"][0]["branches"][0]["ordinary_cases"][0]["expected"]["decision"] = "REFUSE"
    assert any("conflicts with the evaluation case expectation" in issue
               for issue in validate_inventory(conflict, cases))


@pytest.mark.parametrize(
    ("tests", "expected_status"),
    [([], "unresolved"),
     ([{"coverage_test_id": "FI-D12-validator-rejection", "status": "not_executed", "events": []}], "not_executed"),
     ([{"coverage_test_id": "FI-D12-validator-rejection", "status": "skipped", "events": []}], "skipped"),
     ([{"coverage_test_id": "FI-D12-validator-rejection", "status": "deselected", "events": []}], "deselected"),
     ([{"coverage_test_id": "FI-D12-validator-rejection", "status": "failed", "events": []}], "failed")],
)
def test_nonpassing_or_missing_fault_test_never_counts_as_coverage(
    inventory, cases, tests, expected_status
):
    evidence = {"run_id": "run-1", "tests": tests}
    report = _report(inventory, cases, evidence=evidence)
    branch = report["requirements"][11]["branches"][0]

    assert branch["fault_injection"][0]["status"] == expected_status
    assert branch["status"] == ("failing" if expected_status == "failed" else "untested")


def test_missing_ambiguous_and_stale_fault_references_are_not_verified(inventory, cases):
    missing = _report(inventory, cases, evidence={"run_id": "run-1", "tests": []})
    assert missing["requirements"][11]["branches"][0]["fault_injection"][0]["status"] == "unresolved"

    record = {"coverage_test_id": "FI-D12-validator-rejection", "status": "passed", "events": []}
    ambiguous = _report(inventory, cases, evidence={"run_id": "run-1", "tests": [record, record]})
    assert ambiguous["requirements"][11]["branches"][0]["fault_injection"][0]["status"] == "ambiguous"

    stale = _report(inventory, cases, evidence={"run_id": "old-run", "tests": [record]})
    assert stale["requirements"][11]["branches"][0]["fault_injection"][0]["status"] == "stale"


def test_passed_fault_test_needs_exact_runtime_event(inventory, cases):
    record = {
        "coverage_test_id": "FI-D12-validator-rejection",
        "status": "passed",
        "events": [{"requirement_id": "D-12", "branch_id": "wrong-branch", "outcome": "wrong"}],
    }
    report = _report(inventory, cases, evidence={"run_id": "run-1", "tests": [record]})
    branch = report["requirements"][11]["branches"][0]
    assert branch["fault_injection"][0]["status"] == "passed_without_event"
    assert branch["status"] == "untested"


def test_correct_fault_event_verifies_only_its_mapped_branch(inventory, cases):
    record = {
        "coverage_test_id": "FI-D12-validator-rejection",
        "status": "passed",
        "events": [{
            "requirement_id": "D-12",
            "branch_id": "D-12.validator-rejection-refusal",
            "outcome": "fact-free-refusal-trip-once",
        }],
    }
    report = _report(inventory, cases, evidence={"run_id": "run-1", "tests": [record]})
    d12 = report["requirements"][11]
    d17 = report["requirements"][16]
    assert d12["branches"][0]["status"] == "verified"
    assert d12["branches"][0]["fault_injection"][0]["status"] == "verified"
    assert d17["branches"][0]["status"] == "untested"


def test_ordinary_case_requires_executed_matching_observation(inventory, cases):
    case_id = "TC-D02-contradictory"
    missing = _report(inventory, cases)
    branch = missing["requirements"][1]["branches"][0]
    assert branch["ordinary_evaluation"][0]["status"] == "not_executed"
    assert branch["status"] == "untested"

    wrong = _report(inventory, cases, results=[_result(case_id, decision="ANSWER")])
    assert wrong["requirements"][1]["branches"][0]["ordinary_evaluation"][0]["status"] == "failed"
    assert wrong["requirements"][1]["branches"][0]["status"] == "failing"

    matched = _report(inventory, cases, results=[_result(
        case_id, decision="CLARIFY", reason="CONTRADICTORY_CONSTRAINTS"
    )])
    assert matched["requirements"][1]["branches"][0]["status"] == "verified"


def test_d12_and_d17_keep_ordinary_gaps_separate_from_fault_coverage(inventory, cases):
    results = [
        _result("TC-D12-validator-reject", status="known_gap", decision="ANSWER"),
        _result("TC-D17-insufficient-evidence", status="known_gap", decision="ANSWER"),
    ]
    evidence = {"run_id": "run-1", "tests": [
        {
            "coverage_test_id": "FI-D12-validator-rejection", "status": "passed",
            "events": [{"requirement_id": "D-12", "branch_id": "D-12.validator-rejection-refusal",
                        "outcome": "fact-free-refusal-trip-once"}],
        },
        {
            "coverage_test_id": "FI-D17-all-claims-unusable", "status": "passed",
            "events": [{"requirement_id": "D-17", "branch_id": "D-17.all-claims-unusable-refusal",
                        "outcome": "fact-free-insufficient-evidence-refusal"}],
        },
    ]}
    report = _report(inventory, cases, results, evidence)

    for index in (11, 16):
        branch = report["requirements"][index]["branches"][0]
        assert branch["ordinary_evaluation"][0]["status"] == "ordinary_gap"
        assert branch["fault_injection"][0]["status"] == "verified"
        assert branch["status"] == "verified"


def test_injected_failure_evidence_does_not_enter_g_ac_03(inventory, cases):
    evidence = {"run_id": "run-1", "tests": [{
        "coverage_test_id": "FI-D12-validator-rejection", "status": "passed",
        "events": [{"requirement_id": "D-12", "branch_id": "D-12.validator-rejection-refusal",
                    "outcome": "fact-free-refusal-trip-once"}],
    }]}
    # The ordinary evaluation result remains zero-trip; fault evidence is not a
    # CaseResult and the existing gate continues to consume only eval results.
    ordinary_results = [_result("ordinary-d12", trips=0)]
    _report(inventory, cases, ordinary_results, evidence)
    assert evaluate_gates(ordinary_results)["G-AC-03"]["passed"] is True


def test_report_separates_coverage_categories_and_explains_missing_evidence(inventory, cases):
    coverage = _report(inventory, cases)
    rendered = render_markdown({
        "meta": {}, "metrics": {}, "gates": {}, "results": [],
        "requirement_coverage": coverage,
    })
    assert "## Requirement Coverage" in rendered
    assert "Ordinary evaluation" in rendered
    assert "Fault injection" in rendered
    assert "untested" in rendered
    assert "not supplied" in rendered


def test_contract_branches_without_references_are_reported_as_untested(inventory, cases):
    coverage = _report(inventory, cases)
    d01_help = next(
        branch for requirement in coverage["requirements"]
        if requirement["requirement_id"] == "D-01"
        for branch in requirement["branches"]
        if branch["branch_id"] == "D-01.help-answer"
    )
    assert d01_help["status"] == "untested"
    assert d01_help["description"]
    assert coverage["summary"]["untested"] > 0
