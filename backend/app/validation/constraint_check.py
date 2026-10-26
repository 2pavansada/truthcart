"""Independent hard-filter re-evaluation for V-10 (no retrieval/generation imports)."""
from __future__ import annotations

from typing import Any, Sequence

from app.validation.scanners import scan_currency_minors


def _amount_from_view(cv: Any) -> tuple[int | None, bool]:
    """Return (amount_minor, inclusive) from a ConstraintView, falling back to the label."""
    op = getattr(cv, "op_to_remove", None)
    val = getattr(op, "value", None) if op is not None else None
    inclusive = False
    amount: int | None = None
    if isinstance(val, dict) and val.get("amount_minor") is not None:
        try:
            amount = int(val["amount_minor"])
            inclusive = bool(val.get("inclusive", False))
        except (TypeError, ValueError):
            amount = None
    if amount is None:
        minors = scan_currency_minors(getattr(cv, "label", "") or "")
        if minors:
            amount = minors[0]
            # Labels are written as "Price under ₹X" (exclusive) or "Price above ₹X".
            inclusive = "inclusive" in (getattr(cv, "label", "") or "").lower()
    return amount, inclusive


def satisfies(product: Any, attrs: Sequence[Any], applied: Sequence[Any], cfg: Any = None) -> bool:
    """True iff the product satisfies every applied hard constraint (category/price/stock/rating)."""
    _ = cfg
    for cv in applied:
        if not constraint_ok(product, attrs, cv):
            return False
    return True


def constraint_ok(product: Any, attrs: Sequence[Any], cv: Any) -> bool:
    key = getattr(cv, "key", "")
    label = (getattr(cv, "label", "") or "").lower()
    op = getattr(cv, "op_to_remove", None)
    val = getattr(op, "value", None) if op is not None else None

    if key == "price_max":
        amount, inclusive = _amount_from_view(cv)
        if amount is None:
            return True
        if product.price_minor is None:
            return False
        return product.price_minor <= amount if inclusive else product.price_minor < amount

    if key == "price_min":
        amount, inclusive = _amount_from_view(cv)
        if amount is None:
            return True
        if product.price_minor is None:
            return False
        return product.price_minor >= amount if inclusive else product.price_minor > amount

    if key == "stock":
        if "in stock" in label:
            return getattr(product.stock_status, "value", product.stock_status) == "IN_STOCK"
        if "out of stock" in label:
            return getattr(product.stock_status, "value", product.stock_status) == "OUT_OF_STOCK"
        return True

    if key == "rating_min":
        amin: float | None = None
        try:
            if val is not None:
                amin = float(val)
        except (TypeError, ValueError):
            amin = None
        if amin is None:
            return True
        return product.rating is not None and product.rating >= amin

    if key == "category" and val:
        want = str(val).lower().strip()
        got = (getattr(product, "category_norm", None) or "").lower().strip()
        if want and got:
            return want == got
        return True

    if key == "attribute" and isinstance(val, dict):
        want_key = val.get("key")
        want_val = str(val.get("value", "")).lower().strip()
        for a in attrs:
            key_norm = str(a.get("key_norm") or "")
            val_norm = str(a.get("value_norm") or a.get("value_raw") or "").lower().strip()
            if want_val and want_val == val_norm and (want_key is None or key_norm == want_key):
                return True
        return False

    return True
