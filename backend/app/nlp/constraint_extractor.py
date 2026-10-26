import re
from dataclasses import replace
from typing import Any
from app.domain.constraints import AttrConstraint, ConstraintOp, ConstraintSet, PriceBound
from app.domain.enums import Intent, OpKind, StockStatus
from app.domain.parsed_query import ParsedQuery, TraceItem
from app.nlp.intent import classify_intent
from app.nlp.normalizer import normalize
from app.nlp.price_parser import parse_price_phrases
from app.settings import Settings
from app.store.product_repo import CatalogueVocab, ProductRepo

_FOCUS_PRONOUNS = {"it", "this", "that", "the product", "this product", "these", "the item"}

_KNOWN_COLORS = {"black", "white", "blue", "red", "grey", "gray", "brown", "navy", "green", "olive", "silver"}
_KNOWN_MATERIALS = {"cotton", "leather", "faux leather", "mesh", "canvas", "knit", "denim", "linen", "polyester", "ceramic", "stainless steel"}
_ADDITIVE_CATEGORY_CUES = re.compile(
    r"\b(?:in\s+addition\s+to|along\s+with|as\s+well\s+as|also|add|include|plus)\b",
    re.IGNORECASE,
)
_CATEGORY_NEGATION = re.compile(
    r"(?:\bno\s*,?\s+|\bnot\s+|\bwithout\s+|\bexcluding\s+|\bexclude\s+|\bexcept\s+|"
    r"\bnever\s+|\bdon['’]t\s+(?:want|include|show)\s+|\bdo\s+not\s+(?:want|include|show)\s+)"
    r"(?:the\s+)?$",
    re.IGNORECASE,
)
_REPLACE_SOURCE = re.compile(r"\breplace\s+(.+?)\s+with\s+", re.IGNORECASE)
_CATEGORY_CORRECTION = re.compile(r"\bno\s*,?\s+i\s+meant\b", re.IGNORECASE)
_CATEGORY_CUE_BRIDGE_WORDS = {
    "show", "find", "get", "want", "need", "include", "add", "some", "me",
    "a", "the", "more", "products", "options", "category", "categories", "item",
    "please", "to",
}
_APPROXIMATE_PRICE_CUE = re.compile(
    r"\b(?:around|about)\s+₹?\s*\d+(?:,\d{3})*(?:\.\d+)?\b",
    re.IGNORECASE,
)


def _parse_categories(text: str, aliases: dict[str, str]) -> tuple[set[str], bool, bool]:
    """Return positive categories, explicit additive intent, and ambiguity.

    Category values in one message are alternatives (OR). Negated categories are
    never added; unsupported exclusion wording is marked ambiguous for clarification.
    """
    mentions: list[tuple[int, int, str, bool]] = []
    for alias, canonical in aliases.items():
        pattern = re.compile(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", re.IGNORECASE)
        for match in pattern.finditer(text):
            prefix_start = max(0, match.start() - 48)
            prefix = text[prefix_start:match.start()]
            mentions.append((
                match.start(), match.end(), canonical.casefold(),
                bool(_CATEGORY_NEGATION.search(prefix)),
            ))

    # Aliases can overlap (e.g. singular and plural or a category alias embedded in
    # another); retain the longest phrase at a given span.
    mentions.sort(key=lambda item: (item[0], -(item[1] - item[0]), item[2]))
    non_overlapping: list[tuple[int, int, str, bool]] = []
    for mention in mentions:
        if non_overlapping and mention[0] < non_overlapping[-1][1]:
            continue
        non_overlapping.append(mention)

    replace_sources = [m.span(1) for m in _REPLACE_SOURCE.finditer(text)]
    corrected = bool(_CATEGORY_CORRECTION.search(text))
    filtered: list[tuple[int, int, str, bool]] = []
    for start, end, category, negated in non_overlapping:
        if any(lo <= start and end <= hi for lo, hi in replace_sources):
            continue
        # "No, I meant Y" is a correction lead-in, not negation of Y.
        if corrected and negated:
            negated = False
        filtered.append((start, end, category, negated))

    positive = [(start, end, category) for start, end, category, negated in filtered if not negated]
    negatives = [(start, end) for start, end, _, negated in filtered if negated]
    categories = {category for _, _, category in positive}

    # The system has positive category filters, not category exclusions. A negated
    # category request therefore needs clarification instead of being silently ignored.
    ambiguous = bool(negatives)
    additive = False
    for cue in _ADDITIVE_CATEGORY_CUES.finditer(text):
        for start, end, _ in positive:
            if cue.end() <= start:
                between = text[cue.end():start]
                words_between = re.findall(r"[\w’']+", between)
                if (
                    not re.search(r"[.!?;]", between)
                    and len(words_between) <= 5
                    and set(words_between) <= (_CATEGORY_CUE_BRIDGE_WORDS | _KNOWN_COLORS | _KNOWN_MATERIALS)
                ):
                    additive = True
                    break
            elif end <= cue.start():
                between = text[end:cue.start()]
                words_between = re.findall(r"[\w’']+", between)
                # A cue between category mentions expresses adding a category;
                # a trailing "also" after the only mention is not enough context.
                if (
                    positive
                    and any(prev_end <= cue.start() for _, prev_end, _ in positive)
                    and not re.search(r"[.!?;]", between)
                    and len(words_between) <= 2
                    and set(words_between) <= _CATEGORY_CUE_BRIDGE_WORDS
                ):
                    next_categories = [s for s, _, _ in positive if s >= cue.end()]
                    if next_categories:
                        additive = True
                        break
        if additive:
            break

    if not additive:
        # A trailing "also" clearly scopes back to the just-mentioned category;
        # other trailing cue occurrences may refer to an unrelated condition.
        trailing_also = re.search(r"\balso\s*[,!]*\s*$", text, re.IGNORECASE)
        if trailing_also and any(end <= trailing_also.start() for _, end, _ in positive):
            additive = True

    # A negative plus a positive category joined without a contrast marker has
    # ambiguous scope (e.g. "not electronics and footwear").
    if negatives and positive:
        text_without_contrast = re.sub(r"\b(?:but|instead)\b", "\x00", text, flags=re.IGNORECASE)
        for n_start, n_end in negatives:
            for p_start, _, _ in positive:
                if p_start > n_start:
                    bridge = text_without_contrast[n_end:p_start]
                    if re.search(r"\b(?:and|or)\b", bridge, re.IGNORECASE):
                        ambiguous = True

    return categories, additive, ambiguous


def _catalogue_attribute_matches(text: str, vocab: CatalogueVocab | None) -> list[AttrConstraint]:
    """Find whole catalogue attribute values while retaining their trusted key."""
    matches: set[AttrConstraint] = set()
    for raw_key, raw_values in ((vocab.attr_values_by_key if vocab else {}) or {}).items():
        key = " ".join(str(raw_key).casefold().split())
        if not key:
            continue
        key_matches: list[tuple[int, int, str]] = []
        for raw_value in raw_values or ():
            value = " ".join(str(raw_value).casefold().split())
            if value:
                key_matches.extend((m.start(), m.end(), value) for m in re.finditer(
                    r"(?<!\w)" + re.escape(value) + r"(?!\w)", text
                ))
        # Prefer the longest phrase at an overlapping span (e.g. "stainless steel"
        # over the nested value "steel"); retain separate non-overlapping values.
        accepted_spans: list[tuple[int, int]] = []
        for start, end, value in sorted(key_matches, key=lambda m: (-(m[1] - m[0]), m[0], m[2])):
            if any(start < other_end and other_start < end for other_start, other_end in accepted_spans):
                continue
            accepted_spans.append((start, end))
            matches.add(AttrConstraint(key=key, value=value))
    return sorted(matches, key=lambda attr: (attr.key or "", attr.value))


def parse_query(
    message: str,
    view_has_focus: bool,
    vocab: CatalogueVocab | None,
    settings: Settings,
    product_repo: ProductRepo | None = None,
) -> ParsedQuery:
    nt = normalize(
        message,
        settings.codemix,
        vocab,
        settings.thresholds.spell_ratio,
    )
    approximate_price_matches = tuple(_APPROXIMATE_PRICE_CUE.finditer(nt.text))
    if approximate_price_matches:
        nt = replace(
            nt,
            trace=nt.trace + tuple(
                TraceItem(
                    "PRICE_APPROXIMATION_IGNORED",
                    match.group(0),
                    "ignored as a hard price constraint",
                )
                for match in approximate_price_matches
            ),
        )
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

    # 3. Classify intent. Bare "available" does not identify stock or presence.
    explicit_stock_wording = bool(re.search(r"\b(?:in[- ]stock|out of stock|sold out)\b", text_low))
    explicit_presence_wording = bool(re.match(r"^(?:do you (?:have|sell|carry)|is there|are there)\b", text_low))
    explicit_attribute_question = bool(re.search(
        r"\bdoes\s+(?:this|that|the)\s+(?:product|item|one)\s+have\b", text_low
    ))
    available_ambiguous = (
        "available" in text_low
        and not explicit_stock_wording
        and not explicit_presence_wording
        and not explicit_attribute_question
    )
    intent_res = classify_intent(
        nt,
        settings.intent_cues,
        has_focus=view_has_focus or refs_focus,
        has_product_id=len(explicit_ids) > 0,
    )
    if available_ambiguous and intent_res.intent != Intent.OUT_OF_SCOPE:
        intent_res = intent_res.__class__(Intent.SEARCH, (), None, intent_res.confidence)

    ops: list[ConstraintOp] = []

    # 4. Parse prices
    price_ops = parse_price_phrases(nt.text, settings.thresholds.price_under_is_inclusive)
    ops.extend(price_ops)

    # 5. Parse stock
    if "in stock" in text_low or "in-stock" in text_low:
        ops.append(ConstraintOp(op=OpKind.SET, key="stock", value="IN_STOCK", origin="USER_TEXT"))
    elif "out of stock" in text_low or "sold out" in text_low:
        ops.append(ConstraintOp(op=OpKind.SET, key="stock", value="OUT_OF_STOCK", origin="USER_TEXT"))

    # 6. Parse positive categories and their turn-level additive intent.
    matched_cats, additive_category_cue, category_ambiguous = _parse_categories(
        text_low, settings.category_aliases
    )
    if category_ambiguous:
        # Category exclusions are not representable by the positive OR set.
        # Let the decision layer ask for clarification without mutating state.
        matched_cats.clear()
    for cat in sorted(matched_cats):
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

    # Catalogue values are trusted only for their own normalized attribute key.
    # Deduplicate values already recognized by the legacy colour/material lists.
    catalogue_attrs = _catalogue_attribute_matches(text_low, vocab)
    for attr in catalogue_attrs:
        if attr not in attrs_this_turn:
            ops.append(ConstraintOp(
                op=OpKind.ADD,
                key="attribute",
                value={"key": attr.key, "value": attr.value},
                origin="USER_TEXT",
            ))
            attrs_this_turn.append(attr)

    # 8. Extract search terms (content words)
    stop_words = set(settings.stopwords) | {
        "show", "shoes", "shoe", "black", "under", "price", "stock", "find", "only",
        "products", "ones", "also", "add", "include", "addition", "along", "plus",
        "well", "replace", "meant",
        "available",
        # Approximate-price cues are neither price bounds nor product search terms.
        "around", "about",
        # Intent/reference cues are not lexical product requirements (RAG_PIPELINE §3).
        "sell", "carry", "have", "does", "there", "this", "that",
        "what", "which", "who", "when", "where", "why", "how",
    }
    # Add matched category aliases, colors, and materials to stopwords so they are not treated as dangling search terms
    for a in settings.category_aliases.keys():
        stop_words.add(a.lower())
    for c in _KNOWN_COLORS:
        stop_words.add(c.lower())
    for m in _KNOWN_MATERIALS:
        stop_words.add(m.lower())
    for attr in catalogue_attrs:
        stop_words.update(attr.value.split())

    entity_terms = []
    for w in words:
        # Do not fuse an English possessive into a new lexical token ("men's"
        # should contribute "men", not the unsupported term "mens").
        token = re.sub(r"['’]s$", "", w, flags=re.IGNORECASE)
        clean_w = re.sub(r"[^\w]", "", token)
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

    final_intent = intent_res.intent
    if final_intent == Intent.UNKNOWN and intent_res.requested_fields and entity_terms:
        # GROUNDING_AND_REFUSAL D-05: a requested product fact can be named by
        # entity text; it does not require an explicit ID or anaphoric focus.
        final_intent = Intent.PRODUCT_FACT
    elif final_intent == Intent.UNKNOWN and (entity_terms or ops):
        # RAG_PIPELINE §3: unknown intent with a concrete term/constraint follows
        # the ordinary search path; a content-free unknown remains UNKNOWN and
        # is clarified by the decision layer.
        final_intent = Intent.SEARCH

    return ParsedQuery(
        original=message,
        normalized=nt,
        intent=final_intent,
        requested_fields=intent_res.requested_fields,
        product_ids=tuple(explicit_ids),
        entity_terms=tuple(entity_terms),
        references_focus=refs_focus,
        policy_topic=intent_res.policy_topic,
        constraint_ops=tuple(ops),
        this_turn_constraints=this_turn_cs,
        confidence=intent_res.confidence,
        additive_category_cue=additive_category_cue and not category_ambiguous,
        category_ambiguous=category_ambiguous,
        available_ambiguous=available_ambiguous,
        attribute_question=explicit_attribute_question,
        unsupported_action=intent_res.unsupported_action,
    )
