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

_UNSUPPORTED_ACTION_PATTERNS = (
    ("order", r"\b(?:place\s+(?:an?\s+)?order|order\s+(?:this|these))\b"),
    ("payment", r"\b(?:process\s+(?:a\s+)?payment|pay\s+now|payment\s+gateway)\b"),
    ("cancellation", r"\b(?:cancel\s+(?:(?:my|the|an?)\s+)?order|cancellation\s+of\s+(?:(?:my|the)\s+)?order)\b"),
    ("personalized_advice", r"\bpersonal(?:i[sz]ed|)\s+advice\b"),
)

_OUT_OF_SCOPE_PATTERNS = [
    r"\b(?:buy\s+now|checkout|track\s+(?:my\s+)?(?:order|shipment|package)|weather|recipe|sing\s+a\s+song|tell\s+(?:me\s+)?a\s+joke)\b",
]

# Words that make a question about policy in general (a generic policy question).
_POLICY_TOPIC_WORDS = {"policy", "policies"}
# Function/framing words that name no specific policy subject; used to tell a truly
# generic policy question ("what's your policy?" -> D-06) apart from one that names an
# unestablished subject ("price match policy" -> D-09).
_POLICY_STOPWORDS = {
    "what", "whats", "your", "you", "the", "a", "an", "is", "are", "do", "does",
    "have", "has", "to", "of", "for", "about", "tell", "me", "can", "could",
    "please", "any", "some", "and", "or", "my", "need", "want", "like", "know",
    "got", "store", "general", "overall", "there", "with", "on", "how", "much",
}


def _extract_policy_topic(text: str) -> str | None:
    """Return a named-but-specific policy subject, or None for a generic question.

    A generic 'what's your policy?' yields None (D-06 clarification); a question that
    names a concrete subject the document may not cover ('price match policy') returns
    that subject so the pipeline can reach D-09 rather than fabricate an answer.
    """
    toks = re.findall(r"\b[a-z]+\b", text.lower())
    seen: set[str] = set()
    ordered: list[str] = []
    for t in toks:
        if t in _POLICY_TOPIC_WORDS or t in _POLICY_STOPWORDS or len(t) <= 2:
            continue
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return " ".join(ordered) if ordered else None


@dataclass(frozen=True)
class IntentResult:
    intent: Intent
    requested_fields: tuple[RequestedField, ...]
    policy_topic: str | None
    confidence: float
    unsupported_action: str | None = None


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

    # 2. Explicit unsupported actions precede policy routing. In particular,
    # canceling an order is not a request for the store's cancellation policy.
    for action, pattern in _UNSUPPORTED_ACTION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return IntentResult(Intent.OUT_OF_SCOPE, (), None, 1.0, action)

    # 3. Out of scope
    for pat in _OUT_OF_SCOPE_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return IntentResult(Intent.OUT_OF_SCOPE, (), None, 1.0)

    # 4. Product Warranty vs General Policy
    is_warranty = any(w in words for w in _WARRANTY_KEYWORDS)
    if is_warranty:
        if has_focus or has_product_id or any(kw in text for kw in ["this product", "the shoe", "these shoes", "it", "this"]):
            return IntentResult(Intent.PRODUCT_WARRANTY, (RequestedField.ATTRIBUTE,), "warranty", 0.95)
        # Check if asked as general policy ("what is the warranty policy")
        if any(kw in text for kw in ["policy", "store", "terms", "general"]):
            return IntentResult(Intent.POLICY, (), "warranty", 0.95)

    # 5. Policy (explicit topic keyword OR a generic 'policy'/'policies' mention)
    has_policy_kw = any(kw in text for kw in _POLICY_KEYWORDS)
    has_policy_trigger = bool(_POLICY_TOPIC_WORDS & words)
    if has_policy_kw or has_policy_trigger:
        topic = None
        if any(kw in text for kw in ["return", "refund", "exchange"]):
            topic = "returns"
        elif any(kw in text for kw in ["shipping", "delivery", "dispatch", "cancel", "cancellation"]):
            topic = "shipping"
        elif any(kw in text for kw in ["warranty", "guarantee"]):
            topic = "warranty"
        elif "privacy" in text:
            topic = "privacy"
        elif "payment" in text:
            topic = "payment"
        else:
            # Named subject (D-09 candidate) or generic (D-06 when None).
            topic = _extract_policy_topic(text)
        return IntentResult(Intent.POLICY, (), topic, 0.95)

    # 6. Requested fields detection
    req_fields = []
    if "price" in words or "cost" in words or "daam" in words or "how much" in text:
        req_fields.append(RequestedField.PRICE)
    if "stock" in words or "in stock" in text:
        req_fields.append(RequestedField.STOCK)
    if "rating" in words or "rated" in words or "reviews" in words or "stars" in words:
        req_fields.append(RequestedField.RATING)
    if "category" in words:
        req_fields.append(RequestedField.CATEGORY)
    if "description" in words or "details" in words or "features" in words:
        req_fields.append(RequestedField.DESCRIPTION)

    # 7. Product fact vs existence vs search
    if has_product_id or (has_focus and req_fields):
        return IntentResult(Intent.PRODUCT_FACT, tuple(req_fields), None, 0.95)

    for pat in _EXISTENCE_PATTERNS:
        if pat.search(text):
            return IntentResult(Intent.EXISTENCE, tuple(req_fields), None, 0.9)

    # Leave unclassified text explicit. parse_query applies the documented
    # UNKNOWN -> SEARCH/CLARIFY rule after extracting content terms/constraints.
    return IntentResult(Intent.UNKNOWN, tuple(req_fields), None, 0.50)
