import re
from dataclasses import dataclass
from app.domain.enums import Intent, RequestedField
from app.domain.parsed_query import NormalizedText

_EXISTENCE_PATTERNS = [
    re.compile(r"^(?:do\s+you\s+(?:have|sell|carry)|is\s+there|are\s+there)\b", re.IGNORECASE),
]

_POLICY_KEYWORDS = [
    "return", "refund", "exchange", "shipping", "delivery", "dispatch",
    "cancel", "cancellation", "store policy", "privacy", "payment policy"
]

_WARRANTY_KEYWORDS = ["warranty", "guarantee"]

_OUT_OF_SCOPE_PATTERNS = [
    r"\b(?:place\s+order|buy\s+now|checkout|pay\s+now|payment\s+gateway|track\s+(?:my\s+)?(?:order|shipment|package)|weather|recipe|sing\s+a\s+song|tell\s+a\s+joke)\b",
]


@dataclass(frozen=True)
class IntentResult:
    intent: Intent
    requested_fields: tuple[RequestedField, ...]
    policy_topic: str | None
    confidence: float


def classify_intent(
    nt: NormalizedText,
    intent_cues: dict[str, list[str]] | None = None,
    has_focus: bool = False,
    has_product_id: bool = False,
) -> IntentResult:
    text = nt.text.lower().strip()
    words = set(re.findall(r"\b\w+\b", text))

    # 1. Non-factual: Greeting / Help / Reset
    cues = intent_cues or {}
    greetings = cues.get("greeting", ["hi", "hello", "hey", "help"])
    resets = cues.get("reset", ["reset", "clear", "clear filters", "start over"])
    out_of_scope = cues.get("out_of_scope", ["track", "order", "checkout", "pay"])

    if text in greetings or any(text.startswith(g) and len(text.split()) <= 3 for g in greetings):
        return IntentResult(Intent.GREETING_HELP, (), None, 1.0)

    if text in resets:
        return IntentResult(Intent.RESET, (), None, 1.0)

    # 2. Out of scope
    for pat in _OUT_OF_SCOPE_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return IntentResult(Intent.OUT_OF_SCOPE, (), None, 1.0)

    # 3. Product Warranty vs General Policy
    is_warranty = any(w in words for w in _WARRANTY_KEYWORDS)
    if is_warranty:
        if has_focus or has_product_id or any(kw in text for kw in ["this product", "the shoe", "these shoes", "it", "this"]):
            return IntentResult(Intent.PRODUCT_WARRANTY, (RequestedField.ATTRIBUTE,), "warranty", 0.95)
        # Check if asked as general policy ("what is the warranty policy")
        if any(kw in text for kw in ["policy", "store", "terms", "general"]):
            return IntentResult(Intent.POLICY, (), "warranty", 0.95)

    # 4. Policy
    is_policy = any(kw in text for kw in _POLICY_KEYWORDS)
    if is_policy:
        topic = None
        if any(kw in text for kw in ["return", "refund", "exchange"]):
            topic = "returns"
        elif any(kw in text for kw in ["shipping", "delivery", "dispatch", "cancel"]):
            topic = "shipping"
        elif any(kw in text for kw in ["warranty", "guarantee"]):
            topic = "warranty"
        return IntentResult(Intent.POLICY, (), topic, 0.95)

    # 5. Requested fields detection
    req_fields = []
    if "price" in words or "cost" in words or "daam" in words or "how much" in text:
        req_fields.append(RequestedField.PRICE)
    if "stock" in words or "available" in words or "in stock" in text:
        req_fields.append(RequestedField.STOCK)
    if "rating" in words or "rated" in words or "reviews" in words or "stars" in words:
        req_fields.append(RequestedField.RATING)
    if "category" in words:
        req_fields.append(RequestedField.CATEGORY)
    if "description" in words or "details" in words or "features" in words:
        req_fields.append(RequestedField.DESCRIPTION)

    # 6. Product fact vs existence vs search
    if has_product_id or (has_focus and req_fields):
        return IntentResult(Intent.PRODUCT_FACT, tuple(req_fields), None, 0.95)

    for pat in _EXISTENCE_PATTERNS:
        if pat.search(text):
            return IntentResult(Intent.EXISTENCE, tuple(req_fields), None, 0.9)

    # Default to SEARCH
    return IntentResult(Intent.SEARCH, tuple(req_fields), None, 0.85)
