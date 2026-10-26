"""Evaluation runner (M-10).

Loads the dev-set cases, drives them through the real ``Orchestrator.handle``
service interface (no business logic duplicated here), audits every final
response with the independent :class:`eval.auditor.Auditor`, compares the result
against the spec-derived expectation, computes the release gates and writes a
machine-readable + human-readable report.

Usage:
    python -m eval.runner [--suite dev] [--out data/reports] [--quiet]

Exit status is non-zero when any required gate fails, so a critical grounding /
hard-filter / incomplete-search / template-trip failure can never be masked by a
high aggregate pass rate.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.container import build_container
from app.orchestrator import Orchestrator
from app.schemas.chat import ChatRequest, ClientContext, ConstraintOpIn
from app.settings import load_settings
from eval.auditor import Auditor, Finding
from eval import metrics as metrics_mod
from eval.dataset import EvalCase, DEFAULT_DEV_SET, load_cases
from eval.requirement_coverage import audit_coverage, load_inventory


@dataclass
class CaseResult:
    case_id: str
    rules: tuple[str, ...]
    tags: tuple[str, ...]
    status: str  # "pass" | "fail" | "known_gap" | "error"
    final_decision: str | None
    final_reason: str | None
    validator_trips: int
    findings: list[Finding] = field(default_factory=list)
    failure_reasons: list[str] = field(default_factory=list)
    returned_products: list[str] = field(default_factory=list)
    gap_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "rules": list(self.rules),
            "tags": list(self.tags),
            "status": self.status,
            "final_decision": self.final_decision,
            "final_reason": self.final_reason,
            "validator_trips": self.validator_trips,
            "returned_products": self.returned_products,
            "failure_reasons": self.failure_reasons,
            "gap_note": self.gap_note,
            "findings": [f.to_dict() for f in self.findings],
        }


def _actual_reason(resp) -> str | None:
    if resp.refusal is not None:
        return str(resp.refusal.reason.value)
    if resp.clarification is not None:
        return str(resp.clarification.reason.value)
    return None


def run_case(case: EvalCase, orchestrator: Orchestrator, auditor: Auditor) -> CaseResult:
    sid = f"eval_{case.case_id}"
    orchestrator.reset(sid)
    responses = []
    try:
        for idx, turn in enumerate(case.turns):
            req = ChatRequest(
                session_id=sid,
                message=turn.message,
                constraint_ops=[ConstraintOpIn(**op) for op in turn.constraint_ops],
                client_context=ClientContext(**(turn.client_context or {})),
            )
            resp = orchestrator.handle(req, f"eval_{case.case_id}_t{idx}")
            responses.append(resp)
    except Exception as exc:  # a runtime crash is a hard failure
        return CaseResult(
            case_id=case.case_id, rules=case.rules, tags=case.tags, status="error",
            final_decision=None, final_reason=None, validator_trips=0,
            findings=[], failure_reasons=[f"exception: {exc!r}"],
        )

    trips = sum(r.metrics.validator_trips for r in responses)
    final = responses[-1]
    findings = auditor.audit(final, case.case_id)
    # audit intermediate turns too for safety (any critical finding is captured)
    for r in responses[:-1]:
        findings += auditor.audit(r, case.case_id)

    returned = [p.product_id for p in final.products]
    reasons: list[str] = []
    exp = case.expected

    if exp.decision is not None and str(final.decision.value) != exp.decision:
        reasons.append(f"decision {final.decision.value} != expected {exp.decision}")
    if exp.reason is not None and _actual_reason(final) != exp.reason:
        reasons.append(f"reason {_actual_reason(final)} != expected {exp.reason}")

    ret_set = set(returned)
    if exp.must_include_products and not set(exp.must_include_products) <= ret_set:
        missing = set(exp.must_include_products) - ret_set
        reasons.append(f"missing required products: {sorted(missing)}")
    if exp.must_exclude_products and ret_set & set(exp.must_exclude_products):
        bad = ret_set & set(exp.must_exclude_products)
        reasons.append(f"forbidden products returned: {sorted(bad)}")
    if exp.exact_products is not None and ret_set != set(exp.exact_products):
        reasons.append(f"returned {sorted(ret_set)} != expected {sorted(exp.exact_products)}")

    has_absence = any(c.type == "ABSENCE" for c in final.claims)
    if exp.expect_absence is True and not has_absence:
        reasons.append("expected a grounded ABSENCE claim, none present")
    if exp.no_definitive_absence and has_absence:
        reasons.append("definitive ABSENCE claim present but must not be")

    hard_errors = [f for f in findings if f.severity in ("SEVERE", "ERROR")]
    critical = [f for f in findings if f.kind in metrics_mod.CRITICAL_KINDS]

    if case.status == "known_gap":
        # Reported honestly; a critical grounding failure is still escalated.
        status = "fail" if critical else "known_gap"
    else:
        if reasons or hard_errors or trips > 0:
            status = "fail"
        else:
            status = "pass"

    return CaseResult(
        case_id=case.case_id, rules=case.rules, tags=case.tags, status=status,
        final_decision=str(final.decision.value), final_reason=_actual_reason(final),
        validator_trips=trips, findings=findings,
        failure_reasons=reasons + [f"{f.severity}:{f.kind}: {f.detail}" for f in hard_errors],
        returned_products=returned, gap_note=case.gap_note,
    )


def run_eval(cases: list[EvalCase] | None = None, cases_path: Path | None = None,
             settings=None, fault_test_evidence_path: Path | None = None,
             coverage_run_id: str | None = None) -> dict[str, Any]:
    settings = settings or load_settings()
    container = build_container(settings)
    orchestrator = Orchestrator(container)
    db_path = settings.base_dir / settings.app.paths["sqlite_db"]
    auditor = Auditor(db_path)

    if cases is None:
        cases = load_cases(cases_path or DEFAULT_DEV_SET)

    started = time.time()
    results = [run_case(c, orchestrator, auditor) for c in cases]
    duration_ms = round((time.time() - started) * 1000, 2)

    agg = metrics_mod.aggregate(results)
    gates = metrics_mod.evaluate_gates(results)
    ok = metrics_mod.overall_pass(gates)
    fault_test_evidence = None
    if fault_test_evidence_path is not None:
        fault_test_evidence = json.loads(
            fault_test_evidence_path.read_text(encoding="utf-8")
        )
    requirement_coverage = audit_coverage(
        load_inventory(), cases, results,
        fault_test_evidence=fault_test_evidence,
        expected_run_id=coverage_run_id,
    )

    auditor.close()
    return {
        "meta": {
            "suite": "dev",
            "mode": "template",
            "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "db": str(db_path),
            "duration_ms": duration_ms,
            "framework": "eval/runner.py (provisional; not the official evaluator)",
        },
        "gates": gates,
        "overall_pass": ok,
        "metrics": agg,
        "results": [r.to_dict() for r in results],
        "requirement_coverage": requirement_coverage,
    }


def _cli(argv=None) -> int:
    parser = argparse.ArgumentParser(description="TruthCart evaluation runner (M-10)")
    parser.add_argument("--suite", default="dev", choices=["dev"])
    parser.add_argument("--cases", default=str(DEFAULT_DEV_SET))
    parser.add_argument("--out", default=str(Path("data") / "reports"))
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--fault-test-evidence", default=None,
        help="run-scoped JSON emitted by pytest's requirement-coverage reporter",
    )
    parser.add_argument(
        "--coverage-run-id", default=None,
        help="must match the unique run ID used for the pytest evidence",
    )
    args = parser.parse_args(argv)
    if bool(args.fault_test_evidence) != bool(args.coverage_run_id):
        parser.error("--fault-test-evidence and --coverage-run-id must be supplied together")

    report = run_eval(
        cases_path=Path(args.cases),
        fault_test_evidence_path=(Path(args.fault_test_evidence)
                                  if args.fault_test_evidence else None),
        coverage_run_id=args.coverage_run_id,
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    json_path = out_dir / f"eval_{stamp}.json"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path = out_dir / f"eval_{stamp}.md"
    from eval.report import render_markdown
    md_path.write_text(render_markdown(report), encoding="utf-8")

    if not args.quiet:
        print(render_markdown(report))
        print(f"\n[eval] JSON report -> {json_path}")
        print(f"[eval] Markdown report -> {md_path}")

    return 0 if report["overall_pass"] else 1


def main() -> None:
    sys.exit(_cli())


if __name__ == "__main__":
    main()
