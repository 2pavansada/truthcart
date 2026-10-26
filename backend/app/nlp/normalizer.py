import re
import unicodedata
from app.domain.parsed_query import NormalizedText, TraceItem
from app.nlp.spellfix import spellfix
from app.store.product_repo import CatalogueVocab

_NUMBER_EXPANSIONS = [
    (re.compile(r"(\d+(?:\.\d+)?)\s*k\b", re.IGNORECASE), lambda m: str(int(float(m.group(1)) * 1000))),
    (re.compile(r"(\d+(?:\.\d+)?)\s*lakh\b", re.IGNORECASE), lambda m: str(int(float(m.group(1)) * 100000))),
]

_CURRENCY_PATTERNS = [
    (re.compile(r"(?:rs\.?|inr|rupees?)\s*", re.IGNORECASE), "₹"),
]


def _sanitized_original(text: str) -> str:
    """Return a safe fallback without depending on the failing transforms."""
    without_controls = "".join(
        char for char in text
        if unicodedata.category(char) != "Cc" or char in "\t\n\r"
    )
    return re.sub(r"\s+", " ", without_controls).strip()


def normalize(
    text: str,
    codemix_lexicon: dict[str, str] | None = None,
    vocab: CatalogueVocab | None = None,
    spell_ratio: float = 0.85,
) -> NormalizedText:
    if not text:
        return NormalizedText(original="", text="", trace=())

    original = text
    trace = []

    try:
        # 1. NFKC normalization
        nfkc_text = unicodedata.normalize("NFKC", text)
        if nfkc_text != text:
            trace.append(TraceItem("NFKC", text, nfkc_text))
        current = nfkc_text

        # 2. Currency standardization
        for pat, repl in _CURRENCY_PATTERNS:
            sub = pat.sub(
                lambda m: m.group(0) if m.start() and (current[m.start() - 1].isalnum() or current[m.start() - 1] == "_") else repl,
                current,
            )
            if sub != current:
                trace.append(TraceItem("CURRENCY", current, sub))
                current = sub

        # 3. Number abbreviations (e.g. 2k -> 2000)
        for pat, func in _NUMBER_EXPANSIONS:
            sub = pat.sub(func, current)
            if sub != current:
                trace.append(TraceItem("NUMBER_EXPAND", current, sub))
                current = sub

        # 4. Code-mix token normalization
        if codemix_lexicon:
            tokens = current.split()
            new_tokens = []
            changed = False
            for tok in tokens:
                # strip punctuation for lookup
                clean = tok.strip(".,?!;:-").lower()
                if clean in codemix_lexicon:
                    repl = codemix_lexicon[clean]
                    new_tokens.append(repl)
                    changed = True
                else:
                    new_tokens.append(tok)
            if changed:
                sub = " ".join(new_tokens)
                trace.append(TraceItem("CODEMIX", current, sub))
                current = sub

        if vocab is not None:
            corrected, spelling_trace = spellfix(current, vocab, spell_ratio)
            if spelling_trace:
                current = corrected
                trace.extend(spelling_trace)

        # 5. Collapse spaces
        collapsed = re.sub(r"\s+", " ", current).strip()
        if collapsed != current:
            trace.append(TraceItem("WHITESPACE", current, collapsed))
            current = collapsed

        return NormalizedText(original=original, text=current, trace=tuple(trace))
    except Exception:
        # The contract makes normalization best-effort. Rebuild from the raw
        # message so no partially transformed text escapes on a failed step.
        fallback = _sanitized_original(original)
        skipped = TraceItem("NORMALIZE_SKIPPED", "", fallback)
        return NormalizedText(original=original, text=fallback, trace=(skipped,))
