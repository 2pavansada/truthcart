import re
from app.domain.constraints import ConstraintOp
from app.domain.enums import OpKind

_NUM_PATTERN = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"

_BETWEEN_REGEX = re.compile(
    rf"(?:between|from)\s+₹?\s*{_NUM_PATTERN}\s*(?:and|to|-)\s*₹?\s*{_NUM_PATTERN}",
    re.IGNORECASE,
)

_MAX_REGEX = re.compile(
    rf"(?:under|below|less\s+than|cheaper\s+than|upto|up\s+to|within|max(?:imum)?)\s+₹?\s*{_NUM_PATTERN}",
    re.IGNORECASE,
)

_MIN_REGEX = re.compile(
    rf"(?:above|over|more\s+than|greater\s+than|at\s+least|min(?:imum)?)\s+₹?\s*{_NUM_PATTERN}",
    re.IGNORECASE,
)


def _to_minor(val_str: str) -> int:
    clean = val_str.replace(",", "").strip()
    return int(float(clean) * 100)


def parse_price_phrases(text: str, under_is_inclusive: bool = False) -> list[ConstraintOp]:
    ops: list[ConstraintOp] = []

    # 1. Between X and Y
    m_between = _BETWEEN_REGEX.search(text)
    if m_between:
        low = _to_minor(m_between.group(1))
        high = _to_minor(m_between.group(2))
        ops.append(ConstraintOp(
            op=OpKind.SET,
            key="price_min",
            value={"amount_minor": low, "inclusive": True},
            origin="USER_TEXT",
        ))
        ops.append(ConstraintOp(
            op=OpKind.SET,
            key="price_max",
            value={"amount_minor": high, "inclusive": True},
            origin="USER_TEXT",
        ))
        return ops

    # 2. Upper bound (under ₹2,000)
    m_max = _MAX_REGEX.search(text)
    if m_max:
        amount = _to_minor(m_max.group(1))
        ops.append(ConstraintOp(
            op=OpKind.SET,
            key="price_max",
            value={"amount_minor": amount, "inclusive": under_is_inclusive},
            origin="USER_TEXT",
        ))

    # 3. Lower bound (above ₹1,000)
    m_min = _MIN_REGEX.search(text)
    if m_min:
        amount = _to_minor(m_min.group(1))
        ops.append(ConstraintOp(
            op=OpKind.SET,
            key="price_min",
            value={"amount_minor": amount, "inclusive": False},
            origin="USER_TEXT",
        ))

    return ops
