"""Deterministic catalogue-vocabulary spelling correction."""
from __future__ import annotations

from difflib import SequenceMatcher
import re

from app.domain.parsed_query import TraceItem
from app.store.product_repo import CatalogueVocab

_PRODUCT_ID = re.compile(r"^[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+$")
_TOKEN = re.compile(r"^([^A-Za-z]*)([A-Za-z]+)([^A-Za-z]*)$")


def _vocabulary_tokens(vocab: CatalogueVocab) -> tuple[str, ...]:
    values: set[str] = set()
    for value in vocab.categories | vocab.title_tokens:
        values.update(re.findall(r"[a-z]+", value.casefold()))
    for attr_values in vocab.attr_values_by_key.values():
        for value in attr_values:
            values.update(re.findall(r"[a-z]+", value.casefold()))
    return tuple(sorted(values))


def spellfix(
    text: str,
    vocab: CatalogueVocab,
    ratio_threshold: float = 0.85,
) -> tuple[str, tuple[TraceItem, ...]]:
    """Correct only OOV alphabetic tokens with one unique best catalogue match.

    Product IDs, numeric tokens, exact vocabulary terms, and ambiguous candidates
    are left untouched. Whitespace and surrounding punctuation are preserved.
    """
    candidates = _vocabulary_tokens(vocab)
    candidate_set = set(candidates)
    if not candidates:
        return text, ()

    trace: list[TraceItem] = []
    output: list[str] = []
    for token in text.split():
        match = _TOKEN.fullmatch(token)
        if not match:
            output.append(token)
            continue
        prefix, word, suffix = match.groups()
        lower = word.casefold()
        if len(word) < 5 or _PRODUCT_ID.fullmatch(token) or any(ch.isdigit() for ch in token):
            output.append(token)
            continue
        if vocab.has_term(lower) or lower in candidate_set:
            output.append(token)
            continue

        scores = [
            (SequenceMatcher(None, lower, candidate, autojunk=False).ratio(), candidate)
            for candidate in candidates
        ]
        best_score = max(score for score, _ in scores)
        best = sorted({candidate for score, candidate in scores if score == best_score})
        if best_score < ratio_threshold or len(best) != 1:
            output.append(token)
            continue

        correction = best[0]
        if word.isupper():
            correction = correction.upper()
        elif word.istitle():
            correction = correction.title()
        corrected = f"{prefix}{correction}{suffix}"
        output.append(corrected)
        trace.append(TraceItem("SPELLFIX", token, corrected))

    return " ".join(output), tuple(trace)
