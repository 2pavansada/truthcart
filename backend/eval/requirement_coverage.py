"""Evidence-based requirement branch coverage, separate from release gates."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_INVENTORY = Path(__file__).with_name("requirement_coverage.json")


def load_inventory(path: Path = DEFAULT_INVENTORY) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _unique_index(items, key):
    index: dict[str, list[Any]] = {}
    for item in items:
        value = getattr(item, key, None)
        if value is not None:
            index.setdefault(value, []).append(item)
    return index


def _matches_observation(result, expected: dict[str, Any]) -> bool:
    observed = {
        "decision": result.final_decision,
        "reason": result.final_reason,
        "validator_trips": result.validator_trips,
        "returned_products": result.returned_products,
    }
    return all(observed.get(key) == value for key, value in expected.items())


def validate_inventory(inventory, cases) -> list[str]:
    """Validate schema and ordinary-case references; pytest refs need run evidence."""
    issues: list[str] = []
    if inventory.get("schema_version") != 1:
        issues.append("unsupported or missing schema_version (expected 1)")
    requirements = inventory.get("requirements")
    if not isinstance(requirements, list):
        return issues + ["requirements must be a list"]

    case_index = _unique_index(cases, "case_id")
    requirement_ids: set[str] = set()
    branch_ids: set[str] = set()
    test_ids: set[str] = set()
    for requirement in requirements:
        if not isinstance(requirement, dict):
            issues.append("requirement entries must be objects")
            continue
        req_id = requirement.get("requirement_id")
        if not isinstance(req_id, str) or not req_id:
            issues.append("requirement_id is required")
            continue
        if req_id in requirement_ids:
            issues.append(f"duplicate requirement_id: {req_id}")
        requirement_ids.add(req_id)
        branches = requirement.get("branches")
        if not isinstance(branches, list) or not branches:
            issues.append(f"{req_id}: branches must be a non-empty list")
            continue
        for branch in branches:
            if not isinstance(branch, dict):
                issues.append(f"{req_id}: branch entries must be objects")
                continue
            branch_id = branch.get("branch_id")
            if not isinstance(branch_id, str) or not branch_id:
                issues.append(f"{req_id}: branch_id is required")
                continue
            if branch_id in branch_ids:
                issues.append(f"duplicate branch_id: {branch_id}")
            branch_ids.add(branch_id)
            if not isinstance(branch.get("required"), bool):
                issues.append(f"{branch_id}: required must be boolean")
            ordinary_refs = branch.get("ordinary_cases", [])
            fault_refs = branch.get("fault_injection_tests", [])
            if not isinstance(ordinary_refs, list):
                issues.append(f"{branch_id}: ordinary_cases must be a list")
                ordinary_refs = []
            if not isinstance(fault_refs, list):
                issues.append(f"{branch_id}: fault_injection_tests must be a list")
                fault_refs = []
            if branch.get("required") and not ordinary_refs and not fault_refs and not branch.get("description"):
                issues.append(f"{branch_id}: an uncovered branch needs a description")
            for case_ref in ordinary_refs:
                case_id = case_ref.get("case_id") if isinstance(case_ref, dict) else None
                if not case_id:
                    issues.append(f"{branch_id}: ordinary case reference needs case_id")
                elif case_id not in case_index:
                    issues.append(f"{branch_id}: unresolved ordinary case {case_id}")
                elif len(case_index[case_id]) != 1:
                    issues.append(f"{branch_id}: ambiguous ordinary case {case_id}")
                elif isinstance(case_ref, dict) and isinstance(case_ref.get("expected"), dict):
                    expected = case_ref["expected"]
                    case_expected = case_index[case_id][0].expected
                    for field in ("decision", "reason"):
                        declared = expected.get(field)
                        canonical = getattr(case_expected, field, None)
                        if declared is not None and canonical is not None and declared != canonical:
                            issues.append(
                                f"{branch_id}: inventory {field} for {case_id} conflicts "
                                "with the evaluation case expectation"
                            )
                if isinstance(case_ref, dict) and not isinstance(case_ref.get("expected"), dict):
                    issues.append(f"{branch_id}: ordinary case {case_id} needs expected observation")
            for test_ref in fault_refs:
                if not isinstance(test_ref, dict) or not test_ref.get("test_id"):
                    issues.append(f"{branch_id}: fault test reference needs test_id")
                else:
                    test_id = test_ref["test_id"]
                    if test_id in test_ids:
                        issues.append(f"duplicate fault test reference: {test_id}")
                    test_ids.add(test_id)
                    if not isinstance(test_ref.get("expected_event"), dict):
                        issues.append(f"{branch_id}: fault test {test_id} needs expected_event")
                    else:
                        event = test_ref["expected_event"]
                        if event.get("requirement_id") != req_id or event.get("branch_id") != branch_id:
                            issues.append(f"{branch_id}: expected event must identify its mapped requirement and branch")

    if requirement_ids != {f"D-{i:02d}" for i in range(1, 18)}:
        missing = sorted({f"D-{i:02d}" for i in range(1, 18)} - requirement_ids)
        extra = sorted(requirement_ids - {f"D-{i:02d}" for i in range(1, 18)})
        issues.append(f"requirement set mismatch; missing={missing}, extra={extra}")
    return issues


def audit_coverage(
    inventory: dict[str, Any],
    cases,
    case_results,
    fault_test_evidence: dict[str, Any] | None = None,
    expected_run_id: str | None = None,
) -> dict[str, Any]:
    """Derive branch states from executed eval results and current pytest evidence."""
    issues = validate_inventory(inventory, cases)
    cases_by_id = _unique_index(cases, "case_id")
    results_by_id = _unique_index(case_results, "case_id")
    evidence_run_id = (fault_test_evidence or {}).get("run_id")
    evidence_matches = bool(
        fault_test_evidence
        and expected_run_id
        and evidence_run_id == expected_run_id
    )
    tests = (fault_test_evidence or {}).get("tests", []) if evidence_matches else []
    tests_by_id: dict[str, list[dict[str, Any]]] = {}
    for test in tests:
        if isinstance(test, dict) and isinstance(test.get("coverage_test_id"), str):
            tests_by_id.setdefault(test["coverage_test_id"], []).append(test)

    requirements_out = []
    summary = {"verified": 0, "untested": 0, "failing": 0}
    required_total = 0

    for requirement in inventory.get("requirements", []):
        req_out = {"requirement_id": requirement["requirement_id"], "branches": [],
                   "known_limitations": requirement.get("known_limitations", [])}
        for branch in requirement.get("branches", []):
            branch_id = branch["branch_id"]
            ordinary_out = []
            ordinary_verified = False
            branch_failed = False
            for ref in branch.get("ordinary_cases", []):
                case_id = ref["case_id"]
                case = cases_by_id.get(case_id, [])
                result = results_by_id.get(case_id, [])
                if len(case) != 1:
                    status = "unresolved" if not case else "ambiguous"
                    observed = None
                    issues.append(f"{branch_id}: {status} ordinary case {case_id}")
                elif len(result) == 0:
                    status, observed = "not_executed", None
                elif len(result) != 1:
                    status, observed = "ambiguous", None
                    issues.append(f"{branch_id}: ambiguous ordinary result {case_id}")
                else:
                    actual = result[0]
                    observed = {"decision": actual.final_decision,
                                "reason": actual.final_reason,
                                "validator_trips": actual.validator_trips,
                                "returned_products": actual.returned_products}
                    expected_match = _matches_observation(actual, ref["expected"])
                    role = ref.get("role", "branch")
                    if role == "ordinary-gap":
                        status = "ordinary_gap" if actual.status == "known_gap" and expected_match else (
                            "failed" if actual.status in ("fail", "error") or not expected_match else "ordinary_only")
                    elif actual.status == "pass" and expected_match:
                        status = "verified"
                        ordinary_verified = True
                    elif actual.status == "known_gap":
                        status = "ordinary_gap"
                    else:
                        status = "failed"
                    branch_failed |= status == "failed"
                ordinary_out.append({"case_id": case_id, "status": status,
                                     "observed": observed, "role": ref.get("role", "branch")})

            fault_out = []
            fault_verified = False
            for ref in branch.get("fault_injection_tests", []):
                test_id = ref["test_id"]
                if not fault_test_evidence:
                    status, detail = "not_provided", "no pytest evidence supplied"
                    matching_events = []
                elif not evidence_matches:
                    status, detail = "stale", "pytest run_id does not match requested run_id"
                    matching_events = []
                else:
                    matches = tests_by_id.get(test_id, [])
                    if not matches:
                        status, detail, matching_events = "unresolved", "test was not collected", []
                    elif len(matches) > 1:
                        status, detail, matching_events = "ambiguous", "coverage test ID is not unique", []
                        issues.append(f"{branch_id}: ambiguous fault test {test_id}")
                    else:
                        test = matches[0]
                        events = test.get("events", [])
                        wanted = ref["expected_event"]
                        matching_events = [event for event in events
                                           if all(event.get(k) == v for k, v in wanted.items())]
                        test_status = test.get("status")
                        if test_status == "deselected":
                            status, detail = "deselected", "test was deselected"
                        elif test_status == "not_executed":
                            status, detail = "not_executed", "test collected but not executed"
                        elif test_status == "skipped":
                            status, detail = "skipped", "test was skipped"
                        elif test_status == "failed":
                            status, detail = "failed", "test failed"
                            branch_failed = True
                        elif test_status == "passed" and matching_events:
                            status, detail = "verified", "passed with matching runtime event"
                            fault_verified = True
                        elif test_status == "passed":
                            status, detail = "passed_without_event", "test passed without the required event"
                        else:
                            status, detail = "unresolved", f"unknown test status: {test_status}"
                fault_out.append({"test_id": test_id, "status": status,
                                 "detail": detail, "events": matching_events})

            required = bool(branch.get("required"))
            overall = "failing" if branch_failed else (
                "verified" if ordinary_verified or fault_verified else "untested")
            if required:
                required_total += 1
                summary[overall] += 1
            req_out["branches"].append({
                "branch_id": branch_id,
                "description": branch.get("description"),
                "required": required,
                "status": overall,
                "ordinary_evaluation": ordinary_out,
                "fault_injection": fault_out,
            })
        requirements_out.append(req_out)

    return {
        "schema_version": inventory.get("schema_version"),
        "run_id": expected_run_id,
        "evidence_run_id": evidence_run_id,
        "evidence_state": (
            "not_provided" if fault_test_evidence is None else
            "current" if evidence_matches else "stale"
        ),
        "audit_valid": not issues,
        "summary": {**summary, "required_branches": required_total},
        "inventory_errors": issues,
        "requirements": requirements_out,
    }
