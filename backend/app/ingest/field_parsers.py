import re
from decimal import Decimal, InvalidOperation
from typing import Any
from app.domain.enums import StockStatus

_NUM_UNIT_REGEX = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([A-Za-z\"%]+)?\s*$")


def parse_price(raw: Any, cfg: dict[str, Any] | None = None) -> tuple[int | None, str | None]:
    if raw is None or raw == "":
        return None, "PRICE_MISSING"

    cfg = cfg or {}
    strip_tokens = cfg.get("strip_tokens", ["₹", "Rs.", "Rs", "INR"])
    allow_zero = cfg.get("allow_zero_price", False)

    val_str = str(raw).strip()
    for tok in strip_tokens:
        val_str = val_str.replace(tok, "")
    val_str = val_str.replace(",", "").strip()

    try:
        dec = Decimal(val_str)
        # Check fractional digits
        exp = dec.as_tuple().exponent
        if isinstance(exp, int) and exp < -2:
            return None, "PRICE_UNPARSEABLE"

        minor = int(dec * 100)
        if minor < 0:
            return None, "PRICE_NEGATIVE"
        if minor == 0 and not allow_zero:
            return None, "PRICE_ZERO"
        return minor, None
    except (InvalidOperation, ValueError):
        return None, "PRICE_UNPARSEABLE"


def parse_rating(raw: Any, min_r: float = 0.0, max_r: float = 5.0) -> tuple[float | None, str | None]:
    if raw is None or raw == "":
        return None, "RATING_MISSING"
    try:
        r = float(raw)
        if r < min_r or r > max_r:
            return None, "RATING_OUT_OF_RANGE"
        return round(r, 2), None
    except (ValueError, TypeError):
        return None, "RATING_UNPARSEABLE"


def parse_stock(raw: Any, value_map: dict[str, str] | None = None) -> tuple[StockStatus, str | None]:
    if raw is None or raw == "":
        return StockStatus.UNKNOWN, "STOCK_MISSING"

    v = str(raw).strip().lower().replace(" ", "_")
    if value_map:
        mapped = value_map.get(v)
        if mapped:
            try:
                return StockStatus(mapped), None
            except ValueError:
                pass

    if v in ("in_stock", "available", "instock", "yes", "true", "1"):
        return StockStatus.IN_STOCK, None
    elif v in ("out_of_stock", "sold_out", "nostock", "no", "false", "0"):
        return StockStatus.OUT_OF_STOCK, None
    elif v in ("unknown", "unspecified"):
        return StockStatus.UNKNOWN, None

    return StockStatus.UNKNOWN, "STOCK_UNRECOGNIZED"


def normalize_attr_key(key: str, synonyms: dict[str, str] | None = None) -> str:
    k = key.strip().lower()
    k = re.sub(r"[\s\-_]+", "_", k)
    if synonyms and k in synonyms:
        k = synonyms[k]
    return k


def parse_attributes(raw: Any, synonyms: dict[str, str] | None = None) -> list[dict[str, Any]]:
    if not raw:
        return []

    results = []
    if isinstance(raw, dict):
        items = raw.items()
    elif isinstance(raw, list):
        items = []
        for item in raw:
            if isinstance(item, dict):
                items.extend(item.items())
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                items.append((item[0], item[1]))
    else:
        return []

    for k_raw, v_raw in items:
        if k_raw is None or v_raw is None:
            continue
        k_str = str(k_raw).strip()
        v_str = str(v_raw).strip()
        if not k_str or not v_str:
            continue

        k_norm = normalize_attr_key(k_str, synonyms)
        v_norm = re.sub(r"\s+", " ", v_str.strip().lower())

        val_num = None
        unit = None
        m = _NUM_UNIT_REGEX.match(v_norm)
        if m:
            try:
                val_num = float(m.group(1))
                unit = m.group(2)
            except ValueError:
                pass

        results.append({
            "key_raw": k_str,
            "key_norm": k_norm,
            "value_raw": v_str,
            "value_norm": v_norm,
            "value_num": val_num,
            "unit": unit,
        })

    return results
