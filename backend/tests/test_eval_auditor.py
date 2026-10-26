"""Independent-auditor tests for the M-10 evaluation framework.

These verify that the auditor independently detects the safety-critical
invariants, including ones that cannot be produced end-to-end through the
orchestrator (a definitive absence claim over an INCOMPLETE retrieval; a NULL
price returned under a budget cap). The auditor reads the trusted SQLite demo
catalogue and inspects responses purely by duck typing, so lightweight
stand-ins are used to deterministically inject each violation.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.enums import AttrBasis
from app.settings import load_settings
from eval.auditor import ERROR, SEVERE, Auditor


@pytest.fixture(scope="module")
def auditor():
    settings = load_settings()
    db_path = settings.base_dir / settings.app.paths["sqlite_db"]
    a = Auditor(db_path)
    yield a
    a.close()


def _resp(claims=(), products=(), evidence=(), clarification=None,
          suggestions=(), answer_text=""):
    return SimpleNamespace(
        claims=list(claims), products=list(products), evidence=list(evidence),
        clarification=clarification, suggestions=list(suggestions),
        answer_text=answer_text,
    )


def _claim(cid, ctype, scope, pid, value, ev_ids):
    return SimpleNamespace(claim_id=cid, type=ctype, scope=scope, product_id=pid,
                           value=value, evidence_ids=list(ev_ids), sentence="")


def _prod_ev(eid, pid, field, value, available=True, excerpt=None, display=None):
    return SimpleNamespace(evidence_id=eid, kind="PRODUCT_FIELD", product_id=pid,
                           field=field, value=value, available=available,
                           excerpt=excerpt, display_value=display)


def _search_ev(eid, manifest):
    return SimpleNamespace(evidence_id=eid, kind="SEARCH_MANIFEST", search=manifest)


def _kinds(findings):
    return {f.kind for f in findings}


def _hard(findings):
    return [f for f in findings if f.severity in (SEVERE, ERROR)]


# ---- happy path ----------------------------------------------------------
def test_grounded_price_claim_yields_no_hard_finding(auditor):
    ev = _prod_ev("prod:SH-BLK-001:price", "SH-BLK-001", "price", 149900)
    claim = _claim("c1", "PRICE", "PRODUCT", "SH-BLK-001",
                   {"price_minor": 149900}, ["prod:SH-BLK-001:price"])
    resp = _resp([claim], evidence=[ev], answer_text="The price of “SH-BLK-001” is ₹1,499.00.")
    assert _hard(auditor.audit(resp, "t")) == []


# ---- nonexistent product -------------------------------------------------
def test_nonexistent_product_detected(auditor):
    card = SimpleNamespace(product_id="NOPE-999", title="x", origin="LOOKUP",
                           applied_search_id=None, facts=[])
    findings = auditor.audit(_resp(products=[card]), "t")
    assert "NONEXISTENT_PRODUCT" in _kinds(findings)


def test_nonexistent_product_in_claim_evidence(auditor):
    claim = _claim("c1", "PRICE", "PRODUCT", "NOPE-999",
                   {"price_minor": 100}, ["prod:NOPE-999:price"])
    ev = _prod_ev("prod:NOPE-999:price", "NOPE-999", "price", 100)
    findings = auditor.audit(_resp([claim], evidence=[ev]), "t")
    assert "NONEXISTENT_PRODUCT" in _kinds(findings)


# ---- missing / unresolved evidence --------------------------------------
def test_unsupported_claim_without_evidence(auditor):
    claim = _claim("c1", "PRICE", "PRODUCT", "SH-BLK-001", {"price_minor": 149900}, [])
    findings = auditor.audit(_resp([claim]), "t")
    assert "UNSUPPORTED_CLAIM" in _kinds(findings)


def test_citation_not_in_bundle(auditor):
    claim = _claim("c1", "PRICE", "PRODUCT", "SH-BLK-001",
                   {"price_minor": 149900}, ["prod:SH-BLK-001:price"])
    # evidence bundle intentionally EMPTY -> unresolved citation
    findings = auditor.audit(_resp([claim], []), "t")
    assert "CITATION_UNRESOLVED" in _kinds(findings)


def test_bad_evidence_grammar(auditor):
    claim = _claim("c1", "PRICE", "PRODUCT", "SH-BLK-001",
                   {"price_minor": 149900}, ["weird-id"])
    findings = auditor.audit(_resp([claim], []), "t")
    assert "EVIDENCE_GRAMMAR" in _kinds(findings)


# ---- typed value vs source ----------------------------------------------
def test_price_mismatch_detected(auditor):
    ev = _prod_ev("prod:SH-BLK-001:price", "SH-BLK-001", "price", 149900)
    claim = _claim("c1", "PRICE", "PRODUCT", "SH-BLK-001",
                   {"price_minor": 999999}, ["prod:SH-BLK-001:price"])
    findings = auditor.audit(_resp([claim], evidence=[ev]), "t")
    assert "PRICE_MISMATCH" in _kinds(findings)


def test_null_price_assertion_detected(auditor):
    # SH-NOP-001 has a NULL price in the DB; asserting any price is a violation.
    ev = _prod_ev("prod:SH-NOP-001:price", "SH-NOP-001", "price", None, available=False)
    claim = _claim("c1", "PRICE", "PRODUCT", "SH-NOP-001",
                   {"price_minor": 500}, ["prod:SH-NOP-001:price"])
    findings = auditor.audit(_resp([claim], evidence=[ev]), "t")
    assert "PRICE_MISMATCH" in _kinds(findings)


# ---- currency / price hallucination (G-AC-05) ---------------------------
def test_price_hallucination_detected(auditor):
    # answer quotes a price that is not a cited product price nor a constraint
    resp = _resp(answer_text="Great deal at ₹8,777.00 only.")
    findings = auditor.audit(resp, "t")
    assert "PRICE_HALLUCINATION" in _kinds(findings)


def test_cited_price_is_not_a_hallucination(auditor):
    ev = _prod_ev("prod:SH-BLK-001:price", "SH-BLK-001", "price", 149900)
    card = SimpleNamespace(product_id="SH-BLK-001", title="t", origin="LOOKUP",
                           applied_search_id=None,
                           facts=[SimpleNamespace(field="price", available=True,
                                                  value=149900, display="₹1,499.00")])
    findings = auditor.audit(_resp([], [card], [ev],
                                   answer_text="₹1,499.00"), "t")
    assert "PRICE_HALLUCINATION" not in _kinds(findings)


# ---- absence grounding (G-AC-06) ----------------------------------------
def _manifest(completeness, exact, constraints=()):
    return SimpleNamespace(completeness=completeness, exact_match_count=exact,
                           matched_section_ids=[], applied_constraints=list(constraints),
                           scope="CATALOGUE")


def test_definitive_absence_over_incomplete_is_critical(auditor):
    man = _manifest("INCOMPLETE", 0)
    ev = _search_ev("search:s1", man)
    claim = _claim("c1", "ABSENCE", "CATALOGUE", None, {"searched": "widget"}, ["search:s1"])
    findings = auditor.audit(_resp([claim], evidence=[ev]), "t")
    assert "DEFINITIVE_ABSENCE_INCOMPLETE" in _kinds(findings)


def test_grounded_absence_over_exhaustive_zero_passes(auditor):
    man = _manifest("EXHAUSTIVE", 0)
    ev = _search_ev("search:s1", man)
    claim = _claim("c1", "ABSENCE", "CATALOGUE", None, {"searched": "red shoes"}, ["search:s1"])
    findings = auditor.audit(_resp([claim], evidence=[ev]), "t")
    assert _hard(findings) == []


def test_absence_without_manifest_is_ungrounded(auditor):
    claim = _claim("c1", "ABSENCE", "CATALOGUE", None, {"searched": "x"}, [])
    findings = auditor.audit(_resp([claim]), "t")
    assert "UNGROUNDED_ABSENCE" in _kinds(findings) or "UNSUPPORTED_CLAIM" in _kinds(findings)


# ---- hard filter: NULL price under a budget cap -------------------------
def test_null_price_under_budget_cap_detected(auditor):
    view = SimpleNamespace(key="price_max", label="under ₹2,000",
                           op_to_remove=SimpleNamespace(value={"amount_minor": 200000}))
    man = _manifest("EXHAUSTIVE", 1, constraints=[view])
    ev = _search_ev("search:s1", man)
    card = SimpleNamespace(product_id="SH-NOP-001",  # NULL price product
                           title="Vintage Classic Oxford Shoes", origin="SEARCH_RESULT",
                           applied_search_id="s1", facts=[])
    findings = auditor.audit(_resp(products=[card], evidence=[ev]), "t")
    assert "NULL_PRICE_UNDER_CAP" in _kinds(findings)


def test_hard_filter_price_over_cap_detected(auditor):
    view = SimpleNamespace(key="price_max", label="under ₹2,000",
                           op_to_remove=SimpleNamespace(value={"amount_minor": 200000}))
    man = _manifest("EXHAUSTIVE", 1, constraints=[view])
    ev = _search_ev("search:s1", man)
    card = SimpleNamespace(product_id="SH-BLK-005",  # 289900 > 200000 cap
                           title="ApexLeather", origin="SEARCH_RESULT",
                           applied_search_id="s1", facts=[])
    findings = auditor.audit(_resp(products=[card], evidence=[ev]), "t")
    assert "HARD_FILTER_VIOLATION" in _kinds(findings)


# ---- unverifiable is reported, not passed -------------------------------
def test_unverifiable_reported_not_passed(auditor):
    # A text-basis ATTRIBUTE whose value is not found is UNVERIFIABLE, not a pass.
    ev = _prod_ev("prod:SH-BLK-001:attr.color", "SH-BLK-001", "attr.color", "black")
    claim = _claim("c1", "ATTRIBUTE", "PRODUCT", "SH-BLK-001",
                   {"basis": AttrBasis.DESCRIPTION_TEXT, "key": "material",
                    "value": "carbon-fibre"}, ["prod:SH-BLK-001:attr.color"])
    findings = auditor.audit(_resp([claim], evidence=[ev]), "t")
    assert any(f.kind.endswith("UNVERIFIABLE") for f in findings)


def test_structured_attribute_enum_is_verified_against_catalogue(auditor):
    pid = "SH-WARR-2Y"
    ev = _prod_ev(f"prod:{pid}:attr.warranty", pid, "attr.warranty", "2 years")
    claim = _claim(
        "c1", "ATTRIBUTE", "PRODUCT", pid,
        {"basis": AttrBasis.STRUCTURED, "key": "warranty", "value": "2 years"},
        [f"prod:{pid}:attr.warranty"],
    )

    findings = auditor.audit(_resp([claim], evidence=[ev]), "structured-enum")

    assert not any(f.kind == "ATTRIBUTE_UNVERIFIABLE" for f in findings)
    assert _hard(findings) == []


def test_missing_attribute_basis_defaults_to_structured(auditor):
    pid = "SH-WARR-2Y"
    ev = _prod_ev(f"prod:{pid}:attr.warranty", pid, "attr.warranty", "2 years")
    claim = _claim(
        "c1", "ATTRIBUTE", "PRODUCT", pid,
        {"key": "warranty", "value": "2 years"}, [f"prod:{pid}:attr.warranty"],
    )

    findings = auditor.audit(_resp([claim], evidence=[ev]), "structured-default")

    assert _hard(findings) == []
