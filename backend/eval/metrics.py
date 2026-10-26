"""Provisional evaluation metrics + release-gate evaluation (M-10).

All metrics here are explicitly PROVISIONAL (ours, not the official evaluator)
per ``EVALUATION_PLAN.md`` §5. They are never merged with an official score. A
high aggregate is deliberately *unable* to hide a critical grounding / hard-filter
/ incomplete-search failure: the gates in :func:`evaluate_gates` are computed
independently of the aggregate pass rate.

Input is a list of ``runner.CaseResult`` objects.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

ALL_RULES = [f"D-{i:02d}" for i in range(1, 18)] + ["D-08b"]
# D-08b is a sub-branch of D-08; G-AC-01 enumerates D-01..D-17.
GATED_RULES = [f"D-{i:02d}" for i in range(1, 18)]

# Finding kinds that represent a critical grounding / safety failure. A single
# occurrence anywhere must fail the run regardless of the aggregate.
CRITICAL_KINDS = {
    "PRICE_HALLUCINATION",
    "PRICE_MISMATCH",
    "HARD_FILTER_VIOLATION",
    "NULL_PRICE_UNDER_CAP",
    "DEFINITIVE_ABSENCE_INCOMPLETE",
    "NONEXISTENT_PRODUCT",
    "UNSUPPORTED_CLAIM",
    "UNGROUNDED_ABSENCE",
}


def aggregate(results: list[Any]) -> dict[str, Any]:
    active = [r for r in results if r.status != "known_gap"]
    passed = [r for r in active if r.status == "pass"]
    failed = [r for r in active if r.status == "fail"]
    gaps = [r for r in results if r.status == "known_gap"]

    all_findings = [f for r in results for f in r.findings]
    kind_counts = Counter(f.kind for f in all_findings)
    critical = [f for f in all_findings if f.kind in CRITICAL_KINDS]
    unverifiable = [f for f in all_findings if f.severity == "UNVERIFIABLE"]

    answered = [r for r in active if (r.final_decision or "") == "ANSWER"]
    grounded_bad = [r for r in answered
                    if any(f.kind in ("UNSUPPORTED_CLAIM", "PRICE_MISMATCH",
                                      "PRICE_HALLUCINATION") for f in r.findings)]

    # Provisional composite PS: +1 pass, 0 fail, -2 if any price hallucination.
    ps = 0.0
    for r in active:
        if any(f.kind == "PRICE_HALLUCINATION" for f in r.findings):
            ps += -2
        elif r.status == "pass":
            ps += 1
        else:
            ps += 0
    ps = round(ps / len(active), 4) if active else 0.0

    return {
        "total_cases": len(results),
        "active_cases": len(active),
        "passed": len(passed),
        "failed": len(failed),
        "known_gaps": len(gaps),
        "pass_rate": round(len(passed) / len(active), 4) if active else 0.0,
        "groundedness": round(1 - len(grounded_bad) / len(answered), 4) if answered else 1.0,
        "price_hallucinations": kind_counts.get("PRICE_HALLUCINATION", 0),
        "critical_findings": len(critical),
        "unverifiable": len(unverifiable),
        "finding_kind_counts": dict(kind_counts),
        "provisional_composite_PS": ps,
    }


def evaluate_gates(results: list[Any]) -> dict[str, dict[str, Any]]:
    """Release gates. Each gate is independent of the aggregate pass rate."""
    active = [r for r in results if r.status != "known_gap"]

    # G-AC-01: every rule D-01..D-17 has >=1 end-to-end scenario in the dev set.
    covered = {rule for r in results for rule in r.rules}
    missing_rules = [d for d in GATED_RULES if d not in covered]
    g_ac_01 = {
        "passed": not missing_rules,
        "detail": f"{len(GATED_RULES) - len(missing_rules)}/{len(GATED_RULES)} rules covered by an e2e scenario",
        "offenders": missing_rules,
    }

    # G-AC-03: in template mode the validator rejects nothing on the dev set.
    trip_cases = sorted({r.case_id for r in results if r.validator_trips > 0})
    g_ac_03 = {
        "passed": not trip_cases,
        "detail": f"validator trips on {len(trip_cases)} case(s)",
        "offenders": trip_cases,
    }

    # Price-hallucination release gate must be exactly 0 (G-AC-05 / §5).
    ph_cases = sorted({f.case_id for r in results for f in r.findings
                       if f.kind == "PRICE_HALLUCINATION"})
    price_gate = {
        "passed": not ph_cases,
        "detail": f"{len(ph_cases)} case(s) with a price hallucination",
        "offenders": ph_cases,
    }

    # Critical grounding / hard-filter / incomplete-search gate.
    crit_cases = sorted({f.case_id for r in results for f in r.findings
                         if f.kind in CRITICAL_KINDS})
    critical_gate = {
        "passed": not crit_cases,
        "detail": f"{len(crit_cases)} case(s) with a critical grounding/hard-filter finding",
        "offenders": crit_cases,
    }

    # No active case may fail.
    fail_cases = sorted({r.case_id for r in active if r.status == "fail"})
    cases_gate = {
        "passed": not fail_cases,
        "detail": f"{len(fail_cases)} active case(s) failed",
        "offenders": fail_cases,
    }

    return {
        "G-AC-01": g_ac_01,
        "G-AC-03": g_ac_03,
        "PRICE_HALLUCINATION_ZERO": price_gate,
        "CRITICAL_GROUNDING_ZERO": critical_gate,
        "ALL_ACTIVE_CASES_PASS": cases_gate,
    }


def overall_pass(gates: dict[str, dict[str, Any]]) -> bool:
    return all(g["passed"] for g in gates.values())
