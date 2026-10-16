import re
from typing import Any
from app.domain.constraints import AttrConstraint, ConstraintOp, ConstraintSet, PriceBound
from app.domain.enums import Intent, OpKind, StockStatus
from app.domain.parsed_query import ParsedQuery
from app.nlp.intent import classify_intent
from app.nlp.normalizer import normalize
from app.nlp.price_parser import parse_price_phrases
from app.settings import Settings
from app.store.product_repo import CatalogueVocab, ProductRepo

_FOCUS_PRONOUNS = {"it", "this", "that", "the product", "this product", "these", "the item"}

_KNOWN_COLORS = {"black", "white", "blue", "red", "grey", "gray", "brown", "navy", "green", "olive", "silver"}
_KNOWN_MATERIALS = {"cotton", "leather", "faux leather", "mesh", "canvas", "knit", "denim", "linen", "polyester", "ceramic", "stainless steel"}


def parse_query(
    message: str,
    view_has_focus: bool,
    vocab: CatalogueVocab,
    settings: Settings,
    product_repo: ProductRepo | None = None,
) -> ParsedQuery:
    nt = normalize(message, settings.codemix)
    text_low = nt.text.lower()
    words = [w.strip(".,;:?!'\"") for w in text_low.split() if w.strip(".,;:?!'\"")]

    # 1. Detect explicit product IDs
    explicit_ids = []
    if product_repo:
        for w in words:
            # check if word is an existing product ID in repo
            p = product_repo.get(w.upper()) or product_repo.get(w)
            if p:
                explicit_ids.append(p.product_id)
    # Also check regex pattern for IDs like SH-BLK-001 or P102
    for m in re.finditer(r"\b([A-Z0-9]{2,}-[A-Z0-9]+(?:-[A-Z0-9]+)*|P\d{2,})\b", nt.text, re.IGNORECASE):
        tok = m.group(1).upper()
        if tok not in explicit_ids:
            explicit_ids.append(tok)

    # 2. Check focus reference
    # Matches genuine anaphoric references like "is it in stock", "price of this product", etc.
    # Avoids false positives from relative pronouns like "products that are..."
    refs_focus = bool(re.search(r"\b(?:it|this product|that product|the product|this one|that one|the item)\b", text_low))

    # 3. Classify intent
    intent_res = classify_intent(
        nt,
        settings.intent_cues,
        has_focus=view_has_focus or refs_focus,
        has_product_id=len(explicit_ids) > 0,
    )

    ops: list[ConstraintOp] = []

    # 4. Parse prices
    price_ops = parse_price_phrases(nt.text, settings.thresholds.price_under_is_inclusive)
    ops.extend(price_ops)

    # 5. Parse stock
    if "in stock" in text_low or "available" in text_low or "in-stock" in text_low:
        ops.append(ConstraintOp(op=OpKind.SET, key="stock", value="IN_STOCK", origin="USER_TEXT"))
    elif "out of stock" in text_low or "sold out" in text_low:
        ops.append(ConstraintOp(op=OpKind.SET, key="stock", value="OUT_OF_STOCK", origin="USER_TEXT"))

    # 6. Parse category from category_aliases
    matched_cats = set()
    for alias, canon in settings.category_aliases.items():
        # Match whole word
        if re.search(r"\b" + re.escape(alias) + r"\b", text_low):
            matched_cats.add(canon.lower())
    for cat in matched_cats:
        ops.append(ConstraintOp(op=OpKind.ADD, key="category", value=cat, origin="USER_TEXT"))

    # 7. Parse attribute cues (color, material, warranty)
    attrs_this_turn = []
    for c in _KNOWN_COLORS:
        if re.search(r"\b" + re.escape(c) + r"\b", text_low):
            op = ConstraintOp(op=OpKind.ADD, key="attribute", value={"key": "color", "value": c}, origin="USER_TEXT")
            ops.append(op)
            attrs_this_turn.append(AttrConstraint(key="color", value=c))

    for m in _KNOWN_MATERIALS:
        if re.search(r"\b" + re.escape(m) + r"\b", text_low):
            op = ConstraintOp(op=OpKind.ADD, key="attribute", value={"key": "material", "value": m}, origin="USER_TEXT")
            ops.append(op)
            attrs_this_turn.append(AttrConstraint(key="material", value=m))

    # 8. Extract search terms (content words)
    stop_words = set(settings.stopwords) | {"show", "shoes", "shoe", "black", "under", "price", "stock", "find", "only", "products", "ones"}
    # Add matched category aliases, colors, and materials to stopwords so they are not treated as dangling search terms
    for a in settings.category_aliases.keys():
        stop_words.add(a.lower())
    for c in _KNOWN_COLORS:
        stop_words.add(c.lower())
    for m in _KNOWN_MATERIALS:
        stop_words.add(m.lower())

    entity_terms = []
    for w in words:
        clean_w = re.sub(r"[^\w]", "", w)
        if len(clean_w) > 2 and clean_w not in stop_words and not clean_w.isdigit():
            entity_terms.append(clean_w)

    # Build this_turn_constraints
    price_min = None
    price_max = None
    stock_val = None
    for op in ops:
        if op.key == "price_min" and isinstance(op.value, dict):
            price_min = PriceBound(amount_minor=op.value["amount_minor"], inclusive=op.value.get("inclusive", False))
        elif op.key == "price_max" and isinstance(op.value, dict):
            price_max = PriceBound(amount_minor=op.value["amount_minor"], inclusive=op.value.get("inclusive", False))
        elif op.key == "stock":
            stock_val = StockStatus(op.value)

    this_turn_cs = ConstraintSet(
        categories=frozenset(matched_cats),
        price_min=price_min,
        price_max=price_max,
        stock=stock_val,
        attributes=tuple(attrs_this_turn),
        terms=tuple(entity_terms),
    )

    return ParsedQuery(
        original=message,
        normalized=nt,
        intent=intent_res.intent,
        requested_fields=intent_res.requested_fields,
        product_ids=tuple(explicit_ids),
        entity_terms=tuple(entity_terms),
        references_focus=refs_focus,
        policy_topic=intent_res.policy_topic,
        constraint_ops=tuple(ops),
        this_turn_constraints=this_turn_cs,
        confidence=intent_res.confidence,
    )
