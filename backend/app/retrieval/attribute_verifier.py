import re
from dataclasses import dataclass
from typing import Any, Sequence
from app.domain.constraints import AttrConstraint
from app.domain.enums import AttrBasis
from app.domain.plans import AttrMatch
from app.store.product_repo import Product

_NEGATIONS = {"no", "not", "non", "without", "free", "neither"}


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    matches: tuple[AttrMatch, ...] = ()


def _is_negated(text: str, match_start: int) -> bool:
    prefix = text[:match_start].lower()
    words = prefix.split()
    last_three = words[-3:] if len(words) >= 3 else words
    return any(neg in last_three for neg in _NEGATIONS)


def verify_attributes(
    product: Product,
    product_attrs: Sequence[dict[str, Any]],
    wanted: Sequence[AttrConstraint],
    allow_description_text: bool = True,
) -> VerifyResult:
    if not wanted:
        return VerifyResult(ok=True, matches=())

    matches: list[AttrMatch] = []

    for c in wanted:
        val_norm = c.value.lower().strip()
        matched = False

        # 1. Check structured attributes
        for a in product_attrs:
            key_match = (c.key is None) or (a["key_norm"] == c.key.lower().strip())
            if key_match and a["value_norm"] == val_norm:
                matches.append(AttrMatch(
                    constraint=c,
                    basis=AttrBasis.STRUCTURED,
                    evidence_field=f"attr.{a['key_norm']}",
                    matched_text=a["value_raw"],
                ))
                matched = True
                break

        if matched:
            continue

        # 2. Check title text
        t_low = product.title.lower()
        idx_t = t_low.find(val_norm)
        if idx_t != -1 and not _is_negated(t_low, idx_t):
            matched_span = product.title[idx_t : idx_t + len(val_norm)]
            matches.append(AttrMatch(
                constraint=c,
                basis=AttrBasis.TITLE_TEXT,
                evidence_field="title",
                matched_text=matched_span,
            ))
            matched = True
            continue

        # 3. Check description text
        if allow_description_text and product.description:
            d_low = product.description.lower()
            # Whole word boundary search
            pattern = re.compile(r"\b" + re.escape(val_norm) + r"\b", re.IGNORECASE)
            m = pattern.search(product.description)
            if m and not _is_negated(d_low, m.start()):
                matches.append(AttrMatch(
                    constraint=c,
                    basis=AttrBasis.DESCRIPTION_TEXT,
                    evidence_field="description",
                    matched_text=m.group(0),
                ))
                matched = True
                continue

        # If any constraint could not be verified, verification fails!
        return VerifyResult(ok=False, matches=())

    return VerifyResult(ok=True, matches=tuple(matches))
