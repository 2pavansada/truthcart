"""Human-readable evaluation report (M-10).

Renders the ``runner.run_eval`` report dict into concise Markdown. The release
gates are printed FIRST and the overall verdict is derived from the gates, never
from the aggregate pass rate, so a critical grounding / hard-filter / incomplete-
search / template-trip failure cannot be hidden by a high pass percentage.
"""
from __future__ import annotations

from typing import Any

_DECORATIONS = {
    "PASS": "[PASS]", "FAIL": "[FAIL]", "KNOWN_GAP": "[GAP]", "ERROR": "[ERR]",
}


def _status_icon(status: str) -> str:
    return _DECORATIONS.get(status.upper(), "[•]")


def render_markdown(report: dict[str, Any]) -> str:
    meta = report.get("meta", {})
    metrics = report.get("metrics", {})
    gates = report.get("gates", {})
    results = report.get("results", [])
    overall = report.get("overall_pass", False)

    lines: list[str] = []
    lines.append("# TruthCart AI — Evaluation Report (M-10)")
    lines.append("")
    lines.append(
        f"- Suite: `{meta.get('suite','dev')}` · Mode: `{meta.get('mode','template')}` · "
        f"Generated: {meta.get('generated_at_utc','?')}"
    )
    lines.append(f"- DB: `{meta.get('db','?')}` · Duration: {meta.get('duration_ms','?')} ms")
    lines.append(f"- Framework: {meta.get('framework','eval/runner.py')}")
    verdict = "PASS" if overall else "FAIL"
    lines.append(f"- **Overall gate verdict: {_status_icon(verdict)} {verdict}** "
                 "(driven by release gates, not by the aggregate pass rate)")
    lines.append("")

    # ---- Release gates (printed first; safety-critical) ----
    lines.append("## Release gates")
    lines.append("")
    lines.append("| Gate | Result | Detail | Offenders |")
    lines.append("|---|---|---|---|")
    for name, g in gates.items():
        icon = "[PASS]" if g.get("passed") else "[FAIL]"
        offenders = ", ".join(g.get("offenders", [])[:8]) or "—"
        if len(g.get("offenders", [])) > 8:
            offenders += f" (+{len(g['offenders']) - 8} more)"
        lines.append(f"| {name} | {icon} {'PASS' if g.get('passed') else 'FAIL'} | "
                     f"{g.get('detail','')} | {offenders} |")
    lines.append("")

    # ---- Aggregate metrics ----
    lines.append("## Aggregate metrics (provisional — not the official evaluator)")
    lines.append("")
    lines.append(f"- Cases: {metrics.get('total_cases', 0)} "
                 f"(active {metrics.get('active_cases', 0)}, "
                 f"passed {metrics.get('passed', 0)}, failed {metrics.get('failed', 0)}, "
                 f"known gaps {metrics.get('known_gaps', 0)})")
    lines.append(f"- Pass rate (active only): {metrics.get('pass_rate', 0):.2%}")
    lines.append(f"- Groundedness: {metrics.get('groundedness', 0):.2%}")
    lines.append(f"- Price hallucinations: {metrics.get('price_hallucinations', 0)} "
                 "(release gate must be 0)")
    lines.append(f"- Critical findings: {metrics.get('critical_findings', 0)}")
    lines.append(f"- Unverifiable invariants: {metrics.get('unverifiable', 0)}")
    lines.append(f"- Provisional composite PS: {metrics.get('provisional_composite_PS', 0)}")
    lines.append("")

    # ---- Critical failures first ----
    critical = [r for r in results if r.get("status") in ("fail", "error")]
    if critical:
        lines.append("## Critical / failing cases (must triage)")
        lines.append("")
        for r in critical:
            lines.append(f"- {_status_icon(r['status'])} `{r['case_id']}` "
                         f"[{', '.join(r.get('rules', []))}] — {r['status']}")
            for reason in r.get("failure_reasons", [])[:6]:
                lines.append(f"    - {reason}")
        lines.append("")

    # ---- Known gaps ----
    gaps = [r for r in results if r.get("status") == "known_gap"]
    if gaps:
        lines.append("## Known unimplemented specification branches (reported honestly)")
        lines.append("")
        lines.append("These are NOT counted as passing requirements.")
        lines.append("")
        for r in gaps:
            lines.append(f"- [GAP] `{r['case_id']}` [{', '.join(r.get('rules', []))}] "
                         f"→ decision `{r.get('final_decision')}` reason `{r.get('final_reason')}` "
                         f"(validator trips: {r.get('validator_trips', 0)})")
            if r.get("gap_note"):
                lines.append(f"    - {r['gap_note']}")
        lines.append("")

    # ---- Full case table ----
    lines.append("## All cases")
    lines.append("")
    lines.append("| Case | Rules | Status | Decision | Reason | Trips | Products |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in results:
        prods = ", ".join(r.get("returned_products", [])[:5]) or "—"
        if len(r.get("returned_products", [])) > 5:
            prods += " …"
        lines.append(
            f"| `{r['case_id']}` | {', '.join(r.get('rules', []))} | "
            f"{_status_icon(r['status'])} {r['status']} | {r.get('final_decision','—')} | "
            f"{r.get('final_reason') or '—'} | {r.get('validator_trips', 0)} | {prods} |"
        )
    lines.append("")

    # ---- Requirement branch coverage (independent of release gates) ----
    coverage = report.get("requirement_coverage")
    if coverage:
        summary = coverage.get("summary", {})
        lines.append("## Requirement Coverage")
        lines.append("")
        lines.append(
            "Branch evidence is derived from executed case outcomes and run-scoped pytest "
            "events. Inventory mappings alone do not establish coverage. This minimum "
            "inventory is not an exhaustive proof of every sub-branch in the contract."
        )
        lines.append("")
        lines.append(
            f"- Required inventoried branches: {summary.get('required_branches', 0)} "
            f"(verified {summary.get('verified', 0)}, untested {summary.get('untested', 0)}, "
            f"failing {summary.get('failing', 0)})"
        )
        lines.append(f"- Fault-test evidence run ID: `{coverage.get('evidence_run_id') or 'not supplied'}`")
        lines.append(
            f"- Coverage audit references: "
            f"{'valid' if coverage.get('audit_valid') else 'INVALID'}; "
            f"fault evidence: {coverage.get('evidence_state', 'unknown')}"
        )
        lines.append("")
        lines.append("| Requirement / branch | Status | Ordinary evaluation | Fault injection |")
        lines.append("|---|---|---|---|")
        for requirement in coverage.get("requirements", []):
            for branch in requirement.get("branches", []):
                ordinary = "; ".join(
                    f"{item['case_id']}: {item['status']}"
                    + (f" ({item['observed'].get('decision')} / {item['observed'].get('reason') or '—'})"
                       if item.get("observed") else "")
                    for item in branch.get("ordinary_evaluation", [])
                ) or "—"
                fault = "; ".join(
                    f"{item['test_id']}: {item['status']}"
                    for item in branch.get("fault_injection", [])
                ) or "—"
                branch_label = f"`{branch['branch_id']}`"
                if branch.get("description"):
                    branch_label += f" — {branch['description']}"
                lines.append(
                    f"| `{requirement['requirement_id']}` / {branch_label} "
                    f"| {branch['status']} | {ordinary} | {fault} |"
                )
        lines.append("")
        limitations = [
            f"`{requirement['requirement_id']}`: {limitation}"
            for requirement in coverage.get("requirements", [])
            for limitation in requirement.get("known_limitations", [])
        ]
        if limitations:
            lines.append("Known inventory limitations:")
            lines.extend(f"- {item}" for item in limitations)
            lines.append("")
        inventory_errors = coverage.get("inventory_errors", [])
        if inventory_errors:
            lines.append("Inventory/evidence reference errors:")
            lines.extend(f"- {error}" for error in inventory_errors)
            lines.append("")

    # ---- Auditor finding kinds ----
    fc = metrics.get("finding_kind_counts", {})
    if fc:
        lines.append("## Auditor finding kinds")
        lines.append("")
        for kind, n in sorted(fc.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"- `{kind}`: {n}")
        lines.append("")

    return "\n".join(lines)
