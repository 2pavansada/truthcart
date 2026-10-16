from typing import Any, Mapping
from app.domain.enums import AttrBasis, ClaimScope, ClaimType
from app.domain.plans import ClaimSpec
from app.schemas.evidence import Evidence, PolicyEvidence, ProductFieldEvidence


def render_claim(spec: ClaimSpec, bundle: Mapping[str, Evidence]) -> str:
    pid = spec.product_id

    # Retrieve title from bundle if product scope
    title = pid or ""
    if pid:
        title_ev = bundle.get(f"prod:{pid}:title")
        if isinstance(title_ev, ProductFieldEvidence) and title_ev.display_value:
            title = title_ev.display_value

    t = spec.type

    if t == ClaimType.PRODUCT_EXISTS:
        return f"“{title}” ({pid}) is listed in the supplied catalogue."

    elif t == ClaimType.PRICE:
        minor = spec.value.get("price_minor", 0)
        curr = spec.value.get("currency", "INR")
        symbol = "₹" if curr == "INR" else f"{curr} "
        price_display = f"{symbol}{minor / 100.0:,.2f}"
        return f"The price of “{title}” ({pid}) is {price_display}."

    elif t == ClaimType.STOCK:
        st = spec.value.get("stock_status", "")
        st_text = "in stock" if st == "IN_STOCK" else "out of stock"
        return f"“{title}” ({pid}) is {st_text}."

    elif t == ClaimType.RATING:
        r = spec.value.get("rating")
        rc = spec.value.get("rating_count")
        rc_text = f" from {rc} ratings" if rc else ""
        return f"“{title}” ({pid}) has a rating of {r}{rc_text}."

    elif t == ClaimType.CATEGORY:
        cat = spec.value.get("category", "")
        return f"“{title}” ({pid}) is listed under {cat}."

    elif t == ClaimType.ATTRIBUTE:
        k = spec.value.get("key")
        v = spec.value.get("value")
        basis = spec.value.get("basis", AttrBasis.STRUCTURED)
        if basis == AttrBasis.STRUCTURED:
            key_label = k or "attribute"
            return f"The catalogue lists {key_label} as {v} for “{title}” ({pid})."
        else:
            return f"The description of “{title}” ({pid}) mentions {v}."

    elif t == ClaimType.DESCRIPTION_QUOTE:
        q = spec.value.get("quote", "")
        return f"The description of “{title}” ({pid}) says: “{q}”"

    elif t == ClaimType.FIELD_UNAVAILABLE:
        f = spec.value.get("field", "field")
        return f"The {f} of “{title}” ({pid}) is not available in the catalogue."

    elif t == ClaimType.PRODUCT_SUMMARY:
        minor = spec.value.get("price_minor")
        curr = spec.value.get("currency", "INR")
        if minor is not None:
            symbol = "₹" if curr == "INR" else f"{curr} "
            price_display = f"{symbol}{minor / 100.0:,.2f}"
        else:
            price_display = "price not available"

        st = spec.value.get("stock_status")
        if st == "IN_STOCK":
            stock_label = "in stock"
        elif st == "OUT_OF_STOCK":
            stock_label = "out of stock"
        else:
            stock_label = "stock status not available"

        r = spec.value.get("rating")
        rating_text = f", rated {r}★" if r is not None else ""
        return f"“{title}” ({pid}): {price_display}, {stock_label}{rating_text}."

    elif t == ClaimType.RESULT_COUNT:
        cnt = spec.value.get("count", 0)
        return f"{cnt} product(s) in the supplied catalogue match your request."

    elif t == ClaimType.ABSENCE:
        searched = spec.value.get("searched", "your search")
        if spec.scope == ClaimScope.GENERAL_POLICY:
            return f"The supplied policy document does not state {searched}."
        return f"No product matching “{searched}” is listed in the supplied catalogue."

    elif t == ClaimType.POLICY_QUOTE:
        q = spec.value.get("quote", "")
        sec_id = spec.value.get("section_id", "")
        heading = f"Section {sec_id}"
        pol_ev = bundle.get(f"pol:{sec_id}")
        if isinstance(pol_ev, PolicyEvidence) and pol_ev.heading:
            heading = pol_ev.heading
        return f"The policy ({heading}) states: “{q}”"

    elif t == ClaimType.POLICY_NOT_FOUND:
        searched = spec.value.get("searched", "your topic")
        return f"The supplied policy document does not state {searched}."

    return ""


def render_nf(kind: str, params: Mapping[str, Any] | None = None) -> str:
    params = params or {}
    if kind == "NF-01":
        return "Hello! Ask me about products in the catalogue or about returns, shipping and warranty."
    elif kind == "NF-02":
        return "I can look up product prices, stock, ratings and details, search with filters, and quote the supplied policy."
    elif kind == "NF-03":
        return "Okay, I've cleared your filters."
    elif kind == "NF-04":
        entity = params.get("entity", "this product")
        return f"More than one product matches “{entity}”. Which one do you mean?"
    elif kind == "NF-05":
        return "Which product do you mean?"
    elif kind == "NF-06":
        return "What are you looking for? You can mention a category, a price limit, or whether it must be in stock."
    elif kind == "NF-07":
        a = params.get("label_a", "filter A")
        b = params.get("label_b", "filter B")
        return f"Those filters can't both apply: {a} and {b}. Which one should I keep?"
    elif kind == "NF-08":
        return "Which policy would you like to know about?"
    elif kind == "NF-09":
        return "I can only help with products in this catalogue and its returns, shipping and warranty policy."
    elif kind == "NF-10":
        return "I can't place orders, process payments or track shipments."
    elif kind == "NF-11":
        cs = params.get("constraints", "")
        return f"Still applying: {cs}."
    elif kind == "NF-13":
        return "I couldn't produce a verified answer to that. Please try rephrasing."
    return ""
