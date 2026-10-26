"""Independent text scanners for the validator (no generation imports)."""
from __future__ import annotations

import re
from typing import Iterable

# Currency amounts like ₹1,299.00 or ₹2000
_CURRENCY_RE = re.compile(r"₹\s*([\d,]+(?:\.\d{1,2})?)")

# Standalone numbers (ints/decimals), excluding those inside currency matches handled separately
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_.-])(\d+(?:\.\d+)?)(?![A-Za-z0-9_.-])")

# Product-ID-like tokens (letters/digits with internal - _ .)
_ID_TOKEN_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+)\b")

# Citation markers produced by the template citation layer
_MARKER_RE = re.compile(r"\s*\[[^\]]*\]")

_STOCK_WORDS_RE = re.compile(
    r"\b(?:in stock|out of stock|sold out|available|unavailable)\b",
    re.IGNORECASE,
)
_RATING_WORDS_RE = re.compile(r"\b(?:rated|rating|ratings)\b", re.IGNORECASE)
_POLICY_WORDS_RE = re.compile(
    r"\b(?:refund|return|shipping|warranty|guarantee|days)\b",
    re.IGNORECASE,
)
_WARRANTY_WORDS_RE = re.compile(r"\b(?:warranty|guarantee)\b", re.IGNORECASE)
_THIS_PRODUCT_RE = re.compile(r"\bthis product (?:has|includes|comes with)\b", re.IGNORECASE)


def strip_citation_markers(text: str) -> str:
    return _MARKER_RE.sub("", text or "").strip()


def format_citation_marker(evidence_ids: Iterable[str], show_search_marker: bool = True) -> str:
    """Local copy of citation marker formatting for V-06 reconstruction checks."""
    tokens: list[str] = []
    seen: set[str] = set()
    for ev_id in evidence_ids:
        if ev_id.startswith("prod:"):
            parts = ev_id.split(":", 2)
            if len(parts) >= 2:
                pid = parts[1]
                if pid not in seen:
                    tokens.append(pid)
                    seen.add(pid)
    for ev_id in evidence_ids:
        if ev_id.startswith("pol:"):
            tok = f"policy:{ev_id[4:]}"
            if tok not in seen:
                tokens.append(tok)
                seen.add(tok)
    if show_search_marker:
        for ev_id in evidence_ids:
            if ev_id.startswith("search:"):
                tok = f"search:{ev_id[7:]}"
                if tok not in seen:
                    tokens.append(tok)
                    seen.add(tok)
    if not tokens:
        return ""
    return " [" + ", ".join(tokens) + "]"


def scan_currency_minors(text: str) -> list[int]:
    """Return currency amounts found in text, normalised to minor units (paise)."""
    minors: list[int] = []
    for m in _CURRENCY_RE.finditer(text or ""):
        raw = m.group(1).replace(",", "")
        try:
            minors.append(int(round(float(raw) * 100)))
        except ValueError:
            continue
    return minors


def scan_numbers(text: str) -> list[str]:
    """Return numeric tokens that are not part of a currency amount."""
    masked = _CURRENCY_RE.sub(" ", text or "")
    return [m.group(1) for m in _NUMBER_RE.finditer(masked)]


def scan_id_tokens(text: str) -> list[str]:
    return [m.group(1) for m in _ID_TOKEN_RE.finditer(text or "")]


def has_stock_words(text: str) -> bool:
    return bool(_STOCK_WORDS_RE.search(text or ""))


def has_rating_words(text: str) -> bool:
    return bool(_RATING_WORDS_RE.search(text or ""))


def has_policy_words(text: str) -> bool:
    return bool(_POLICY_WORDS_RE.search(text or ""))


def has_warranty_words(text: str) -> bool:
    return bool(_WARRANTY_WORDS_RE.search(text or ""))


def has_this_product_has(text: str) -> bool:
    return bool(_THIS_PRODUCT_RE.search(text or ""))


def whitespace_norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


# Fixed non-factual templates (NF-01…NF-13) used by V-06 / V-07.
# Parametric templates are matched by prefix/regex below.
FIXED_NF_TEXTS: frozenset[str] = frozenset({
    "Hello! Ask me about products in the catalogue or about returns, shipping and warranty.",
    "I can look up product prices, stock, ratings and details, search with filters, and quote the supplied policy.",
    "Okay, I've cleared your filters.",
    "Which product do you mean?",
    "What are you looking for? You can mention a category, a price limit, or whether it must be in stock.",
    "Which policy would you like to know about?",
    "I can only help with products in this catalogue and its returns, shipping and warranty policy.",
    "I can't place orders, process payments or track shipments.",
    "I couldn't produce a verified answer to that. Please try rephrasing.",
    "No product is titled this, but a description mentions it:",
    "Do you mean whether it is listed in the catalogue or currently in stock?",
})

NONFACTUAL_ANSWER_KINDS: frozenset[str] = frozenset({
    "Hello! Ask me about products in the catalogue or about returns, shipping and warranty.",
    "I can look up product prices, stock, ratings and details, search with filters, and quote the supplied policy.",
    "Okay, I've cleared your filters.",
})

_PARAMETRIC_NF_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r'^More than one product matches “.+?”\. Which one do you mean\?'),
    re.compile(r"^Those filters can't both apply: .+\. Which one should I keep\?"),
    re.compile(r"^Still applying: .+\."),
)


def match_nf_prefix(text: str) -> int | None:
    """If text begins with an allowlisted NF template, return consumed length."""
    t = text.lstrip()
    for fixed in FIXED_NF_TEXTS:
        if t.startswith(fixed):
            return len(text) - len(t) + len(fixed)
    for pat in _PARAMETRIC_NF_RES:
        m = pat.match(t)
        if m:
            return len(text) - len(t) + m.end()
    return None


def parse_evidence_id(ev_id: str) -> tuple[str, ...] | None:
    """Return ('prod', product_id, field) | ('pol', section_id) | ('search', search_id)."""
    if ev_id.startswith("prod:"):
        parts = ev_id.split(":", 2)
        if len(parts) == 3 and parts[1] and parts[2]:
            return ("prod", parts[1], parts[2])
        return None
    if ev_id.startswith("pol:"):
        sec = ev_id[4:]
        return ("pol", sec) if sec else None
    if ev_id.startswith("search:"):
        sid = ev_id[7:]
        return ("search", sid) if sid else None
    return None
