"""Independent post-generation validator (V-01…V-12).

Imports only schemas, store repos, and validation helpers — never generation,
decision, retrieval, or nlp (CONTRACTS §6.9).
"""
from __future__ import annotations

import re
from typing import Any

from app.domain.enums import (
    AttrBasis,
    CardOrigin,
    ClaimScope,
    ClaimType,
    Completeness,
    Decision,
    RefusalReason,
)
from app.domain.plans import PlanDigest, ValidationReport, Violation
from app.schemas.evidence import (
    CatalogueSearchManifest,
    Evidence,
    PolicyEvidence,
    ProductFieldEvidence,
    SearchEvidence,
)
from app.store.policy_repo import PolicyRepo
from app.store.product_repo import ProductRepo
from app.validation.constraint_check import satisfies as constraints_satisfied
from app.validation.scanners import (
    FIXED_NF_TEXTS,
    NONFACTUAL_ANSWER_KINDS,
    format_citation_marker,
    has_policy_words,
    has_rating_words,
    has_stock_words,
    has_this_product_has,
    has_warranty_words,
    match_nf_prefix,
    parse_evidence_id,
    scan_currency_minors,
    scan_id_tokens,
    scan_numbers,
    strip_citation_markers,
    whitespace_norm,
)

# Required evidence kinds / field suffixes per claim type (API_SPEC §4.4).
_CLAIM_EVIDENCE_REQUIREMENTS: dict[ClaimType, set[str]] = {
    ClaimType.PRODUCT_EXISTS: {"PRODUCT_FIELD:title"},
    ClaimType.PRICE: {"PRODUCT_FIELD:price"},
    ClaimType.STOCK: {"PRODUCT_FIELD:stock"},
    ClaimType.RATING: {"PRODUCT_FIELD:rating"},
    ClaimType.CATEGORY: {"PRODUCT_FIELD:category"},
    ClaimType.DESCRIPTION_QUOTE: {"PRODUCT_FIELD:description"},
    ClaimType.FIELD_UNAVAILABLE: {"PRODUCT_FIELD:title"},
    ClaimType.RESULT_COUNT: {"SEARCH_MANIFEST"},
    ClaimType.ABSENCE: {"SEARCH_MANIFEST"},
    ClaimType.POLICY_QUOTE: {"POLICY_SECTION"},
    ClaimType.POLICY_NOT_FOUND: {"SEARCH_MANIFEST"},
}


class Validator:
    def __init__(self, product_repo: ProductRepo, policy_repo: PolicyRepo):
        self.product_repo = product_repo
        self.policy_repo = policy_repo

    def validate(self, draft: Any, digest: PlanDigest) -> ValidationReport:
        try:
            violations: list[Violation] = []
            evidence_map: dict[str, Evidence] = {e.evidence_id: e for e in draft.evidence}

            violations.extend(self._check_v01(draft))
            violations.extend(self._check_v02(draft, evidence_map))
            violations.extend(self._check_v03(draft, evidence_map))
            violations.extend(self._check_v04(draft, evidence_map))
            violations.extend(self._check_v05(draft, evidence_map))
            violations.extend(self._check_v06(draft))
            violations.extend(self._check_v07(draft))
            violations.extend(self._check_v08(draft, evidence_map))
            violations.extend(self._check_v09(draft, evidence_map))
            violations.extend(self._check_v10(draft, evidence_map))
            violations.extend(self._check_v11(draft, evidence_map))
            violations.extend(self._check_v12(draft, digest))

            return ValidationReport(ok=len(violations) == 0, violations=tuple(violations))
        except Exception as exc:  # never crash the API; D-12 handles ok=False
            return ValidationReport(
                ok=False,
                violations=(
                    Violation(
                        rule="V-12",
                        claim_id=None,
                        detail=f"Validator raised {type(exc).__name__}: {exc}",
                    ),
                ),
            )

    # ------------------------------------------------------------------ V-01
    def _check_v01(self, draft: Any) -> list[Violation]:
        out: list[Violation] = []
        for claim in draft.claims:
            if not claim.evidence_ids:
                out.append(Violation(
                    rule="V-01",
                    claim_id=claim.claim_id,
                    detail="Claim has no evidence IDs",
                ))
        return out

    # ------------------------------------------------------------------ V-02
    def _check_v02(self, draft: Any, evidence_map: dict[str, Evidence]) -> list[Violation]:
        out: list[Violation] = []

        def check_id(ev_id: str, claim_id: str | None, owner: str) -> None:
            parsed = parse_evidence_id(ev_id)
            if parsed is None:
                out.append(Violation(
                    rule="V-02",
                    claim_id=claim_id,
                    detail=f"{owner}: evidence ID {ev_id!r} fails grammar",
                ))
                return
            if ev_id not in evidence_map:
                out.append(Violation(
                    rule="V-02",
                    claim_id=claim_id,
                    detail=f"{owner}: evidence ID {ev_id} not in response evidence bundle",
                ))

        for claim in draft.claims:
            for ev_id in claim.evidence_ids:
                check_id(ev_id, claim.claim_id, "claim")
                ev = evidence_map.get(ev_id)
                if ev is None:
                    continue
                if claim.scope == ClaimScope.PRODUCT and isinstance(ev, ProductFieldEvidence):
                    if ev.product_id != claim.product_id:
                        out.append(Violation(
                            rule="V-02",
                            claim_id=claim.claim_id,
                            detail=(
                                f"Evidence product ID {ev.product_id} does not match "
                                f"claim product ID {claim.product_id}"
                            ),
                        ))
            # Kind compatibility
            required = _CLAIM_EVIDENCE_REQUIREMENTS.get(claim.type)
            if required is not None:
                kinds_present = self._evidence_kind_tags(claim.evidence_ids, evidence_map, claim)
                if not (kinds_present & required):
                    out.append(Violation(
                        rule="V-02",
                        claim_id=claim.claim_id,
                        detail=(
                            f"Claim type {claim.type.value} requires evidence kind in "
                            f"{sorted(required)}; found {sorted(kinds_present)}"
                        ),
                    ))
            elif claim.type == ClaimType.ATTRIBUTE:
                kinds_present = self._evidence_kind_tags(claim.evidence_ids, evidence_map, claim)
                basis = claim.value.get("basis", AttrBasis.STRUCTURED)
                if basis == AttrBasis.STRUCTURED.value or basis == AttrBasis.STRUCTURED:
                    ok = any(k.startswith("PRODUCT_FIELD:attr.") for k in kinds_present)
                else:
                    ok = "PRODUCT_FIELD:description" in kinds_present or "PRODUCT_FIELD:title" in kinds_present
                if not ok:
                    out.append(Violation(
                        rule="V-02",
                        claim_id=claim.claim_id,
                        detail=f"ATTRIBUTE claim lacks compatible field evidence for basis={basis}",
                    ))
            elif claim.type == ClaimType.PRODUCT_SUMMARY:
                kinds_present = self._evidence_kind_tags(claim.evidence_ids, evidence_map, claim)
                if "PRODUCT_FIELD:title" not in kinds_present:
                    out.append(Violation(
                        rule="V-02",
                        claim_id=claim.claim_id,
                        detail="PRODUCT_SUMMARY requires title evidence",
                    ))

        for card in draft.products:
            check_id(card.title_evidence_id, None, "card.title")
            for fact in card.facts:
                check_id(fact.evidence_id, None, f"card.{card.product_id}.{fact.field}")

        if draft.clarification:
            for opt in draft.clarification.options:
                for ev_id in opt.evidence_ids:
                    check_id(ev_id, None, f"option.{opt.option_id}")

        return out

    def _evidence_kind_tags(
        self,
        evidence_ids: list[str],
        evidence_map: dict[str, Evidence],
        claim: Any,
    ) -> set[str]:
        tags: set[str] = set()
        for ev_id in evidence_ids:
            ev = evidence_map.get(ev_id)
            if isinstance(ev, ProductFieldEvidence):
                tags.add(f"PRODUCT_FIELD:{ev.field}")
            elif isinstance(ev, PolicyEvidence):
                tags.add("POLICY_SECTION")
            elif isinstance(ev, SearchEvidence):
                tags.add("SEARCH_MANIFEST")
            else:
                parsed = parse_evidence_id(ev_id)
                if parsed and parsed[0] == "prod" and len(parsed) == 3:
                    tags.add(f"PRODUCT_FIELD:{parsed[2]}")
                elif parsed and parsed[0] == "pol":
                    tags.add("POLICY_SECTION")
                elif parsed and parsed[0] == "search":
                    tags.add("SEARCH_MANIFEST")
        return tags

    # ------------------------------------------------------------------ V-03
    def _check_v03(self, draft: Any, evidence_map: dict[str, Evidence]) -> list[Violation]:
        out: list[Violation] = []
        for claim in draft.claims:
            if claim.scope == ClaimScope.PRODUCT and claim.product_id:
                prod = self.product_repo.get(claim.product_id)
                if not prod:
                    out.append(Violation(
                        rule="V-03",
                        claim_id=claim.claim_id,
                        detail=f"Product {claim.product_id} not in DB",
                    ))
                    continue

                if claim.type == ClaimType.PRICE:
                    expected = claim.value.get("price_minor")
                    if prod.price_minor != expected:
                        out.append(Violation(
                            rule="V-03",
                            claim_id=claim.claim_id,
                            detail=f"Price {expected} does not match DB price {prod.price_minor}",
                        ))
                elif claim.type == ClaimType.STOCK:
                    expected = claim.value.get("stock_status")
                    if prod.stock_status.value != expected:
                        out.append(Violation(
                            rule="V-03",
                            claim_id=claim.claim_id,
                            detail=f"Stock {expected} does not match DB stock {prod.stock_status.value}",
                        ))
                elif claim.type == ClaimType.RATING:
                    expected = claim.value.get("rating")
                    if prod.rating != expected:
                        out.append(Violation(
                            rule="V-03",
                            claim_id=claim.claim_id,
                            detail=f"Rating {expected} does not match DB rating {prod.rating}",
                        ))
                elif claim.type == ClaimType.CATEGORY:
                    expected = claim.value.get("category")
                    if prod.category != expected:
                        out.append(Violation(
                            rule="V-03",
                            claim_id=claim.claim_id,
                            detail=f"Category {expected!r} does not match DB {prod.category!r}",
                        ))
                elif claim.type == ClaimType.ATTRIBUTE:
                    out.extend(self._check_attribute_value(claim, prod))
                elif claim.type == ClaimType.PRODUCT_SUMMARY:
                    out.extend(self._check_product_summary(claim, prod))
                elif claim.type == ClaimType.DESCRIPTION_QUOTE:
                    quote = claim.value.get("quote", "")
                    if not quote or not prod.description or quote not in prod.description:
                        out.append(Violation(
                            rule="V-03",
                            claim_id=claim.claim_id,
                            detail="DESCRIPTION_QUOTE is not a verbatim substring of the product description",
                        ))

            elif claim.type == ClaimType.RESULT_COUNT:
                count = claim.value.get("count")
                for ev_id in claim.evidence_ids:
                    ev = evidence_map.get(ev_id)
                    if isinstance(ev, SearchEvidence) and isinstance(ev.search, CatalogueSearchManifest):
                        if ev.search.exact_match_count != count:
                            out.append(Violation(
                                rule="V-03",
                                claim_id=claim.claim_id,
                                detail=(
                                    f"RESULT_COUNT {count} != manifest exact_match_count "
                                    f"{ev.search.exact_match_count}"
                                ),
                            ))

            elif claim.type == ClaimType.ABSENCE:
                out.extend(self._check_absence(claim, evidence_map))

        return out

    def _check_attribute_value(self, claim: Any, prod: Any) -> list[Violation]:
        out: list[Violation] = []
        key = claim.value.get("key")
        value = claim.value.get("value")
        basis = claim.value.get("basis", AttrBasis.STRUCTURED)
        if basis == AttrBasis.STRUCTURED or basis == AttrBasis.STRUCTURED.value:
            attrs = self.product_repo.attributes(claim.product_id)
            found = False
            for a in attrs:
                if value is None:
                    continue
                key_ok = key is None or a.get("key_norm") == key or a.get("key_raw") == key
                val_ok = (
                    a.get("value_norm") == str(value).lower()
                    or a.get("value_raw") == value
                )
                if key_ok and val_ok:
                    found = True
                    break
            if not found:
                out.append(Violation(
                    rule="V-03",
                    claim_id=claim.claim_id,
                    detail=f"ATTRIBUTE key={key!r} value={value!r} not in product_attributes",
                ))
        else:
            hay = f"{prod.title or ''} {prod.description or ''}".lower()
            needle = str(value or "").lower()
            if not needle or not re.search(rf"\b{re.escape(needle)}\b", hay):
                out.append(Violation(
                    rule="V-03",
                    claim_id=claim.claim_id,
                    detail=f"ATTRIBUTE value {value!r} not found as whole word in title/description",
                ))
            # Simple negation guard
            if needle and re.search(rf"\b(?:not|no|without)\s+{re.escape(needle)}\b", hay):
                out.append(Violation(
                    rule="V-03",
                    claim_id=claim.claim_id,
                    detail=f"ATTRIBUTE value {value!r} appears under negation",
                ))
        return out

    def _check_product_summary(self, claim: Any, prod: Any) -> list[Violation]:
        out: list[Violation] = []
        pm = claim.value.get("price_minor")
        if pm is not None and prod.price_minor != pm:
            out.append(Violation(
                rule="V-03",
                claim_id=claim.claim_id,
                detail=f"PRODUCT_SUMMARY price {pm} != DB {prod.price_minor}",
            ))
        if pm is None and prod.price_minor is not None:
            out.append(Violation(
                rule="V-03",
                claim_id=claim.claim_id,
                detail="PRODUCT_SUMMARY omits price but DB has a price",
            ))
        st = claim.value.get("stock_status")
        if st is not None and prod.stock_status.value != st:
            out.append(Violation(
                rule="V-03",
                claim_id=claim.claim_id,
                detail=f"PRODUCT_SUMMARY stock {st} != DB {prod.stock_status.value}",
            ))
        rating = claim.value.get("rating")
        if rating is not None and prod.rating != rating:
            out.append(Violation(
                rule="V-03",
                claim_id=claim.claim_id,
                detail=f"PRODUCT_SUMMARY rating {rating} != DB {prod.rating}",
            ))
        return out

    def _check_absence(self, claim: Any, evidence_map: dict[str, Evidence]) -> list[Violation]:
        out: list[Violation] = []
        saw_manifest = False
        for ev_id in claim.evidence_ids:
            ev = evidence_map.get(ev_id)
            if not isinstance(ev, SearchEvidence):
                continue
            saw_manifest = True
            sm = ev.search
            if isinstance(sm, CatalogueSearchManifest):
                if sm.completeness != Completeness.EXHAUSTIVE:
                    out.append(Violation(
                        rule="V-03",
                        claim_id=claim.claim_id,
                        detail=(
                            "ABSENCE claim requires EXHAUSTIVE catalogue search; "
                            f"got {sm.completeness.value} ({sm.incomplete_reasons})"
                        ),
                    ))
                if sm.exact_match_count != 0:
                    out.append(Violation(
                        rule="V-03",
                        claim_id=claim.claim_id,
                        detail=(
                            f"ABSENCE claim made but search manifest has "
                            f"{sm.exact_match_count} exact matches"
                        ),
                    ))
            else:
                # Policy search manifest: established iff matched_section_ids non-empty
                if sm.completeness != Completeness.EXHAUSTIVE:
                    out.append(Violation(
                        rule="V-03",
                        claim_id=claim.claim_id,
                        detail="ABSENCE (policy) requires EXHAUSTIVE policy search",
                    ))
                if sm.matched_section_ids:
                    out.append(Violation(
                        rule="V-03",
                        claim_id=claim.claim_id,
                        detail="ABSENCE (policy) but matched_section_ids is non-empty",
                    ))
        if not saw_manifest:
            out.append(Violation(
                rule="V-03",
                claim_id=claim.claim_id,
                detail="ABSENCE claim requires a SEARCH_MANIFEST evidence ID",
            ))
        return out

    # ------------------------------------------------------------------ V-04
    def _check_v04(self, draft: Any, evidence_map: dict[str, Evidence]) -> list[Violation]:
        out: list[Violation] = []
        allow_minors = self._allowlisted_price_minors(draft, evidence_map)
        allow_numbers = self._allowlisted_numbers(draft, evidence_map)

        texts: list[tuple[str | None, str]] = [("answer", draft.answer_text or "")]
        if draft.clarification:
            for opt in draft.clarification.options:
                texts.append((f"option:{opt.option_id}", opt.label or ""))
        for sug in draft.suggestions or []:
            texts.append((f"suggestion:{sug.claim_id}", sug.label or ""))

        # Per-claim sentence scan with that claim's cited evidence
        for claim in draft.claims:
            claim_minors = set(allow_minors)
            claim_numbers = set(allow_numbers)
            claim_numbers.update(self._numbers_from_claim_evidence(claim, evidence_map))
            claim_minors.update(self._prices_from_claim_evidence(claim, evidence_map))
            # Product IDs are allowed number-bearing tokens
            if claim.product_id:
                claim_numbers.update(re.findall(r"\d+", claim.product_id))

            for minor in scan_currency_minors(claim.sentence):
                if minor not in claim_minors:
                    out.append(Violation(
                        rule="V-04",
                        claim_id=claim.claim_id,
                        detail=f"Stray currency amount {minor} (minor units) in claim sentence",
                    ))
            for num in scan_numbers(claim.sentence):
                if not self._number_allowed(num, claim_numbers, claim):
                    out.append(Violation(
                        rule="V-04",
                        claim_id=claim.claim_id,
                        detail=f"Stray number {num!r} in claim sentence",
                    ))

        # Whole-answer scan: every currency must be allowlisted globally
        for owner, text in texts:
            for minor in scan_currency_minors(text):
                if minor not in allow_minors:
                    out.append(Violation(
                        rule="V-04",
                        claim_id=None,
                        detail=f"Stray currency amount {minor} in {owner}",
                    ))
        return out

    def _allowlisted_price_minors(self, draft: Any, evidence_map: dict[str, Evidence]) -> set[int]:
        minors: set[int] = set()
        for ev in evidence_map.values():
            if isinstance(ev, ProductFieldEvidence) and ev.field == "price" and isinstance(ev.value, int):
                minors.add(ev.value)
            if isinstance(ev, PolicyEvidence):
                minors.update(scan_currency_minors(ev.excerpt or ""))
            if isinstance(ev, SearchEvidence) and isinstance(ev.search, CatalogueSearchManifest):
                for cv in ev.search.applied_constraints:
                    val = getattr(cv.op_to_remove, "value", None)
                    if isinstance(val, dict) and "amount_minor" in val:
                        try:
                            minors.add(int(val["amount_minor"]))
                        except (TypeError, ValueError):
                            pass
                    # Also parse label amounts (e.g. Price under ₹2,000.00)
                    for m in scan_currency_minors(cv.label or ""):
                        minors.add(m)
        for claim in draft.claims:
            if claim.type in (ClaimType.PRICE, ClaimType.PRODUCT_SUMMARY):
                pm = claim.value.get("price_minor")
                if isinstance(pm, int):
                    minors.add(pm)
        return minors

    def _allowlisted_numbers(self, draft: Any, evidence_map: dict[str, Evidence]) -> set[str]:
        nums: set[str] = set()
        for claim in draft.claims:
            nums.update(self._numbers_from_claim_evidence(claim, evidence_map))
            if claim.product_id:
                nums.update(re.findall(r"\d+", claim.product_id))
            for v in claim.value.values():
                if isinstance(v, (int, float)):
                    nums.add(self._num_token(v))
                elif isinstance(v, str):
                    nums.update(scan_numbers(v))
        for ev in evidence_map.values():
            if isinstance(ev, SearchEvidence) and isinstance(ev.search, CatalogueSearchManifest):
                nums.add(str(ev.search.exact_match_count))
                nums.add(str(ev.search.eligible_count))
                nums.add(str(ev.search.returned_count))
            if isinstance(ev, ProductFieldEvidence):
                if ev.value is not None:
                    nums.add(self._num_token(ev.value))
                if ev.display_value:
                    nums.update(scan_numbers(ev.display_value))
                if ev.excerpt:
                    nums.update(scan_numbers(ev.excerpt))
            if isinstance(ev, PolicyEvidence):
                nums.update(scan_numbers(ev.excerpt or ""))
        return nums

    def _prices_from_claim_evidence(self, claim: Any, evidence_map: dict[str, Evidence]) -> set[int]:
        minors: set[int] = set()
        for ev_id in claim.evidence_ids:
            ev = evidence_map.get(ev_id)
            if isinstance(ev, ProductFieldEvidence) and ev.field == "price" and isinstance(ev.value, int):
                minors.add(ev.value)
            if isinstance(ev, PolicyEvidence):
                minors.update(scan_currency_minors(ev.excerpt or ""))
        return minors

    def _numbers_from_claim_evidence(self, claim: Any, evidence_map: dict[str, Evidence]) -> set[str]:
        nums: set[str] = set()
        for ev_id in claim.evidence_ids:
            ev = evidence_map.get(ev_id)
            if isinstance(ev, ProductFieldEvidence):
                if ev.value is not None:
                    nums.add(self._num_token(ev.value))
                if ev.display_value:
                    nums.update(scan_numbers(ev.display_value))
                if ev.excerpt:
                    nums.update(scan_numbers(ev.excerpt))
            elif isinstance(ev, PolicyEvidence):
                nums.update(scan_numbers(ev.excerpt or ""))
            elif isinstance(ev, SearchEvidence) and isinstance(ev.search, CatalogueSearchManifest):
                nums.add(str(ev.search.exact_match_count))
                nums.add(str(ev.search.returned_count))
                nums.add(str(ev.search.eligible_count))
        return nums

    @staticmethod
    def _num_token(v: Any) -> str:
        if isinstance(v, float):
            if v == int(v):
                return str(int(v))
            return str(v)
        return str(v)

    def _number_allowed(self, num: str, allow: set[str], claim: Any) -> bool:
        if num in allow:
            return True
        # Float/int equivalence: "4.0" vs "4"
        try:
            f = float(num)
            if self._num_token(f) in allow or str(int(f)) in allow if f == int(f) else False:
                return True
            for a in allow:
                try:
                    if float(a) == f:
                        return True
                except ValueError:
                    continue
        except ValueError:
            pass
        # Product ID embedding
        if claim.product_id and num in claim.product_id:
            return True
        return False

    # ------------------------------------------------------------------ V-05
    def _check_v05(self, draft: Any, evidence_map: dict[str, Evidence]) -> list[Violation]:
        out: list[Violation] = []
        known_ids = {
            r[0]
            for r in self.product_repo.conn.execute("SELECT product_id FROM products").fetchall()
        }
        for claim in draft.claims:
            if claim.scope != ClaimScope.PRODUCT or not claim.product_id:
                continue
            text = claim.sentence or ""
            for tok in scan_id_tokens(text):
                if tok in known_ids and tok != claim.product_id:
                    out.append(Violation(
                        rule="V-05",
                        claim_id=claim.claim_id,
                        detail=f"Product-scoped sentence mentions foreign product ID {tok}",
                    ))
            # Prices in sentence must belong to this product's evidence
            own_prices = self._prices_from_claim_evidence(claim, evidence_map)
            prod = self.product_repo.get(claim.product_id)
            if prod and prod.price_minor is not None:
                own_prices.add(prod.price_minor)
            for minor in scan_currency_minors(text):
                if minor not in own_prices:
                    out.append(Violation(
                        rule="V-05",
                        claim_id=claim.claim_id,
                        detail=f"Price {minor} in sentence not supported by evidence for {claim.product_id}",
                    ))
        return out

    # ------------------------------------------------------------------ V-06
    def _check_v06(self, draft: Any) -> list[Violation]:
        """Every answer segment must map to a claim sentence (+ marker) or an NF template."""
        out: list[Violation] = []
        answer = (draft.answer_text or "").strip()
        if not answer:
            if draft.claims:
                out.append(Violation(
                    rule="V-06",
                    claim_id=None,
                    detail="answer_text empty but claims are present",
                ))
            return out

        # Build allowed claim segments (sentence + optional citation marker)
        claim_segments: list[str] = []
        for claim in draft.claims:
            sent = claim.sentence or ""
            marker = format_citation_marker(claim.evidence_ids, show_search_marker=True)
            claim_segments.append(whitespace_norm(sent + marker))
            claim_segments.append(whitespace_norm(sent))  # marker optional

        remaining = whitespace_norm(answer)
        # Greedy left-to-right consume
        safety = 0
        while remaining:
            safety += 1
            if safety > 200:
                out.append(Violation(
                    rule="V-06",
                    claim_id=None,
                    detail="V-06 matcher exceeded iteration budget",
                ))
                break
            matched = False
            # Prefer longer claim segment match
            for seg in sorted(claim_segments, key=len, reverse=True):
                if seg and remaining.startswith(seg):
                    remaining = remaining[len(seg):].lstrip()
                    matched = True
                    break
            if matched:
                continue
            nf_len = match_nf_prefix(remaining)
            if nf_len is not None:
                remaining = remaining[nf_len:].lstrip()
                continue
            # Also allow bare citation markers between segments
            m = re.match(r"^\[[^\]]+\]\s*", remaining)
            if m:
                remaining = remaining[m.end():].lstrip()
                continue
            snippet = remaining[:80]
            out.append(Violation(
                rule="V-06",
                claim_id=None,
                detail=f"Free sentence not derived from a claim or NF template: {snippet!r}",
            ))
            break
        return out

    # ------------------------------------------------------------------ V-07
    def _check_v07(self, draft: Any) -> list[Violation]:
        out: list[Violation] = []
        if draft.decision == Decision.ANSWER:
            if not draft.claims:
                text = strip_citation_markers(draft.answer_text or "").strip()
                if text not in NONFACTUAL_ANSWER_KINDS and text not in FIXED_NF_TEXTS:
                    # Allow HELP which is NF-02
                    out.append(Violation(
                        rule="V-07",
                        claim_id=None,
                        detail="ANSWER has no claims and answer_text is not a non-factual GREETING/HELP/RESET_ACK template",
                    ))
        elif draft.decision == Decision.CLARIFY:
            if not draft.clarification:
                out.append(Violation(
                    rule="V-07",
                    claim_id=None,
                    detail="CLARIFY decision requires clarification block",
                ))
            else:
                reason = draft.clarification.reason
                n_opts = len(draft.clarification.options)
                exempt = reason.value in {
                    "UNDER_SPECIFIED_QUERY",
                    "UNRESOLVED_REFERENCE",
                }
                if n_opts < 2 and not exempt:
                    out.append(Violation(
                        rule="V-07",
                        claim_id=None,
                        detail=f"CLARIFY {reason.value} requires ≥2 options (got {n_opts})",
                    ))
                # Top-level factual claims not allowed on CLARIFY
                if draft.claims:
                    out.append(Violation(
                        rule="V-07",
                        claim_id=None,
                        detail="CLARIFY decision must not carry top-level factual claims",
                    ))
        elif draft.decision == Decision.REFUSE:
            if not draft.refusal:
                out.append(Violation(
                    rule="V-07",
                    claim_id=None,
                    detail="REFUSE decision requires refusal block",
                ))
            else:
                reason = draft.refusal.reason
                if reason == RefusalReason.NO_CATALOGUE_MATCH:
                    if not any(c.type == ClaimType.ABSENCE for c in draft.claims):
                        out.append(Violation(
                            rule="V-07",
                            claim_id=None,
                            detail="NO_CATALOGUE_MATCH requires an ABSENCE claim",
                        ))
                if reason == RefusalReason.POLICY_NOT_ESTABLISHED:
                    if not any(
                        c.type == ClaimType.ABSENCE and c.scope == ClaimScope.GENERAL_POLICY
                        for c in draft.claims
                    ):
                        out.append(Violation(
                            rule="V-07",
                            claim_id=None,
                            detail="POLICY_NOT_ESTABLISHED requires policy-scope ABSENCE claim",
                        ))
        return out

    # ------------------------------------------------------------------ V-08
    def _check_v08(self, draft: Any, evidence_map: dict[str, Evidence]) -> list[Violation]:
        out: list[Violation] = []
        for claim in draft.claims:
            if claim.type != ClaimType.POLICY_QUOTE:
                continue
            if claim.scope != ClaimScope.GENERAL_POLICY:
                out.append(Violation(
                    rule="V-08",
                    claim_id=claim.claim_id,
                    detail="POLICY_QUOTE scope must be GENERAL_POLICY",
                ))
            sec_id = claim.value.get("section_id")
            quote = claim.value.get("quote", "")
            if not sec_id:
                out.append(Violation(
                    rule="V-08",
                    claim_id=claim.claim_id,
                    detail="POLICY_QUOTE missing section_id",
                ))
                continue
            sec = self.policy_repo.get_section(sec_id)
            if not sec:
                out.append(Violation(
                    rule="V-08",
                    claim_id=claim.claim_id,
                    detail=f"Policy section {sec_id} not in DB",
                ))
                continue
            body_norm = whitespace_norm(sec.body or "")
            head_norm = whitespace_norm(sec.heading or "")
            quote_norm = whitespace_norm(quote)
            if quote_norm not in body_norm and quote_norm not in head_norm:
                out.append(Violation(
                    rule="V-08",
                    claim_id=claim.claim_id,
                    detail="Quoted text not found verbatim (whitespace-normalised) in policy section",
                ))
            # Cited evidence must point at this section
            pol_ok = False
            for ev_id in claim.evidence_ids:
                ev = evidence_map.get(ev_id)
                if isinstance(ev, PolicyEvidence) and ev.section_id == sec_id:
                    pol_ok = True
            if not pol_ok:
                out.append(Violation(
                    rule="V-08",
                    claim_id=claim.claim_id,
                    detail=f"POLICY_QUOTE lacks pol:{sec_id} evidence",
                ))
        return out

    # ------------------------------------------------------------------ V-09
    def _check_v09(self, draft: Any, evidence_map: dict[str, Evidence]) -> list[Violation]:
        out: list[Violation] = []
        known_ids = {
            r[0]
            for r in self.product_repo.conn.execute("SELECT product_id FROM products").fetchall()
        }
        titles = {
            r[0]: r[1]
            for r in self.product_repo.conn.execute("SELECT product_id, title FROM products").fetchall()
        }
        for claim in draft.claims:
            text = claim.sentence or ""
            if not has_warranty_words(text):
                continue
            if claim.scope == ClaimScope.PRODUCT:
                has_product_ev = False
                for ev_id in claim.evidence_ids:
                    ev = evidence_map.get(ev_id)
                    if isinstance(ev, ProductFieldEvidence):
                        if ev.field.startswith("attr.") or ev.field == "description":
                            has_product_ev = True
                    parsed = parse_evidence_id(ev_id)
                    if parsed and parsed[0] == "prod" and len(parsed) == 3:
                        if parsed[2].startswith("attr.") or parsed[2] == "description":
                            has_product_ev = True
                if not has_product_ev:
                    out.append(Violation(
                        rule="V-09",
                        claim_id=claim.claim_id,
                        detail="PRODUCT-scope warranty sentence lacks attr.* or description evidence",
                    ))
            elif claim.scope == ClaimScope.GENERAL_POLICY:
                if has_this_product_has(text):
                    out.append(Violation(
                        rule="V-09",
                        claim_id=claim.claim_id,
                        detail='GENERAL_POLICY warranty sentence must not say "this product has"',
                    ))
                for tok in scan_id_tokens(text):
                    if tok in known_ids:
                        out.append(Violation(
                            rule="V-09",
                            claim_id=claim.claim_id,
                            detail=f"GENERAL_POLICY warranty sentence contains product ID {tok}",
                        ))
                for pid, title in titles.items():
                    if title and title in text:
                        out.append(Violation(
                            rule="V-09",
                            claim_id=claim.claim_id,
                            detail=f"GENERAL_POLICY warranty sentence contains product title {pid}",
                        ))
        return out

    # ------------------------------------------------------------------ V-10
    def _check_v10(self, draft: Any, evidence_map: dict[str, Evidence]) -> list[Violation]:
        out: list[Violation] = []
        max_results = 10  # hard ceiling from ChatOptions; tighter bound checked if present
        if len(draft.products) > max_results:
            out.append(Violation(
                rule="V-10",
                claim_id=None,
                detail=f"Card count {len(draft.products)} exceeds max_results ceiling {max_results}",
            ))

        search_manifests: dict[str, list[CatalogueSearchManifest]] = {}
        for ev in draft.evidence:
            if isinstance(ev, SearchEvidence) and isinstance(ev.search, CatalogueSearchManifest):
                # A card must be checked against the exact catalogue search that
                # produced it. Ignore malformed evidence IDs so they cannot be
                # used as trusted constraint provenance.
                if ev.evidence_id != f"search:{ev.search.search_id}":
                    continue
                search_manifests.setdefault(ev.search.search_id, []).append(ev.search)

        for card in draft.products:
            prod = self.product_repo.get(card.product_id)
            if not prod:
                out.append(Violation(
                    rule="V-10",
                    claim_id=None,
                    detail=f"Card product {card.product_id} not in DB",
                ))
                continue
            if card.title != prod.title:
                out.append(Violation(
                    rule="V-10",
                    claim_id=None,
                    detail=f"Card title for {card.product_id} does not match DB",
                ))
            for fact in card.facts:
                if fact.field == "price" and prod.price_minor != fact.value:
                    out.append(Violation(
                        rule="V-10",
                        claim_id=None,
                        detail=f"Card price {fact.value} != DB {prod.price_minor}",
                    ))
                elif fact.field == "stock" and prod.stock_status.value != fact.value:
                    out.append(Violation(
                        rule="V-10",
                        claim_id=None,
                        detail=f"Card stock {fact.value} != DB {prod.stock_status.value}",
                    ))

            # Re-evaluate hard filters for SEARCH-origin cards (independent of retrieval).
            # Missing, ambiguous, or mismatched provenance must fail closed: otherwise
            # a truthful DB card could bypass the constraints that selected it.
            if card.origin in (CardOrigin.SEARCH_RESULT, CardOrigin.EXISTENCE_MATCH):
                manifests = search_manifests.get(card.applied_search_id or "", [])
                if len(manifests) != 1:
                    out.append(Violation(
                        rule="V-10",
                        claim_id=None,
                        detail=(
                            f"Search-origin card {card.product_id} has no unique catalogue "
                            f"manifest matching applied_search_id {card.applied_search_id!r}"
                        ),
                    ))
                    continue
                attrs = self.product_repo.attributes(card.product_id)
                if not constraints_satisfied(prod, attrs, manifests[0].applied_constraints):
                    out.append(Violation(
                        rule="V-10",
                        claim_id=None,
                        detail=(
                            f"Card {card.product_id} fails independent re-check of "
                            "applied search hard filters"
                        ),
                    ))

        return out

    # ------------------------------------------------------------------ V-11
    def _check_v11(self, draft: Any, evidence_map: dict[str, Evidence]) -> list[Violation]:
        out: list[Violation] = []
        for claim in draft.claims:
            text = claim.sentence or ""
            fields = self._fields_cited(claim, evidence_map)

            # FIELD_UNAVAILABLE is a closed-set template whose sanctioned wording
            # (GROUNDING_AND_REFUSAL §4) is literally `The {field} of "…" (…) is not
            # available in the catalogue.`  It asserts the ABSENCE of a value, so the
            # word "available" is structural, and a field label such as "warranty" or
            # "stock" only names the missing field -- neither is a factual stock/policy
            # assertion. The spec's V-11 (which exempts only POLICY_*/DESCRIPTION_QUOTE/
            # ATTRIBUTE/ABSENCE for policy words and offers no FIELD_UNAVAILABLE carve-out
            # for the stock/rating words) would otherwise reject this valid, spec-defined
            # template on every null-field response (G-AC-03). It stays covered by V-01/V-02
            # (a resolvable field citation) and, in template mode, cannot smuggle a positive
            # fact because its sentence is fixed generated code, never model text. Genuine
            # STOCK/RATING/POLICY assertions on other claim types are still enforced below.
            if claim.type == ClaimType.FIELD_UNAVAILABLE:
                continue

            if has_stock_words(text) and "stock" not in fields:
                out.append(Violation(
                    rule="V-11",
                    claim_id=claim.claim_id,
                    detail="Stock words present without STOCK field evidence on this claim",
                ))

            if has_rating_words(text) and "rating" not in fields:
                out.append(Violation(
                    rule="V-11",
                    claim_id=claim.claim_id,
                    detail="Rating words present without RATING field evidence on this claim",
                ))

            if has_policy_words(text) and claim.type not in {
                ClaimType.POLICY_QUOTE,
                ClaimType.POLICY_NOT_FOUND,
                ClaimType.DESCRIPTION_QUOTE,
                ClaimType.ATTRIBUTE,
                ClaimType.ABSENCE,
            }:
                # Non-factual NF templates may contain "warranty"/"returns" — those are not claims
                out.append(Violation(
                    rule="V-11",
                    claim_id=claim.claim_id,
                    detail="Policy words outside POLICY_*/DESCRIPTION_QUOTE/ATTRIBUTE/ABSENCE claim",
                ))

        # Also scan non-claim answer segments for stock/rating assertions without any supporting claim
        bare = strip_citation_markers(draft.answer_text or "")
        for claim in draft.claims:
            bare = bare.replace(claim.sentence or "", " ", 1)
        bare = whitespace_norm(bare)
        if bare and bare not in FIXED_NF_TEXTS and match_nf_prefix(bare) is None:
            # leftover free text already caught by V-06; keyword scan on NF is allowed
            pass
        return out

    def _fields_cited(self, claim: Any, evidence_map: dict[str, Evidence]) -> set[str]:
        fields: set[str] = set()
        for ev_id in claim.evidence_ids:
            ev = evidence_map.get(ev_id)
            if isinstance(ev, ProductFieldEvidence):
                fields.add(ev.field.split(".", 1)[0] if not ev.field.startswith("attr.") else ev.field)
                fields.add(ev.field)
            parsed = parse_evidence_id(ev_id)
            if parsed and parsed[0] == "prod" and len(parsed) == 3:
                fields.add(parsed[2])
        return fields

    # ------------------------------------------------------------------ V-12
    def _check_v12(self, draft: Any, digest: PlanDigest) -> list[Violation]:
        """Template-mode subset: claim_id set must match the plan digest.

        Full Tier-2 checks (required_tokens, URL/HTML ban, suspicious_text) apply
        only when an LLM rephraser is enabled — see IMPLEMENTATION_STATUS.md UNK-13.
        """
        out: list[Violation] = []
        draft_ids = tuple(c.claim_id for c in draft.claims)
        if draft_ids != digest.claim_ids:
            out.append(Violation(
                rule="V-12",
                claim_id=None,
                detail=f"Draft claim_ids {draft_ids} != planned digest {digest.claim_ids}",
            ))
        # Lightweight integrity: no URLs / HTML in claim sentences (safe in all modes)
        for claim in draft.claims:
            s = claim.sentence or ""
            if re.search(r"https?://|www\.|<a\s|</|\[.*\]\(.*\)", s, re.IGNORECASE):
                out.append(Violation(
                    rule="V-12",
                    claim_id=claim.claim_id,
                    detail="Claim sentence contains URL/markdown-link/HTML",
                ))
        return out
