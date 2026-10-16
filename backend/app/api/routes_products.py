from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from app.api.deps import get_container
from app.api.errors import ApiError
from app.container import Container
from app.schemas.product import ProductDetail
from app.store.product_repo import Product

router = APIRouter(prefix="/api/products", tags=["Products"])


def _to_detail(p: Product, container: Container) -> ProductDetail:
    attrs = container.product_repo.attributes(p.product_id)
    unavail = []
    if p.price_minor is None:
        unavail.append("price")
    if p.stock_status.value == "UNKNOWN":
        unavail.append("stock")
    if p.rating is None:
        unavail.append("rating")
    if not p.category:
        unavail.append("category")
    if not p.description:
        unavail.append("description")

    return ProductDetail(
        product_id=p.product_id,
        title=p.title,
        description=p.description,
        category=p.category,
        price_minor=p.price_minor,
        currency=p.currency,
        price_display=p.price_display,
        rating=p.rating,
        rating_count=p.rating_count,
        stock_status=p.stock_status,
        stock_label=p.stock_label,
        stock_quantity=p.stock_quantity,
        attributes=attrs,
        unavailable_fields=unavail,
        flags={"suspicious_text": bool(p.suspicious_text)},
    )


@router.get("", response_model=List[ProductDetail])
async def get_products_batch(
    ids: str = Query(..., description="Comma-separated product IDs (max 20)"),
    container: Container = Depends(get_container),
) -> List[ProductDetail]:
    id_list = [i.strip() for i in ids.split(",") if i.strip()]
    if len(id_list) > 20:
        raise ApiError(
            code="VALIDATION_ERROR",
            status_code=422,
            message="Cannot query more than 20 product IDs at once",
        )
    prods = container.product_repo.get_many(id_list)
    return [_to_detail(p, container) for p in prods.values()]


@router.get("/{product_id}", response_model=ProductDetail)
async def get_product(
    product_id: str,
    container: Container = Depends(get_container),
) -> ProductDetail:
    p = container.product_repo.get(product_id)
    if not p:
        raise ApiError(
            code="PRODUCT_NOT_FOUND",
            status_code=404,
            message=f"Product '{product_id}' was not found in the catalogue",
        )
    return _to_detail(p, container)
