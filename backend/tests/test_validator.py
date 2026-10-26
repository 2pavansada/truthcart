"""
Focused unit tests for validator rules V-01…V-12 (G-AC-02).

Each rule has at least one deliberately failing draft and one passing draft.
Adversarial fixtures cover fabricated prices, wrong attributes, nonexistent IDs,
invalid citations, unsupported policy claims, and missing evidence.
"""
from __future__ import annotations

import pytest

from app.container import build_container
from app.domain.enums import (
    AttrBasis,
    CardOrigin,
    ClaimScope,
    ClaimType,
    Completeness,
    Decision,
    Intent,
    RefusalReason,
)
from app.domain.plans import PlanDigest
from app.generation.template_generator import DraftResponse
from app.schemas.claims import (
    CardFact,
    Claim,
    ClarificationBlock,
    ClarificationOption,
    ProductCard,
    RefusalBlock,
    Suggestion,
)
from app.schemas.evidence import (
    CatalogueSearchManifest,
    PolicyEvidence,
    PolicySearchManifest,
    ProductFieldEvidence,
    SearchEvidence,
)
from app.schemas.chat import ConstraintOpIn, ConstraintView
from app.settings import load_settings
from app.validation.validator import Validator


@pytest.fixture(scope="module")
def container():
    return build_container(load_settings())


@pytest.fixture(scope="module")
def validator(container) -> Validator:
    return container.validator


@pytest.fixture(scope="module")
def pid(container) -> str:
    return "SH-BLK-001"


@pytest.fixture(scope="module")
def prod(container, pid):
    p = container.product_repo.get(pid)
    assert p is not None
    return p


def _digest_from_claims(claims: list[Claim]) -> PlanDigest:
    return PlanDigest(
        claim_ids=tuple(c.claim_id for c in claims),
        types=tuple(c.type for c in claims),
        values=tuple(c.value for c in claims),
        evidence_ids=tuple(tuple(c.evidence_ids) for c in claims),
    )


def _price_ev(pid: str, minor: int) -> ProductFieldEvidence:
    return ProductFieldEvidence(
        evidence_id=f"prod:{pid}:price",
        product_id=pid,
        field="price",
        value=minor,
        display_value=f"₹{minor / 100:,.2f}",
        available=True,
    )


def _title_ev(pid: str, title: str) -> ProductFieldEvidence:
    return ProductFieldEvidence(
        evidence_id=f"prod:{pid}:title",
        product_id=pid,
        field="title",
        value=title,
        display_value=title,
        available=True,
    )


def _stock_ev(pid: str, status: str) -> ProductFieldEvidence:
    return ProductFieldEvidence(
        evidence_id=f"prod:{pid}:stock",
        product_id=pid,
        field="stock",
        value=status,
        display_value="In stock" if status == "IN_STOCK" else status,
        available=status != "UNKNOWN",
    )


def _search_ev(
    search_id: str = "s_1",
    exact: int = 0,
    completeness: Completeness = Completeness.EXHAUSTIVE,
    incomplete_reasons: list[str] | None = None,
) -> SearchEvidence:
    return SearchEvidence(
        evidence_id=f"search:{search_id}",
        search=CatalogueSearchManifest(
            search_id=search_id,
            intent=Intent.SEARCH,
            query_text="test",
            exact_match_count=exact,
            eligible_count=exact,
            returned_count=min(exact, 5),
            completeness=completeness,
            incomplete_reasons=incomplete_reasons or [],
        ),
    )


def _cv(key: str, label: str, value=None) -> ConstraintView:
    """An applied hard-filter ConstraintView (op_to_remove carries the trusted value)."""
    return ConstraintView(
        key=key,
        label=label,
        op_to_remove=ConstraintOpIn(op="REMOVE", key=key, value=value),
    )


def _price_cap(amount_minor: int, inclusive: bool = False) -> ConstraintView:
    return _cv(
        "price_max",
        f"Price {'up to' if inclusive else 'under'} ₹{amount_minor / 100:,.2f}",
        {"amount_minor": amount_minor, "inclusive": inclusive},
    )


def _search_manifest(
    constraints: list[ConstraintView],
    completeness: Completeness = Completeness.EXHAUSTIVE,
    reasons: list[str] | None = None,
    exact: int = 1,
) -> SearchEvidence:
    return SearchEvidence(
        evidence_id="search:s_1",
        search=CatalogueSearchManifest(
            search_id="s_1",
            intent=Intent.SEARCH,
            query_text="shoes under budget",
            applied_constraints=list(constraints),
            exact_match_count=exact,
            eligible_count=exact,
            returned_count=min(exact, 5),
            completeness=completeness,
            incomplete_reasons=reasons or [],
        ),
    )


def _draft(
    *,
    decision: Decision = Decision.ANSWER,
    answer_text: str = "",
    claims: list[Claim] | None = None,
    evidence: list | None = None,
    products: list[ProductCard] | None = None,
    clarification: ClarificationBlock | None = None,
    refusal: RefusalBlock | None = None,
    suggestions: list[Suggestion] | None = None,
) -> DraftResponse:
    return DraftResponse(
        decision=decision,
        answer_text=answer_text,
        claims=claims or [],
        evidence=evidence or [],
        products=products or [],
        clarification=clarification,
        refusal=refusal,
        suggestions=suggestions or [],
    )


NF_TEXT = "Hello! Ask me about products in the catalogue or about returns, shipping and warranty."


def _db_card(validator, product_id: str, origin: CardOrigin, search_id: str | None = "s_1"):
    """Build a card whose facts mirror the DB (so DB-consistency checks pass) plus evidence.

    Returns (ProductCard, list[Evidence]). Any V-10 violation therefore comes from
    the *independent hard-filter re-check*, not from a fabricated card value.
    """
    prod = validator.product_repo.get(product_id)
    assert prod is not None, f"fixture product {product_id} must exist in DB"
    facts = []
    evidence = [_title_ev(product_id, prod.title)]
    if prod.price_minor is not None:
        facts.append(CardFact(
            field="price", label="Price", value=prod.price_minor,
            display=prod.price_display or "", available=True,
            evidence_id=f"prod:{product_id}:price",
        ))
        evidence.append(_price_ev(product_id, prod.price_minor))
    facts.append(CardFact(
        field="stock", label="Stock", value=prod.stock_status.value,
        display=prod.stock_label, available=True,
        evidence_id=f"prod:{product_id}:stock",
    ))
    evidence.append(_stock_ev(product_id, prod.stock_status.value))
    card = ProductCard(
        product_id=product_id, title=prod.title,
        title_evidence_id=f"prod:{product_id}:title",
        origin=origin, applied_search_id=search_id, facts=facts,
    )
    return card, evidence


# ---------------------------------------------------------------------------
# V-01 Evidence present
# ---------------------------------------------------------------------------

class TestV01:
    def test_fail_missing_evidence_ids(self, validator, pid, prod):
        claim = Claim(
            claim_id="c1",
            type=ClaimType.PRICE,
            scope=ClaimScope.PRODUCT,
            product_id=pid,
            value={"price_minor": prod.price_minor, "currency": "INR"},
            sentence=f"The price is ₹{prod.price_minor / 100:.2f}.",
            evidence_ids=[],
        )
        draft = _draft(answer_text=claim.sentence, claims=[claim], evidence=[])
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert any(v.rule == "V-01" for v in report.violations)

    def test_pass_with_evidence(self, validator, pid, prod):
        claim = Claim(
            claim_id="c1",
            type=ClaimType.PRICE,
            scope=ClaimScope.PRODUCT,
            product_id=pid,
            value={"price_minor": prod.price_minor, "currency": "INR"},
            sentence=f"The price of “{prod.title}” ({pid}) is ₹{prod.price_minor / 100:,.2f}.",
            evidence_ids=[f"prod:{pid}:price", f"prod:{pid}:title"],
        )
        ev = [_price_ev(pid, prod.price_minor), _title_ev(pid, prod.title)]
        draft = _draft(
            answer_text=claim.sentence + f" [{pid}]",
            claims=[claim],
            evidence=ev,
        )
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert not any(v.rule == "V-01" for v in report.violations)


# ---------------------------------------------------------------------------
# V-02 Evidence real and compatible
# ---------------------------------------------------------------------------

class TestV02:
    def test_fail_invalid_citation_grammar(self, validator, pid, prod):
        claim = Claim(
            claim_id="c1",
            type=ClaimType.PRICE,
            scope=ClaimScope.PRODUCT,
            product_id=pid,
            value={"price_minor": prod.price_minor, "currency": "INR"},
            sentence=f"The price of “{prod.title}” ({pid}) is ₹{prod.price_minor / 100:,.2f}.",
            evidence_ids=["not-a-valid-id"],
        )
        draft = _draft(answer_text=claim.sentence, claims=[claim], evidence=[])
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert any(v.rule == "V-02" and "grammar" in v.detail for v in report.violations)

    def test_fail_nonexistent_evidence_id(self, validator, pid, prod):
        claim = Claim(
            claim_id="c1",
            type=ClaimType.PRICE,
            scope=ClaimScope.PRODUCT,
            product_id=pid,
            value={"price_minor": prod.price_minor, "currency": "INR"},
            sentence=f"The price of “{prod.title}” ({pid}) is ₹{prod.price_minor / 100:,.2f}.",
            evidence_ids=[f"prod:{pid}:price"],
        )
        draft = _draft(answer_text=claim.sentence, claims=[claim], evidence=[])
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert any(v.rule == "V-02" for v in report.violations)

    def test_fail_wrong_product_on_evidence(self, validator, pid, prod, container):
        other = "AC-BAG-001"
        claim = Claim(
            claim_id="c1",
            type=ClaimType.PRICE,
            scope=ClaimScope.PRODUCT,
            product_id=pid,
            value={"price_minor": prod.price_minor, "currency": "INR"},
            sentence=f"The price of “{prod.title}” ({pid}) is ₹{prod.price_minor / 100:,.2f}.",
            evidence_ids=[f"prod:{other}:price", f"prod:{pid}:title"],
        )
        other_p = container.product_repo.get(other)
        ev = [
            _price_ev(other, other_p.price_minor),
            _title_ev(pid, prod.title),
        ]
        draft = _draft(answer_text=claim.sentence, claims=[claim], evidence=ev)
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert any(v.rule == "V-02" for v in report.violations)

    def test_pass_compatible_kinds(self, validator, pid, prod):
        claim = Claim(
            claim_id="c1",
            type=ClaimType.PRICE,
            scope=ClaimScope.PRODUCT,
            product_id=pid,
            value={"price_minor": prod.price_minor, "currency": "INR"},
            sentence=f"The price of “{prod.title}” ({pid}) is ₹{prod.price_minor / 100:,.2f}.",
            evidence_ids=[f"prod:{pid}:price", f"prod:{pid}:title"],
        )
        ev = [_price_ev(pid, prod.price_minor), _title_ev(pid, prod.title)]
        draft = _draft(
            answer_text=claim.sentence + f" [{pid}]",
            claims=[claim],
            evidence=ev,
        )
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert not any(v.rule == "V-02" for v in report.violations)


# ---------------------------------------------------------------------------
# V-03 Typed value equals source
# ---------------------------------------------------------------------------

class TestV03:
    def test_fail_fabricated_price(self, validator, pid, prod):
        fake = (prod.price_minor or 0) + 99900
        claim = Claim(
            claim_id="c1",
            type=ClaimType.PRICE,
            scope=ClaimScope.PRODUCT,
            product_id=pid,
            value={"price_minor": fake, "currency": "INR"},
            sentence=f"The price of “{prod.title}” ({pid}) is ₹{fake / 100:,.2f}.",
            evidence_ids=[f"prod:{pid}:price", f"prod:{pid}:title"],
        )
        # Evidence shows the real price — claim value is fabricated
        ev = [_price_ev(pid, prod.price_minor), _title_ev(pid, prod.title)]
        draft = _draft(answer_text=claim.sentence + f" [{pid}]", claims=[claim], evidence=ev)
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert any(v.rule == "V-03" for v in report.violations)

    def test_fail_nonexistent_product_id(self, validator):
        claim = Claim(
            claim_id="c1",
            type=ClaimType.PRICE,
            scope=ClaimScope.PRODUCT,
            product_id="NO-SUCH-PRODUCT",
            value={"price_minor": 100, "currency": "INR"},
            sentence="The price of “X” (NO-SUCH-PRODUCT) is ₹1.00.",
            evidence_ids=["prod:NO-SUCH-PRODUCT:price"],
        )
        ev = [_price_ev("NO-SUCH-PRODUCT", 100)]
        draft = _draft(answer_text=claim.sentence, claims=[claim], evidence=ev)
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert any(v.rule == "V-03" and "not in DB" in v.detail for v in report.violations)

    def test_fail_absence_when_search_incomplete(self, validator):
        claim = Claim(
            claim_id="c1",
            type=ClaimType.ABSENCE,
            scope=ClaimScope.CATALOGUE,
            product_id=None,
            value={"searched": "widgets"},
            sentence='No product matching “widgets” is listed in the supplied catalogue.',
            evidence_ids=["search:s_1"],
        )
        ev = [_search_ev(exact=0, completeness=Completeness.INCOMPLETE, incomplete_reasons=["SEARCH_BUDGET_EXCEEDED"])]
        draft = _draft(
            decision=Decision.REFUSE,
            answer_text=claim.sentence + " [search:s_1]",
            claims=[claim],
            evidence=ev,
            refusal=RefusalBlock(reason=RefusalReason.NO_CATALOGUE_MATCH),
        )
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert any(v.rule == "V-03" and "EXHAUSTIVE" in v.detail for v in report.violations)

    def test_fail_wrong_attribute(self, validator, container):
        pid = "AC-BAG-001"
        prod = container.product_repo.get(pid)
        claim = Claim(
            claim_id="c1",
            type=ClaimType.ATTRIBUTE,
            scope=ClaimScope.PRODUCT,
            product_id=pid,
            value={"key": "color", "value": "neon-pink", "basis": AttrBasis.STRUCTURED.value},
            sentence=f"The catalogue lists color as neon-pink for “{prod.title}” ({pid}).",
            evidence_ids=[f"prod:{pid}:attr.color", f"prod:{pid}:title"],
        )
        ev = [
            ProductFieldEvidence(
                evidence_id=f"prod:{pid}:attr.color",
                product_id=pid,
                field="attr.color",
                value="grey",
                display_value="grey",
                available=True,
            ),
            _title_ev(pid, prod.title),
        ]
        draft = _draft(answer_text=claim.sentence + f" [{pid}]", claims=[claim], evidence=ev)
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert any(v.rule == "V-03" for v in report.violations)

    def test_pass_real_price(self, validator, pid, prod):
        claim = Claim(
            claim_id="c1",
            type=ClaimType.PRICE,
            scope=ClaimScope.PRODUCT,
            product_id=pid,
            value={"price_minor": prod.price_minor, "currency": "INR"},
            sentence=f"The price of “{prod.title}” ({pid}) is ₹{prod.price_minor / 100:,.2f}.",
            evidence_ids=[f"prod:{pid}:price", f"prod:{pid}:title"],
        )
        ev = [_price_ev(pid, prod.price_minor), _title_ev(pid, prod.title)]
        draft = _draft(answer_text=claim.sentence + f" [{pid}]", claims=[claim], evidence=ev)
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert report.ok, report.violations


# ---------------------------------------------------------------------------
# V-04 No stray numbers
# ---------------------------------------------------------------------------

class TestV04:
    def test_fail_fabricated_currency_in_sentence(self, validator, pid, prod):
        claim = Claim(
            claim_id="c1",
            type=ClaimType.STOCK,
            scope=ClaimScope.PRODUCT,
            product_id=pid,
            value={"stock_status": prod.stock_status.value},
            sentence=f"“{prod.title}” ({pid}) is in stock and costs ₹9,999.00.",
            evidence_ids=[f"prod:{pid}:stock", f"prod:{pid}:title"],
        )
        ev = [_stock_ev(pid, prod.stock_status.value), _title_ev(pid, prod.title)]
        draft = _draft(answer_text=claim.sentence + f" [{pid}]", claims=[claim], evidence=ev)
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert any(v.rule == "V-04" for v in report.violations)

    def test_pass_price_from_evidence(self, validator, pid, prod):
        claim = Claim(
            claim_id="c1",
            type=ClaimType.PRICE,
            scope=ClaimScope.PRODUCT,
            product_id=pid,
            value={"price_minor": prod.price_minor, "currency": "INR"},
            sentence=f"The price of “{prod.title}” ({pid}) is ₹{prod.price_minor / 100:,.2f}.",
            evidence_ids=[f"prod:{pid}:price", f"prod:{pid}:title"],
        )
        ev = [_price_ev(pid, prod.price_minor), _title_ev(pid, prod.title)]
        draft = _draft(answer_text=claim.sentence + f" [{pid}]", claims=[claim], evidence=ev)
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert not any(v.rule == "V-04" for v in report.violations)


# ---------------------------------------------------------------------------
# V-05 Subject binding
# ---------------------------------------------------------------------------

class TestV05:
    def test_fail_foreign_product_id_in_sentence(self, validator, pid, prod, container):
        other = "AC-BAG-001"
        claim = Claim(
            claim_id="c1",
            type=ClaimType.PRICE,
            scope=ClaimScope.PRODUCT,
            product_id=pid,
            value={"price_minor": prod.price_minor, "currency": "INR"},
            sentence=(
                f"The price of “{prod.title}” ({pid}) is ₹{prod.price_minor / 100:,.2f}, "
                f"unlike {other}."
            ),
            evidence_ids=[f"prod:{pid}:price", f"prod:{pid}:title"],
        )
        ev = [_price_ev(pid, prod.price_minor), _title_ev(pid, prod.title)]
        draft = _draft(answer_text=claim.sentence + f" [{pid}]", claims=[claim], evidence=ev)
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert any(v.rule == "V-05" for v in report.violations)

    def test_pass_bound_to_own_product(self, validator, pid, prod):
        claim = Claim(
            claim_id="c1",
            type=ClaimType.PRICE,
            scope=ClaimScope.PRODUCT,
            product_id=pid,
            value={"price_minor": prod.price_minor, "currency": "INR"},
            sentence=f"The price of “{prod.title}” ({pid}) is ₹{prod.price_minor / 100:,.2f}.",
            evidence_ids=[f"prod:{pid}:price", f"prod:{pid}:title"],
        )
        ev = [_price_ev(pid, prod.price_minor), _title_ev(pid, prod.title)]
        draft = _draft(answer_text=claim.sentence + f" [{pid}]", claims=[claim], evidence=ev)
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert not any(v.rule == "V-05" for v in report.violations)


# ---------------------------------------------------------------------------
# V-06 No free sentences
# ---------------------------------------------------------------------------

class TestV06:
    def test_fail_free_sentence(self, validator, pid, prod):
        claim = Claim(
            claim_id="c1",
            type=ClaimType.PRICE,
            scope=ClaimScope.PRODUCT,
            product_id=pid,
            value={"price_minor": prod.price_minor, "currency": "INR"},
            sentence=f"The price of “{prod.title}” ({pid}) is ₹{prod.price_minor / 100:,.2f}.",
            evidence_ids=[f"prod:{pid}:price", f"prod:{pid}:title"],
        )
        ev = [_price_ev(pid, prod.price_minor), _title_ev(pid, prod.title)]
        draft = _draft(
            answer_text=claim.sentence + f" [{pid}] Also, this is a great deal!",
            claims=[claim],
            evidence=ev,
        )
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert any(v.rule == "V-06" for v in report.violations)

    def test_pass_claim_plus_nf(self, validator):
        text = "Hello! Ask me about products in the catalogue or about returns, shipping and warranty."
        draft = _draft(answer_text=text, claims=[])
        report = validator.validate(draft, _digest_from_claims([]))
        assert not any(v.rule == "V-06" for v in report.violations)


# ---------------------------------------------------------------------------
# V-07 Decision consistency
# ---------------------------------------------------------------------------

class TestV07:
    def test_fail_answer_without_claims_or_nf(self, validator):
        draft = _draft(decision=Decision.ANSWER, answer_text="Sure, anything you like.")
        report = validator.validate(draft, _digest_from_claims([]))
        assert any(v.rule == "V-07" for v in report.violations)

    def test_fail_refuse_no_catalogue_match_without_absence(self, validator):
        draft = _draft(
            decision=Decision.REFUSE,
            answer_text="I can only help with products in this catalogue and its returns, shipping and warranty policy.",
            refusal=RefusalBlock(reason=RefusalReason.NO_CATALOGUE_MATCH),
        )
        report = validator.validate(draft, _digest_from_claims([]))
        assert any(v.rule == "V-07" for v in report.violations)

    def test_pass_greeting(self, validator):
        text = "Hello! Ask me about products in the catalogue or about returns, shipping and warranty."
        draft = _draft(decision=Decision.ANSWER, answer_text=text)
        report = validator.validate(draft, _digest_from_claims([]))
        assert not any(v.rule == "V-07" for v in report.violations)


# ---------------------------------------------------------------------------
# V-08 Policy claims
# ---------------------------------------------------------------------------

class TestV08:
    def test_fail_fabricated_policy_quote(self, validator, container):
        sec = container.policy_repo.get_section("1.1")
        assert sec is not None
        claim = Claim(
            claim_id="c1",
            type=ClaimType.POLICY_QUOTE,
            scope=ClaimScope.GENERAL_POLICY,
            product_id=None,
            value={"quote": "We refund everything instantly with no questions.", "section_id": "1.1"},
            sentence='The policy (Return Window) states: “We refund everything instantly with no questions.”',
            evidence_ids=["pol:1.1"],
        )
        ev = [PolicyEvidence(
            evidence_id="pol:1.1",
            section_id="1.1",
            heading=sec.heading,
            excerpt=sec.body,
        )]
        draft = _draft(answer_text=claim.sentence + " [policy:1.1]", claims=[claim], evidence=ev)
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert any(v.rule == "V-08" for v in report.violations)

    def test_pass_verbatim_quote(self, validator, container):
        sec = container.policy_repo.get_section("1.1")
        quote = "Customers may return eligible items within 30 days of delivery."
        claim = Claim(
            claim_id="c1",
            type=ClaimType.POLICY_QUOTE,
            scope=ClaimScope.GENERAL_POLICY,
            product_id=None,
            value={"quote": quote, "section_id": "1.1"},
            sentence=f'The policy ({sec.heading}) states: “{quote}”',
            evidence_ids=["pol:1.1"],
        )
        ev = [PolicyEvidence(
            evidence_id="pol:1.1",
            section_id="1.1",
            heading=sec.heading,
            excerpt=sec.body,
        )]
        draft = _draft(answer_text=claim.sentence + " [policy:1.1]", claims=[claim], evidence=ev)
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert not any(v.rule == "V-08" for v in report.violations)


# ---------------------------------------------------------------------------
# V-09 Warranty scope
# ---------------------------------------------------------------------------

class TestV09:
    def test_fail_product_warranty_without_product_evidence(self, validator, pid, prod):
        claim = Claim(
            claim_id="c1",
            type=ClaimType.PRODUCT_SUMMARY,
            scope=ClaimScope.PRODUCT,
            product_id=pid,
            value={
                "price_minor": prod.price_minor,
                "currency": "INR",
                "stock_status": prod.stock_status.value,
                "rating": prod.rating,
            },
            sentence=f"“{prod.title}” ({pid}) has a two-year warranty.",
            evidence_ids=[f"prod:{pid}:title", f"prod:{pid}:price", f"prod:{pid}:stock"],
        )
        ev = [
            _title_ev(pid, prod.title),
            _price_ev(pid, prod.price_minor),
            _stock_ev(pid, prod.stock_status.value),
        ]
        draft = _draft(answer_text=claim.sentence + f" [{pid}]", claims=[claim], evidence=ev)
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert any(v.rule == "V-09" for v in report.violations)

    def test_fail_policy_warranty_mentions_product_id(self, validator, container, pid):
        sec = container.policy_repo.get_section("1.1")
        quote = "Customers may return eligible items within 30 days of delivery."
        claim = Claim(
            claim_id="c1",
            type=ClaimType.POLICY_QUOTE,
            scope=ClaimScope.GENERAL_POLICY,
            product_id=None,
            value={"quote": quote, "section_id": "1.1"},
            sentence=f'The policy states warranty for {pid}: “{quote}”',
            evidence_ids=["pol:1.1"],
        )
        ev = [PolicyEvidence(
            evidence_id="pol:1.1",
            section_id="1.1",
            heading=sec.heading,
            excerpt=sec.body,
        )]
        draft = _draft(answer_text=claim.sentence + " [policy:1.1]", claims=[claim], evidence=ev)
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert any(v.rule == "V-09" for v in report.violations)

    def test_pass_attribute_warranty(self, validator, container):
        pid = "AC-BAG-001"
        prod = container.product_repo.get(pid)
        claim = Claim(
            claim_id="c1",
            type=ClaimType.ATTRIBUTE,
            scope=ClaimScope.PRODUCT,
            product_id=pid,
            value={"key": "warranty", "value": "1 year", "basis": AttrBasis.STRUCTURED.value},
            sentence=f'The catalogue lists warranty as 1 year for “{prod.title}” ({pid}).',
            evidence_ids=[f"prod:{pid}:attr.warranty", f"prod:{pid}:title"],
        )
        ev = [
            ProductFieldEvidence(
                evidence_id=f"prod:{pid}:attr.warranty",
                product_id=pid,
                field="attr.warranty",
                value="1 year",
                display_value="1 year",
                available=True,
            ),
            _title_ev(pid, prod.title),
        ]
        draft = _draft(answer_text=claim.sentence + f" [{pid}]", claims=[claim], evidence=ev)
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert not any(v.rule == "V-09" for v in report.violations)


# ---------------------------------------------------------------------------
# V-10 Cards re-verified
# ---------------------------------------------------------------------------

class TestV10:
    def test_fail_card_wrong_price(self, validator, pid, prod):
        card = ProductCard(
            product_id=pid,
            title=prod.title,
            title_evidence_id=f"prod:{pid}:title",
            origin=CardOrigin.LOOKUP,
            applied_search_id=None,
            facts=[
                CardFact(
                    field="price",
                    label="Price",
                    value=(prod.price_minor or 0) + 50000,
                    display="₹fake",
                    available=True,
                    evidence_id=f"prod:{pid}:price",
                ),
            ],
        )
        draft = _draft(
            answer_text="Hello! Ask me about products in the catalogue or about returns, shipping and warranty.",
            products=[card],
            evidence=[_title_ev(pid, prod.title), _price_ev(pid, prod.price_minor)],
        )
        report = validator.validate(draft, _digest_from_claims([]))
        assert any(v.rule == "V-10" for v in report.violations)

    def test_fail_search_card_violates_price_filter(self, validator):
        """SEARCH-origin card whose DB price exceeds the applied price_max must fail V-10."""
        expensive_id = "SH-BLK-005"
        expensive = validator.product_repo.get(expensive_id)
        assert expensive is not None and expensive.price_minor is not None
        cap = expensive.price_minor - 1
        search = SearchEvidence(
            evidence_id="search:s_1",
            search=CatalogueSearchManifest(
                search_id="s_1",
                intent=Intent.SEARCH,
                query_text="shoes",
                exact_match_count=1,
                applied_constraints=[
                    ConstraintView(
                        key="price_max",
                        label=f"Price under ₹{cap / 100:,.2f}",
                        op_to_remove=ConstraintOpIn(op="REMOVE", key="price_max"),
                    )
                ],
            ),
        )
        card = ProductCard(
            product_id=expensive_id,
            title=expensive.title,
            title_evidence_id=f"prod:{expensive_id}:title",
            origin=CardOrigin.SEARCH_RESULT,
            applied_search_id="s_1",
            facts=[
                CardFact(
                    field="price",
                    label="Price",
                    value=expensive.price_minor,
                    display=expensive.price_display or "",
                    available=True,
                    evidence_id=f"prod:{expensive_id}:price",
                ),
                CardFact(
                    field="stock",
                    label="Stock",
                    value=expensive.stock_status.value,
                    display=expensive.stock_label,
                    available=True,
                    evidence_id=f"prod:{expensive_id}:stock",
                ),
            ],
        )
        draft = _draft(
            answer_text="Hello! Ask me about products in the catalogue or about returns, shipping and warranty.",
            products=[card],
            evidence=[
                search,
                _title_ev(expensive_id, expensive.title),
                _price_ev(expensive_id, expensive.price_minor),
                _stock_ev(expensive_id, expensive.stock_status.value),
            ],
        )
        report = validator.validate(draft, _digest_from_claims([]))
        assert any(v.rule == "V-10" for v in report.violations)

    def test_pass_card_matches_db(self, validator, pid, prod):
        card = ProductCard(
            product_id=pid,
            title=prod.title,
            title_evidence_id=f"prod:{pid}:title",
            origin=CardOrigin.LOOKUP,
            applied_search_id=None,
            facts=[
                CardFact(
                    field="price",
                    label="Price",
                    value=prod.price_minor,
                    display=prod.price_display or "",
                    available=True,
                    evidence_id=f"prod:{pid}:price",
                ),
                CardFact(
                    field="stock",
                    label="Stock",
                    value=prod.stock_status.value,
                    display=prod.stock_label,
                    available=True,
                    evidence_id=f"prod:{pid}:stock",
                ),
            ],
        )
        text = "Hello! Ask me about products in the catalogue or about returns, shipping and warranty."
        draft = _draft(
            answer_text=text,
            products=[card],
            evidence=[
                _title_ev(pid, prod.title),
                _price_ev(pid, prod.price_minor),
                _stock_ev(pid, prod.stock_status.value),
            ],
        )
        report = validator.validate(draft, _digest_from_claims([]))
        assert not any(v.rule == "V-10" for v in report.violations)

    # -- V-10 independent hard-filter re-evaluation -----------------------
    def _run_search_card(self, validator, product_id, constraints, origin,
                         completeness=Completeness.EXHAUSTIVE, reasons=None):
        card, ev = _db_card(validator, product_id, origin)
        manifest = _search_manifest(constraints, completeness=completeness, reasons=reasons)
        draft = _draft(
            answer_text=NF_TEXT,
            products=[card],
            evidence=[manifest, *ev],
        )
        return validator.validate(draft, _digest_from_claims([]))

    def test_pass_valid_search_card_satisfies_all_applied_filters(self, validator):
        """A shoe within budget, correct category, in stock -> no V-10 violation."""
        report = self._run_search_card(
            validator, "SH-BLK-001",
            [_cv("category", "Category: footwear", "footwear"), _price_cap(200000), _cv("stock", "In stock")],
            CardOrigin.SEARCH_RESULT,
        )
        assert not any(v.rule == "V-10" for v in report.violations)

    def test_fail_search_card_exceeds_max_price(self, validator):
        """Card is truthful vs DB, but DB price exceeds the applied budget cap."""
        report = self._run_search_card(
            validator, "SH-BLK-005", [_price_cap(200000)], CardOrigin.SEARCH_RESULT,
        )
        assert any(v.rule == "V-10" and "hard filters" in v.detail for v in report.violations)

    def test_fail_search_card_category_mismatch(self, validator):
        """Electronics card under an applied 'footwear' category hard filter."""
        report = self._run_search_card(
            validator, "EL-SPK-001", [_cv("category", "Category: footwear", "footwear")],
            CardOrigin.SEARCH_RESULT,
        )
        assert any(v.rule == "V-10" and "hard filters" in v.detail for v in report.violations)

    def test_fail_search_card_out_of_stock_when_in_stock_required(self, validator):
        """Out-of-stock product returned while 'in stock' availability is required."""
        report = self._run_search_card(
            validator, "SH-BLK-004", [_cv("stock", "In stock")], CardOrigin.SEARCH_RESULT,
        )
        assert any(v.rule == "V-10" and "hard filters" in v.detail for v in report.violations)

    def test_fail_search_card_unknown_price_under_budget(self, validator):
        """A null DB price must never be treated as within budget."""
        prod = validator.product_repo.get("SH-NOP-001")
        assert prod.price_minor is None
        report = self._run_search_card(
            validator, "SH-NOP-001", [_price_cap(200000)], CardOrigin.SEARCH_RESULT,
        )
        assert any(v.rule == "V-10" and "hard filters" in v.detail for v in report.violations)

    def test_fail_search_card_missing_product_record(self, validator):
        """A search card referencing a nonexistent product is unsafe."""
        card = ProductCard(
            product_id="GHOST-999", title="Nonexistent Product",
            title_evidence_id="prod:GHOST-999:title",
            origin=CardOrigin.SEARCH_RESULT, applied_search_id="s_1", facts=[],
        )
        draft = _draft(
            answer_text=NF_TEXT, products=[card],
            evidence=[_search_manifest([_price_cap(200000)])],
        )
        report = validator.validate(draft, _digest_from_claims([]))
        assert any(v.rule == "V-10" and "not in DB" in v.detail for v in report.violations)

    @pytest.mark.parametrize("origin", [CardOrigin.SEARCH_RESULT, CardOrigin.EXISTENCE_MATCH])
    def test_fail_search_origin_card_without_manifest(self, validator, origin):
        """Search cards cannot bypass V-10 when their constraint provenance is absent."""
        card, card_evidence = _db_card(validator, "SH-BLK-001", origin)
        draft = _draft(answer_text=NF_TEXT, products=[card], evidence=card_evidence)

        report = validator.validate(draft, _digest_from_claims([]))

        assert any(
            v.rule == "V-10" and "no unique catalogue manifest" in v.detail
            for v in report.violations
        )

    def test_fail_search_origin_card_with_mismatched_manifest_id(self, validator):
        """A card must use the manifest named by its own applied_search_id."""
        card, card_evidence = _db_card(
            validator, "SH-BLK-001", CardOrigin.SEARCH_RESULT, search_id="s_2"
        )
        draft = _draft(
            answer_text=NF_TEXT,
            products=[card],
            evidence=[_search_manifest([]), *card_evidence],
        )

        report = validator.validate(draft, _digest_from_claims([]))

        assert any(
            v.rule == "V-10" and "applied_search_id 's_2'" in v.detail
            for v in report.violations
        )

    def test_fail_existence_match_card_violates_filter(self, validator):
        """EXISTENCE_MATCH origin is subject to the same independent re-check."""
        report = self._run_search_card(
            validator, "SH-BLK-004", [_cv("stock", "In stock")], CardOrigin.EXISTENCE_MATCH,
        )
        assert any(v.rule == "V-10" and "hard filters" in v.detail for v in report.violations)

    def test_lookup_card_not_subject_to_search_filter_recheck(self, validator):
        """LOOKUP cards are DB-checked but exempt from the search hard-filter re-check."""
        report = self._run_search_card(
            validator, "SH-BLK-004", [_cv("stock", "In stock")], CardOrigin.LOOKUP,
        )
        assert not any(v.rule == "V-10" for v in report.violations)

    def test_incomplete_search_still_flags_violating_card(self, validator):
        """Filter re-evaluation is independent of search completeness."""
        report = self._run_search_card(
            validator, "SH-BLK-005", [_price_cap(200000)], CardOrigin.SEARCH_RESULT,
            completeness=Completeness.INCOMPLETE, reasons=["SEARCH_BUDGET_EXCEEDED"],
        )
        assert any(v.rule == "V-10" and "hard filters" in v.detail for v in report.violations)

    def test_incomplete_search_does_not_yield_definitive_absence(self, validator):
        """An ABSENCE claim over an INCOMPLETE search must be rejected (D-07 safety)."""
        claim = Claim(
            claim_id="c1", type=ClaimType.ABSENCE, scope=ClaimScope.CATALOGUE,
            product_id=None, value={"searched": "black shoes under budget"},
            sentence='No product matching “black shoes under budget” is listed in the supplied catalogue.',
            evidence_ids=["search:s_1"],
        )
        manifest = _search_manifest([], completeness=Completeness.INCOMPLETE,
                                    reasons=["LEXICAL_BACKEND_ERROR"], exact=0)
        draft = _draft(
            decision=Decision.REFUSE, answer_text=claim.sentence + " [search:s_1]",
            claims=[claim], evidence=[manifest],
            refusal=RefusalBlock(reason=RefusalReason.NO_CATALOGUE_MATCH),
        )
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert not report.ok
        assert any(v.rule == "V-03" and "EXHAUSTIVE" in v.detail for v in report.violations)


# ---------------------------------------------------------------------------
# V-11 Keyword guard
# ---------------------------------------------------------------------------

class TestV11:
    def test_fail_stock_words_without_stock_evidence(self, validator, pid, prod):
        claim = Claim(
            claim_id="c1",
            type=ClaimType.PRICE,
            scope=ClaimScope.PRODUCT,
            product_id=pid,
            value={"price_minor": prod.price_minor, "currency": "INR"},
            sentence=f"The price of “{prod.title}” ({pid}) is ₹{prod.price_minor / 100:,.2f} and it is in stock.",
            evidence_ids=[f"prod:{pid}:price", f"prod:{pid}:title"],
        )
        ev = [_price_ev(pid, prod.price_minor), _title_ev(pid, prod.title)]
        draft = _draft(answer_text=claim.sentence + f" [{pid}]", claims=[claim], evidence=ev)
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert any(v.rule == "V-11" for v in report.violations)

    def test_pass_stock_with_evidence(self, validator, pid, prod):
        claim = Claim(
            claim_id="c1",
            type=ClaimType.STOCK,
            scope=ClaimScope.PRODUCT,
            product_id=pid,
            value={"stock_status": "IN_STOCK"},
            sentence=f"“{prod.title}” ({pid}) is in stock.",
            evidence_ids=[f"prod:{pid}:stock", f"prod:{pid}:title"],
        )
        ev = [_stock_ev(pid, "IN_STOCK"), _title_ev(pid, prod.title)]
        draft = _draft(answer_text=claim.sentence + f" [{pid}]", claims=[claim], evidence=ev)
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert not any(v.rule == "V-11" for v in report.violations)

    # -- FIELD_UNAVAILABLE: closed-set missing-field template vs the keyword guard --
    def _fu_draft(self, validator, pid, field, label=None):
        prod = validator.product_repo.get(pid)
        assert prod is not None
        ev_field = "attr.warranty" if field == "warranty" else field
        claim = Claim(
            claim_id="c1",
            type=ClaimType.FIELD_UNAVAILABLE,
            scope=ClaimScope.PRODUCT,
            product_id=pid,
            value={"field": field},
            sentence=f'The {label or field} of “{prod.title}” ({pid}) is not available in the catalogue.',
            evidence_ids=[f"prod:{pid}:{ev_field}", f"prod:{pid}:title"],
        )
        ev = [
            ProductFieldEvidence(
                evidence_id=f"prod:{pid}:{ev_field}", product_id=pid, field=ev_field,
                value=None, display_value=None, available=False,
            ),
            _title_ev(pid, prod.title),
        ]
        draft = _draft(
            decision=Decision.REFUSE,
            answer_text=claim.sentence + f" [{pid}]",
            claims=[claim], evidence=ev,
            refusal=RefusalBlock(reason=RefusalReason.FIELD_MISSING),
        )
        return claim, draft

    def test_pass_field_unavailable_null_price(self, validator):
        """Positive: the sanctioned '… is not available …' wording must NOT trip V-11."""
        claim, draft = self._fu_draft(validator, "SH-NOP-001", "price")
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert not any(v.rule == "V-11" for v in report.violations)

    def test_pass_field_unavailable_warranty(self, validator):
        """Boundary: a 'warranty' field label is structural, not a policy assertion."""
        claim, draft = self._fu_draft(validator, "CL-JNS-001", "warranty")
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert not any(v.rule == "V-11" for v in report.violations)

    def test_pass_field_unavailable_rating_and_stock(self, validator):
        """Boundary: 'rating'/'stock' labels neither trip rating nor stock keyword guards."""
        for field in ("rating", "stock"):
            claim, draft = self._fu_draft(validator, "SH-BLK-001", field)
            report = validator.validate(draft, _digest_from_claims([claim]))
            assert not any(v.rule == "V-11" for v in report.violations), field

    def test_fail_policy_word_on_non_exempt_claim_still_caught(self, validator, pid, prod):
        """Negative: the exemption is NOT global -- a real PRICE claim asserting a
        warranty (a policy word) without policy/attribute evidence is still rejected."""
        claim = Claim(
            claim_id="c1",
            type=ClaimType.PRICE,
            scope=ClaimScope.PRODUCT,
            product_id=pid,
            value={"price_minor": prod.price_minor, "currency": "INR"},
            sentence=f'The price of “{prod.title}” ({pid}) is ₹{prod.price_minor / 100:,.2f} and it has a warranty.',
            evidence_ids=[f"prod:{pid}:price", f"prod:{pid}:title"],
        )
        ev = [_price_ev(pid, prod.price_minor), _title_ev(pid, prod.title)]
        draft = _draft(answer_text=claim.sentence + f" [{pid}]", claims=[claim], evidence=ev)
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert any(v.rule == "V-11" and "Policy words" in v.detail for v in report.violations)


# ---------------------------------------------------------------------------
# V-12 Template-mode claim_id integrity
# ---------------------------------------------------------------------------

class TestV12:
    def test_fail_claim_id_mismatch_with_digest(self, validator, pid, prod):
        claim = Claim(
            claim_id="c1",
            type=ClaimType.PRICE,
            scope=ClaimScope.PRODUCT,
            product_id=pid,
            value={"price_minor": prod.price_minor, "currency": "INR"},
            sentence=f"The price of “{prod.title}” ({pid}) is ₹{prod.price_minor / 100:,.2f}.",
            evidence_ids=[f"prod:{pid}:price", f"prod:{pid}:title"],
        )
        ev = [_price_ev(pid, prod.price_minor), _title_ev(pid, prod.title)]
        draft = _draft(answer_text=claim.sentence + f" [{pid}]", claims=[claim], evidence=ev)
        wrong_digest = PlanDigest(
            claim_ids=("c99",),
            types=(ClaimType.PRICE,),
            values=(claim.value,),
            evidence_ids=(tuple(claim.evidence_ids),),
        )
        report = validator.validate(draft, wrong_digest)
        assert any(v.rule == "V-12" for v in report.violations)

    def test_pass_matching_digest(self, validator, pid, prod):
        claim = Claim(
            claim_id="c1",
            type=ClaimType.PRICE,
            scope=ClaimScope.PRODUCT,
            product_id=pid,
            value={"price_minor": prod.price_minor, "currency": "INR"},
            sentence=f"The price of “{prod.title}” ({pid}) is ₹{prod.price_minor / 100:,.2f}.",
            evidence_ids=[f"prod:{pid}:price", f"prod:{pid}:title"],
        )
        ev = [_price_ev(pid, prod.price_minor), _title_ev(pid, prod.title)]
        draft = _draft(answer_text=claim.sentence + f" [{pid}]", claims=[claim], evidence=ev)
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert not any(v.rule == "V-12" for v in report.violations)


# ---------------------------------------------------------------------------
# Safe failure: validator returns report, never raises on adversarial draft
# ---------------------------------------------------------------------------

class TestSafeFailure:
    def test_validator_does_not_crash_on_empty_adversarial_bundle(self, validator):
        claim = Claim(
            claim_id="c1",
            type=ClaimType.PRICE,
            scope=ClaimScope.PRODUCT,
            product_id="NOPE",
            value={"price_minor": 1},
            sentence="Invented price ₹0.01 for NOPE.",
            evidence_ids=["prod:NOPE:price", "not-a-valid-id"],
        )
        draft = _draft(answer_text=claim.sentence + " and more free text!!!", claims=[claim], evidence=[])
        report = validator.validate(draft, _digest_from_claims([claim]))
        assert report.ok is False
        assert len(report.violations) >= 1
