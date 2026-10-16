import re

SUSPICIOUS_PATTERNS = [
    r"ignore\s+(?:all\s+)?(?:previous|above)\s+instructions",
    r"disregard\s+(?:all\s+)?(?:previous|above)\s+instructions",
    r"system\s*prompt",
    r"you\s+are\s+now",
    r"role\s*:",
    r"assistant\s*:",
    r"<\s*script",
    r"act\s+as\s+(?:an?\s+)?(?:ai|assistant|dan|admin|hacker)",
    r"reveal\s+(?:your\s+)?prompt",
]

_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in SUSPICIOUS_PATTERNS]


def is_suspicious(text: str | None) -> bool:
    if not text:
        return False
    return any(p.search(text) is not None for p in _COMPILED_PATTERNS)


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    # remove control characters except \n, \r, \t
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return cleaned.strip()
