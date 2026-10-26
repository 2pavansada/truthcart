"""Optional run-scoped structured evidence for requirement fault tests."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest


def pytest_addoption(parser):
    group = parser.getgroup("requirement coverage")
    group.addoption("--requirement-coverage-output", default=None,
                    help="write collected and executed requirement-test evidence to this JSON file")
    group.addoption("--requirement-coverage-run-id", default=None,
                    help="unique run token paired with the evaluation report")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "requirement_coverage(test_id): stable ID referenced by coverage inventory"
    )
    output = config.getoption("--requirement-coverage-output")
    run_id = config.getoption("--requirement-coverage-run-id")
    if bool(output) != bool(run_id):
        raise pytest.UsageError(
            "--requirement-coverage-output and --requirement-coverage-run-id must be supplied together"
        )
    if output and Path(output).exists():
        raise pytest.UsageError(
            "requirement coverage output already exists; choose a fresh per-run path"
        )
    config._requirement_coverage = {
        "output": output,
        "run_id": run_id,
        "tests": {},
        "deselected_items": [],
    }


def pytest_collection_finish(session):
    state = session.config._requirement_coverage
    if not state["output"]:
        return
    deselected_nodeids = {item.nodeid for item in state["deselected_items"]}
    for item in [*session.items, *state["deselected_items"]]:
        mark = item.get_closest_marker("requirement_coverage")
        if mark is None:
            continue
        test_id = mark.kwargs.get("test_id")
        if not test_id:
            continue
        status = "deselected" if item.nodeid in deselected_nodeids else "not_executed"
        state["tests"].setdefault(test_id, []).append({
            "coverage_test_id": test_id,
            "nodeid": item.nodeid,
            "status": status,
            "events": [],
        })


def pytest_deselected(items):
    if not items:
        return
    state = items[0].config._requirement_coverage
    for item in items:
        state["deselected_items"].append(item)
        mark = item.get_closest_marker("requirement_coverage")
        if mark is None:
            continue
        test_id = mark.kwargs.get("test_id")
        for record in state["tests"].get(test_id, []):
            if record["nodeid"] == item.nodeid:
                record["status"] = "deselected"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    state = item.config._requirement_coverage
    if not state["output"]:
        return
    mark = item.get_closest_marker("requirement_coverage")
    if mark is None:
        return
    test_id = mark.kwargs.get("test_id")
    record = next((r for r in state["tests"].get(test_id, [])
                   if r["nodeid"] == item.nodeid), None)
    if record is None:
        return
    if report.when == "call":
        if report.skipped:
            record["status"] = "skipped"
        else:
            record["status"] = "passed" if report.passed else "failed"
    elif report.when == "setup" and (report.failed or report.skipped):
        record["status"] = "failed" if report.failed else "skipped"


@pytest.fixture
def record_requirement_coverage(request):
    """Emit branch evidence from a marked test after its assertions succeed."""
    def emit(test_id: str, requirement_id: str, branch_id: str, outcome: str) -> None:
        mark = request.node.get_closest_marker("requirement_coverage")
        if mark is None or mark.kwargs.get("test_id") != test_id:
            pytest.fail("coverage event test_id must match the test's registered marker")
        events = getattr(request.node, "_requirement_coverage_events", None)
        if events is None:
            events = []
            request.node._requirement_coverage_events = events
        events.append({
            "requirement_id": requirement_id,
            "branch_id": branch_id,
            "outcome": outcome,
        })
    return emit


def pytest_sessionfinish(session, exitstatus):
    state = session.config._requirement_coverage
    if not state["output"]:
        return
    # Pull events only from completed test items. The audit independently
    # requires both a passing result and an exact expected event.
    for item in session.items:
        mark = item.get_closest_marker("requirement_coverage")
        if mark is None:
            continue
        test_id = mark.kwargs.get("test_id")
        record = next((r for r in state["tests"].get(test_id, [])
                       if r["nodeid"] == item.nodeid), None)
        if record is not None:
            record["events"] = getattr(item, "_requirement_coverage_events", [])
    output = Path(state["output"])
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "run_id": state["run_id"],
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "pytest_exit_status": int(exitstatus),
        "tests": [record for records in state["tests"].values() for record in records],
    }
    with output.open("x", encoding="utf-8") as evidence_file:
        json.dump(payload, evidence_file, indent=2)
