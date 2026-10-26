"""Independent evaluation auditor (EVALUATION_PLAN.md §7).

Post-hoc, deterministic audit of final ``ChatResponse`` objects against the
trusted SQLite catalogue. Deliberately written apart from the runtime validator
(``app/validation``) so a shared bug cannot hide in both: this module imports
only the standard library and reads the response via duck typing (the domain
enums are ``str`` subclasses, so a value compares equal to its string name).

For every response it verifies the applicable invariants independently:
  * cited product IDs exist in the trusted repository;
  * returned products satisfy the applicable hard filters (re-evaluated from DB);
  * an unknown/NULL price is never treated as zero or as satisfying a budget cap;
  * stock / rating / category constraints are enforced;
  * factual claim values equal the source (price/stock/rating/category/attribute);
  * citations and evidence references resolve to real source records;
  * policy quotes are verbatim substrings of the cited section;
  * definitive absence claims require an EXHAUSTIVE zero-result manifest;
  * every factual claim carries evidence required by the contract;
  * no currency amount appears unless it matches a cited product price or an
    allowlisted constraint amount (price-hallucination release gate).

Findings that cannot be decided from available data are reported as
``UNVERIFIABLE`` rather than silently passed.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SEVERE = "SEVERE"
ERROR = "ERROR"
UNVERIFIABLE = "UNVERIFIABLE"

# A currency amount like ₹1,499.00 / ₹2,000 / Rs 999
_CURRENCY_RE = re.compile(r"(?:₹|Rs\.?|INR)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)")
# A rating token like "≥ 4.0★" or "4.5★"
_RATING_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*★")


@dataclass(frozen=True)
class Finding:
    case_id: str
    severity: str
    kind: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {
            "case_id": self.case_id,
            "severity": self.severity,
            "kind": self.kind,
            "detail": self.detail,
        }


def _money_to_minor(text: str) -> int:
    """'1,499.00' -> 149900 ; '2000' -> 200000."""
    clean = text.replace(",", "").strip()
    if "." in clean:
        rupees, frac = clean.split(".", 1)
        frac = (frac + "00")[:2]
    else:
        rupees, frac = clean, "00"
    return int(rupees) * 100 + int(frac)


class Auditor:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(
            f"file:{self.db_path.resolve().as_posix()}?mode=ro", uri=True
        )
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    # ---- trusted lookups -------------------------------------------------
    def _product(self, pid: str) -> sqlite3.Row | None:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM products WHERE product_id = ?", (pid,))
        return cur.fetchone()

    def _attrs(self, pid: str) -> dict[str, set[str]]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT key_norm, value_norm FROM product_attributes WHERE product_id = ?",
            (pid,),
        )
        out: dict[str, set[str]] = {}
        for r in cur.fetchall():
            out.setdefault(r["key_norm"], set()).add((r["value_norm"] or "").lower())
        return out

    def _policy_body(self, section_id: str) -> str | None:
        cur = self.conn.cursor()
        cur.execute("SELECT body FROM policy_sections WHERE section_id = ?", (section_id,))
        r = cur.fetchone()
        return r["body"] if r else None

    def _policy_section(self, section_id: str) -> sqlite3.Row | None:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT body, heading FROM policy_sections WHERE section_id = ?", (section_id,)
        )
        return cur.fetchone()

    # ---- entry point -----------------------------------------------------
    def audit(self, response: Any, case_id: str = "?") -> list[Finding]:
        findings: list[Finding] = []
        ev_index = {ev.evidence_id: ev for ev in getattr(response, "evidence", []) or []}

        claims = list(getattr(response, "claims", []) or [])
        claim_ids = {c.claim_id for c in claims}
        cited_pids: set[str] = set()

        for c in claims:
            findings += self._audit_claim(c, ev_index, cited_pids, case_id)

        for card in getattr(response, "products", []) or []:
            findings += self._audit_card(card, ev_index, case_id)
            if self._product(card.product_id) is not None:
                cited_pids.add(card.product_id)

        clar = getattr(response, "clarification", None)
        for opt in (clar.options if clar else []) or []:
            findings += self._audit_option(opt, ev_index, case_id)
            # Option labels echo a real product price (D-05 / §7); its DB price is
            # a legitimate amount, so register the option product for the allowlist.
            if getattr(opt, "product_id", None) and self._product(opt.product_id) is not None:
                cited_pids.add(opt.product_id)

        for s in getattr(response, "suggestions", []) or []:
            if s.claim_id not in claim_ids:
                findings.append(Finding(case_id, ERROR, "ORPHAN_SUGGESTION",
                                       f"suggestion references unknown claim {s.claim_id!r}"))

        # Allowlisted currency amounts: cited product DB prices + constraint amounts
        # + amounts that appear verbatim inside cited evidence text (V-04).
        allow = self._collect_price_allowlist(cited_pids, ev_index)
        findings += self._audit_currency(response, allow, case_id)
        return findings

    # ---- per-claim -------------------------------------------------------
    def _audit_claim(self, c: Any, ev_index: dict, cited_pids: set, case_id: str) -> list[Finding]:
        f: list[Finding] = []
        scope = c.scope  # str-Enum compares equal to its value; also fine as a raw str
        pid = c.product_id
        if not c.evidence_ids:
            f.append(Finding(case_id, SEVERE, "UNSUPPORTED_CLAIM",
                             f"claim {c.claim_id} ({c.type}) has no evidence"))
        for eid in c.evidence_ids:
            parsed = self._parse_evidence_id(eid)
            if parsed is None:
                f.append(Finding(case_id, ERROR, "EVIDENCE_GRAMMAR",
                                 f"claim {c.claim_id} evidence {eid!r} does not parse"))
                continue
            kind, ident, field = parsed
            if eid not in ev_index:
                f.append(Finding(case_id, ERROR, "CITATION_UNRESOLVED",
                                 f"claim {c.claim_id} evidence {eid!r} not in bundle"))
            if kind == "prod":
                cited_pids.add(ident)
                if self._product(ident) is None:
                    f.append(Finding(case_id, SEVERE, "NONEXISTENT_PRODUCT",
                                     f"claim {c.claim_id} cites product {ident!r} absent from DB"))
                if scope == "PRODUCT" and pid and ident != pid:
                    f.append(Finding(case_id, ERROR, "SUBJECT_BINDING",
                                     f"claim {c.claim_id} product {pid!r} vs evidence {ident!r}"))
            elif kind == "pol":
                if self._policy_body(ident) is None:
                    f.append(Finding(case_id, ERROR, "POLICY_CITATION_UNRESOLVED",
                                     f"claim {c.claim_id} cites policy section {ident!r} absent"))
            elif kind == "search":
                pass
        # typed value vs source
        f += self._audit_value(c, case_id, ev_index)
        return f

    def _audit_value(self, c: Any, case_id: str, ev_index: dict) -> list[Finding]:
        f: list[Finding] = []
        t = c.type  # str-Enum compares equal to its value
        v = c.value or {}
        pid = c.product_id

        if t in ("PRICE", "STOCK", "RATING", "CATEGORY", "PRODUCT_SUMMARY", "ATTRIBUTE") and pid:
            prod = self._product(pid)
            if prod is None:
                return [Finding(case_id, SEVERE, "NONEXISTENT_PRODUCT",
                                f"claim {c.claim_id} product {pid!r} absent from DB")]
            if t == "PRICE":
                got = v.get("price_minor")
                if prod["price_minor"] is None and got is not None:
                    f.append(Finding(case_id, SEVERE, "PRICE_MISMATCH",
                                     f"{pid}: DB price NULL but claim asserts {got}"))
                elif prod["price_minor"] is not None and got != prod["price_minor"]:
                    f.append(Finding(case_id, SEVERE, "PRICE_MISMATCH",
                                     f"{pid}: claim {got} != DB {prod['price_minor']}"))
            elif t == "STOCK":
                if v.get("stock_status") != prod["stock_status"]:
                    f.append(Finding(case_id, SEVERE, "UNSUPPORTED_CLAIM",
                                     f"{pid}: stock {v.get('stock_status')} != DB {prod['stock_status']}"))
            elif t == "RATING":
                if v.get("rating") is None or prod["rating"] is None or float(v["rating"]) != float(prod["rating"]):
                    f.append(Finding(case_id, SEVERE, "UNSUPPORTED_CLAIM",
                                     f"{pid}: rating {v.get('rating')} != DB {prod['rating']}"))
            elif t == "CATEGORY":
                db_cat = (prod["category"] or "").lower()
                if str(v.get("category", "")).lower() != db_cat:
                    f.append(Finding(case_id, SEVERE, "UNSUPPORTED_CLAIM",
                                     f"{pid}: category {v.get('category')} != DB {prod['category']}"))
            elif t == "PRODUCT_SUMMARY":
                if prod["price_minor"] is not None and v.get("price_minor") not in (None, prod["price_minor"]):
                    f.append(Finding(case_id, SEVERE, "PRICE_MISMATCH",
                                     f"{pid}: summary price {v.get('price_minor')} != DB {prod['price_minor']}"))
                if v.get("stock_status") is not None and v.get("stock_status") != prod["stock_status"]:
                    f.append(Finding(case_id, SEVERE, "UNSUPPORTED_CLAIM",
                                     f"{pid}: summary stock {v.get('stock_status')} != DB {prod['stock_status']}"))
            elif t == "ATTRIBUTE":
                basis_value = v.get("basis", "STRUCTURED")
                basis = getattr(basis_value, "value", basis_value)
                key = (v.get("key") or "").lower()
                val = str(v.get("value") or "").lower()
                attrs = self._attrs(pid)
                if basis == "STRUCTURED":
                    if key not in attrs:
                        f.append(Finding(case_id, SEVERE, "UNSUPPORTED_CLAIM",
                                         f"{pid}: no attribute {key!r} in DB"))
                    elif not any(val == a or val in a or a in val for a in attrs[key]):
                        f.append(Finding(case_id, SEVERE, "UNSUPPORTED_CLAIM",
                                         f"{pid}: attribute {key}={v.get('value')} not in DB values {sorted(attrs[key])}"))
                elif basis in {"TITLE_TEXT", "DESCRIPTION_TEXT"}:
                    hay = ((prod["title"] or "") + " " + (prod["description"] or "")).lower()
                    if val and val not in hay:
                        f.append(Finding(case_id, UNVERIFIABLE, "ATTRIBUTE_UNVERIFIABLE",
                                         f"{pid}: text attribute {key}={v.get('value')} not found in title/description"))
                else:
                    f.append(Finding(case_id, SEVERE, "UNSUPPORTED_CLAIM",
                                     f"{pid}: unrecognized attribute basis {basis!r}"))

        elif t == "POLICY_QUOTE":
            sec = v.get("section_id")
            row = self._policy_section(sec) if sec else None
            if row is None:
                f.append(Finding(case_id, ERROR, "POLICY_CITATION_UNRESOLVED",
                                 f"claim {c.claim_id}: policy section {sec!r} absent"))
            else:
                quote_norm = re.sub(r"\s+", " ", str(v.get("quote", ""))).strip()
                # V-08 accepts a verbatim substring of the section body OR its
                # heading (an empty-body parent section is quoted by heading).
                hay = " ".join(
                    re.sub(r"\s+", " ", (row[k] or "")).strip()
                    for k in ("body", "heading")
                )
                if quote_norm and quote_norm not in hay:
                    f.append(Finding(case_id, SEVERE, "UNSUPPORTED_CLAIM",
                                     f"policy quote for section {sec} is not verbatim"))

        elif t == "RESULT_COUNT":
            man = self._resolve_manifest(c.evidence_ids, ev_index)
            if man is None:
                f.append(Finding(case_id, UNVERIFIABLE, "COUNT_UNVERIFIABLE",
                                 f"claim {c.claim_id}: no search manifest to verify count"))
            else:
                if int(v.get("count", -1)) != int(man.exact_match_count):
                    f.append(Finding(case_id, ERROR, "COUNT_MISMATCH",
                                     f"count {v.get('count')} != manifest exact {man.exact_match_count}"))

        elif t == "ABSENCE":
            f += self._audit_absence(c, case_id, ev_index)
        return f

    def _audit_absence(self, c: Any, case_id: str, ev_index: dict) -> list[Finding]:
        # A definitive absence claim requires an EXHAUSTIVE zero-result manifest.
        man = self._resolve_manifest(c.evidence_ids, ev_index)
        if man is None:
            return [Finding(case_id, SEVERE, "UNGROUNDED_ABSENCE",
                            f"absence claim {c.claim_id} has no search manifest evidence")]
        if c.scope == "CATALOGUE":
            if man.completeness != "EXHAUSTIVE":
                return [Finding(case_id, SEVERE, "DEFINITIVE_ABSENCE_INCOMPLETE",
                                "absence asserted over an INCOMPLETE search")]
            if int(getattr(man, "exact_match_count", -1)) != 0:
                return [Finding(case_id, SEVERE, "UNGROUNDED_ABSENCE",
                                "absence asserted but manifest has non-zero exact matches")]
        if c.scope == "GENERAL_POLICY":
            matched = getattr(man, "matched_section_ids", None)
            if matched is not None and len(matched) != 0:
                return [Finding(case_id, SEVERE, "UNGROUNDED_ABSENCE",
                                "policy absence asserted but sections were matched")]
        return []

    # ---- per-card --------------------------------------------------------
    def _audit_card(self, card: Any, ev_index: dict, case_id: str) -> list[Finding]:
        f: list[Finding] = []
        pid = card.product_id
        prod = self._product(pid)
        if prod is None:
            f.append(Finding(case_id, SEVERE, "NONEXISTENT_PRODUCT",
                             f"card product {pid!r} absent from DB"))
        # facts vs DB
        for fact in getattr(card, "facts", None) or []:
            if fact.field == "price" and fact.available:
                if prod is not None and (prod["price_minor"] is None or fact.value != prod["price_minor"]):
                    f.append(Finding(case_id, SEVERE, "PRICE_MISMATCH",
                                     f"card {pid} fact price {fact.value} != DB {prod['price_minor'] if prod else None}"))
            if fact.field == "stock" and fact.available:
                if prod is not None and str(fact.value) != prod["stock_status"]:
                    f.append(Finding(case_id, SEVERE, "UNSUPPORTED_CLAIM",
                                     f"card {pid} fact stock {fact.value} != DB {prod['stock_status']}"))
        # independent hard-filter re-check for search-origin cards
        if card.origin in ("SEARCH_RESULT", "EXISTENCE_MATCH") and card.applied_search_id:
            man = ev_index.get(f"search:{card.applied_search_id}")
            if man is not None and prod is not None:
                f += self._audit_filters(pid, prod, man.search, case_id)
        return f

    def _audit_filters(self, pid: str, prod: sqlite3.Row, man: Any, case_id: str) -> list[Finding]:
        f: list[Finding] = []
        for view in getattr(man, "applied_constraints", []) or []:
            key = view.key
            label = view.label
            val = view.op_to_remove.value if view.op_to_remove else None
            if key == "category" and val is not None:
                if (prod["category_norm"] or "") != str(val).lower():
                    f.append(Finding(case_id, SEVERE, "HARD_FILTER_VIOLATION",
                                     f"{pid}: category {prod['category_norm']} violates filter {label!r}"))
            elif key == "price_max":
                cap = self._first_minor(label, val)
                if cap is not None:
                    if prod["price_minor"] is None:
                        f.append(Finding(case_id, SEVERE, "NULL_PRICE_UNDER_CAP",
                                         f"{pid}: NULL price returned under cap {label!r}"))
                    elif prod["price_minor"] > cap:
                        f.append(Finding(case_id, SEVERE, "HARD_FILTER_VIOLATION",
                                         f"{pid}: price {prod['price_minor']} exceeds cap {cap}"))
            elif key == "price_min":
                floor = self._first_minor(label, val)
                if floor is not None:
                    if prod["price_minor"] is None or prod["price_minor"] < floor:
                        f.append(Finding(case_id, SEVERE, "HARD_FILTER_VIOLATION",
                                         f"{pid}: price {prod['price_minor']} below floor {floor}"))
            elif key == "stock":
                want = "IN_STOCK" if "out of stock" not in label.lower() else "OUT_OF_STOCK"
                if prod["stock_status"] != want:
                    f.append(Finding(case_id, SEVERE, "HARD_FILTER_VIOLATION",
                                     f"{pid}: stock {prod['stock_status']} violates {label!r}"))
            elif key == "rating_min":
                m = _RATING_RE.search(label or "")
                if m:
                    rmin = float(m.group(1))
                    if prod["rating"] is None or float(prod["rating"]) < rmin:
                        f.append(Finding(case_id, SEVERE, "HARD_FILTER_VIOLATION",
                                         f"{pid}: rating {prod['rating']} below min {rmin}"))
            elif key == "attribute" and isinstance(val, dict):
                k = str(val.get("key", "")).lower()
                v = str(val.get("value", "")).lower()
                attrs = self._attrs(pid)
                ok = k in attrs and any(v == a or v in a or a in v for a in attrs[k])
                if not ok:
                    hay = ((prod["title"] or "") + " " + (prod["description"] or "")).lower()
                    if v and v in hay:
                        ok = True
                if not ok:
                    f.append(Finding(case_id, SEVERE, "HARD_FILTER_VIOLATION",
                                     f"{pid}: attribute {k}={v} not satisfied by DB"))
        return f

    def _audit_option(self, opt: Any, ev_index: dict, case_id: str) -> list[Finding]:
        f: list[Finding] = []
        for eid in getattr(opt, "evidence_ids", []) or []:
            if eid not in ev_index:
                f.append(Finding(case_id, ERROR, "CITATION_UNRESOLVED",
                                 f"clarify option {opt.option_id} evidence {eid!r} not in bundle"))
            parsed = self._parse_evidence_id(eid)
            if parsed and parsed[0] == "prod" and self._product(parsed[1]) is None:
                f.append(Finding(case_id, SEVERE, "NONEXISTENT_PRODUCT",
                                 f"clarify option cites product {parsed[1]!r} absent from DB"))
        if getattr(opt, "product_id", None) and self._product(opt.product_id) is None:
            f.append(Finding(case_id, SEVERE, "NONEXISTENT_PRODUCT",
                             f"clarify option product {opt.product_id!r} absent from DB"))
        return f

    # ---- currency / price-hallucination (G-AC-05) ------------------------
    def _audit_currency(self, response: Any, allow: set[int], case_id: str) -> list[Finding]:
        f: list[Finding] = []
        texts = [getattr(response, "answer_text", "") or ""]
        for card in getattr(response, "products", []) or []:
            for fact in card.facts or []:
                texts.append(fact.display or "")
        if getattr(response, "clarification", None):
            for opt in response.clarification.options:
                texts.append(opt.label or "")
        for s in getattr(response, "suggestions", []) or []:
            texts.append(s.label or "")
        for text in texts:
            for m in _CURRENCY_RE.finditer(text or ""):
                minor = _money_to_minor(m.group(1))
                if minor not in allow:
                    f.append(Finding(case_id, SEVERE, "PRICE_HALLUCINATION",
                                     f"currency {m.group(0)!r} is not a cited product price or constraint amount"))
        return f

    def _collect_price_allowlist(self, cited_pids: set, ev_index: dict) -> set[int]:
        allow: set[int] = set()
        for pid in cited_pids:
            prod = self._product(pid)
            if prod and prod["price_minor"] is not None:
                allow.add(int(prod["price_minor"]))
        for ev in ev_index.values():
            man = getattr(ev, "search", None)
            for view in getattr(man, "applied_constraints", []) or []:
                cap = self._first_minor(view.label, view.op_to_remove.value if view.op_to_remove else None)
                if cap is not None:
                    allow.add(cap)
            # V-04: a currency amount is legitimate if it appears verbatim in the
            # cited evidence text (policy excerpt, product display value / excerpt).
            for text in self._evidence_texts(ev):
                for m in _CURRENCY_RE.finditer(text):
                    allow.add(_money_to_minor(m.group(1)))
        return allow

    @staticmethod
    def _evidence_texts(ev: Any) -> list[str]:
        out: list[str] = []
        for attr in ("excerpt", "display_value", "value", "body", "heading"):
            v = getattr(ev, attr, None)
            if isinstance(v, str) and v:
                out.append(v)
        return out

    # ---- helpers ---------------------------------------------------------
    @staticmethod
    def _parse_evidence_id(eid: str) -> tuple[str, str, str | None] | None:
        if eid.startswith("prod:"):
            parts = eid.split(":", 2)
            if len(parts) == 3:
                return ("prod", parts[1], parts[2])
            return None
        if eid.startswith("pol:"):
            return ("pol", eid[4:], None)
        if eid.startswith("search:"):
            return ("search", eid[7:], None)
        return None

    def _resolve_manifest(self, evidence_ids, ev_index: dict) -> Any | None:
        for eid in evidence_ids or []:
            p = self._parse_evidence_id(eid)
            if p and p[0] == "search":
                ev = ev_index.get(eid)
                return getattr(ev, "search", None) if ev is not None else None
        return None

    @staticmethod
    def _first_minor(label: str | None, val: Any) -> int | None:
        if isinstance(val, dict) and "amount_minor" in val:
            return int(val["amount_minor"])
        if label:
            m = _CURRENCY_RE.search(label)
            if m:
                return _money_to_minor(m.group(1))
        return None
