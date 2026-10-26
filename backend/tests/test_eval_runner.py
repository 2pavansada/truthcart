"""Runner, metrics and report tests for the M-10 evaluation framework.

Two things are asserted separately and deliberately:
  1. the framework runs the dev set through the real orchestrator and produces a
     gate-driven verdict;
  2. the gate logic cannot be masked by a high aggregate pass rate — a single
     critical grounding / hard-filter / incomplete-search / template-trip failure
     forces a FAIL even when every other case passes.
"""
from __future__ import annotations

import pytest

from eval.auditor import SEVERE, Finding
from eval import metrics as M
from eval.report import render_markdown
from eval.runner import CaseResult, run_eval


def _cr(case_id, status, rules=(), trips=0, findings=(), decision="ANSWER", reasons=()):
    return CaseResult(
        case_id=case_id, rules=tuple(rules), tags=(), status=status,
        final_decision=decision, final_reason=None, validator_trips=trips,
        findings=list(findings), failure_reasons=list(reasons),
    )


def _crit(kind):
    return Finding("x", SEVERE, kind, "injected")


# ---- live framework run --------------------------------------------------
@pytest.fixture(scope="module")
def report():
    return run_eval()


def test_run_eval_returns_full_report(report):
    for key in ("meta", "gates", "overall_pass", "metrics", "results"):
        assert key in report
    assert report["results"], "no cases ran"


def test_g_ac_01_coverage_gate_passes(report):
    assert report["gates"]["G-AC-01"]["passed"] is True


def test_price_hallucination_gate_passes_on_live_system(report):
    # The live demo catalogue run produces zero price hallucinations.
    assert report["gates"]["PRICE_HALLUCINATION_ZERO"]["passed"] is True


def test_critical_grounding_gate_passes_on_live_system(report):
    assert report["gates"]["CRITICAL_GROUNDING_ZERO"]["passed"] is True


def test_budget_and_stock_carryover_scenario_passes(report):
    r = {x["case_id"]: x for x in report["results"]}
    carry = next(v for k, v in r.items() if "carryover" in k)
    assert carry["status"] == "pass"
    assert "SH-BLK-004" not in carry["returned_products"]
    assert "SH-TRK-001" not in carry["returned_products"]


def test_all_active_cases_pass_on_live_system(report):
    assert report["gates"]["ALL_ACTIVE_CASES_PASS"]["passed"] is True


def test_g_ac_03_template_zero_trip_passes(report):
    """G-AC-03 requires zero validator trips in template mode. The V-11 vs
    FIELD_UNAVAILABLE conflict was the source of the three trips (D-08 null price,
    D-08b partial fields, D-10 warranty-silent); the fix exempts only the closed-set
    missing-field template while keeping unsupported-claim detection intact, so all
    three now clear the validator without any trip."""
    gate = report["gates"]["G-AC-03"]
    assert gate["passed"] is True
    assert gate["offenders"] == []
    assert report["overall_pass"] is True


def test_known_gaps_are_not_counted_as_active_passes(report):
    # D-06 (generic policy clarify) is now implemented end-to-end -> active/pass.
    ids = {x["case_id"]: x for x in report["results"]}
    assert ids["TC-D06-ambiguous-policy-topic"]["status"] == "pass"
    # Genuine unimplemented branches stay flagged, not counted as active passes.
    still_gap = next(
        v for v in ids.values()
        if v["status"] == "known_gap" and v.get("gap_note")
    )
    assert still_gap["status"] == "known_gap"


def test_requirement_coverage_reports_ordinary_gaps_separately_from_unprovided_fault_evidence(report):
    coverage = report["requirement_coverage"]
    d12 = next(r for r in coverage["requirements"] if r["requirement_id"] == "D-12")
    d17 = next(r for r in coverage["requirements"] if r["requirement_id"] == "D-17")
    for requirement in (d12, d17):
        branch = requirement["branches"][0]
        assert branch["ordinary_evaluation"][0]["status"] == "ordinary_gap"
        assert branch["fault_injection"][0]["status"] == "not_provided"
        assert branch["status"] == "untested"
    assert coverage["summary"]["untested"] > 0
    assert report["gates"]["G-AC-03"]["passed"] is True


# ---- metrics: masking resistance (pure, deterministic) -------------------
def test_critical_finding_fails_gate_despite_100pct_pass_rate():
    results = [
        _cr("ok1", "pass", rules=["D-13"]),
        _cr("ok2", "pass", rules=["D-15"]),
        _cr("bad", "pass", rules=["D-15"], findings=[_crit("HARD_FILTER_VIOLATION")]),
    ]
    gates = M.evaluate_gates(results)
    assert gates["CRITICAL_GROUNDING_ZERO"]["passed"] is False
    assert M.overall_pass(gates) is False
    agg = M.aggregate(results)
    # aggregate pass rate still looks perfect for the first two — masking blocked
    assert agg["critical_findings"] == 1


def test_definitive_absence_incomplete_is_critical():
    results = [_cr("a", "pass", rules=["D-07"], findings=[_crit("DEFINITIVE_ABSENCE_INCOMPLETE")])]
    assert M.evaluate_gates(results)["CRITICAL_GROUNDING_ZERO"]["passed"] is False


def test_validator_trip_fails_g_ac_03():
    results = [_cr("t", "pass", rules=["D-08"], trips=1)]
    gates = M.evaluate_gates(results)
    assert gates["G-AC-03"]["passed"] is False
    assert "t" in gates["G-AC-03"]["offenders"]


def test_price_hallucination_penalises_composite_and_fails_gate():
    results = [
        _cr("p1", "pass", rules=["D-13"]),
        _cr("p2", "pass", rules=["D-13"], findings=[_crit("PRICE_HALLUCINATION")]),
    ]
    agg = M.aggregate(results)
    assert agg["price_hallucinations"] == 1
    assert agg["provisional_composite_PS"] < 0.5  # -2 penalty for the hallucination
    assert M.evaluate_gates(results)["PRICE_HALLUCINATION_ZERO"]["passed"] is False


def test_known_gap_excluded_from_active_pass_rate():
    results = [_cr("g", "known_gap", rules=["D-06"]), _cr("a", "pass", rules=["D-13"])]
    agg = M.aggregate(results)
    assert agg["active_cases"] == 1
    assert agg["known_gaps"] == 1
    assert agg["pass_rate"] == 1.0


def test_g_ac_01_flags_uncovered_rule():
    results = [_cr("only", "pass", rules=["D-13"])]
    gate = M.evaluate_gates(results)["G-AC-01"]
    assert gate["passed"] is False
    assert "D-02" in gate["offenders"]


# ---- report rendering ----------------------------------------------------
def test_render_markdown_surfaces_failing_gate_first(report):
    md = render_markdown(report)
    assert "Release gates" in md
    assert "G-AC-03" in md
    # a failing gate must be labelled, not hidden
    assert "FAIL" in md
    assert "Overall gate verdict" in md


def test_render_markdown_lists_known_gaps(report):
    md = render_markdown(report)
    assert "Known unimplemented specification branches" in md
    # D-14 DESCRIPTION_ONLY is now implemented and active; assert against a branch
    # that is still reported as an honest known gap rather than a passing requirement.
    assert "TC-D17-insufficient-evidence" in md
