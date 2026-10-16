from app.domain.constraints import ConstraintSet
from app.domain.plans import SearchSpec
from app.schemas.evidence import Relaxation
from app.store.product_repo import ProductRepo


def analyze_relaxations(
    spec: SearchSpec,
    product_repo: ProductRepo,
    verify_func,
) -> list[Relaxation]:
    relaxations = []
    c = spec.constraints

    # 1. Try dropping stock
    if c.stock is not None:
        c_no_stock = ConstraintSet(
            categories=c.categories,
            price_min=c.price_min,
            price_max=c.price_max,
            stock=None,
            rating_min=c.rating_min,
            attributes=c.attributes,
            terms=c.terms,
            sort=c.sort,
        )
        eligible = product_repo.eligible_ids(c_no_stock)
        matches = [pid for pid in eligible.ids if verify_func(pid, c.attributes)]
        if len(matches) > 0:
            relaxations.append(Relaxation(
                dropped="stock",
                label="In stock only",
                exact_match_count=len(matches),
            ))

    # 2. Try dropping price_max
    if c.price_max is not None:
        c_no_price = ConstraintSet(
            categories=c.categories,
            price_min=c.price_min,
            price_max=None,
            stock=c.stock,
            rating_min=c.rating_min,
            attributes=c.attributes,
            terms=c.terms,
            sort=c.sort,
        )
        eligible = product_repo.eligible_ids(c_no_price)
        matches = [pid for pid in eligible.ids if verify_func(pid, c.attributes)]
        if len(matches) > 0:
            rupees = c.price_max.amount_minor / 100.0
            relaxations.append(Relaxation(
                dropped="price_max",
                label=f"Price under ₹{rupees:,.2f}",
                exact_match_count=len(matches),
            ))

    # 3. Try dropping attributes
    if c.attributes:
        c_no_attr = ConstraintSet(
            categories=c.categories,
            price_min=c.price_min,
            price_max=c.price_max,
            stock=c.stock,
            rating_min=c.rating_min,
            attributes=(),
            terms=c.terms,
            sort=c.sort,
        )
        eligible = product_repo.eligible_ids(c_no_attr)
        if len(eligible.ids) > 0:
            attr_labels = ", ".join(f"{a.key}: {a.value}" if a.key else a.value for a in c.attributes)
            relaxations.append(Relaxation(
                dropped="attribute",
                label=f"Attribute {attr_labels}",
                exact_match_count=len(eligible.ids),
            ))

    return relaxations
