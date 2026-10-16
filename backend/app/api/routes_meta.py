from fastapi import APIRouter, Depends
from app.api.deps import get_container
from app.container import Container
from app.schemas.product import CatalogueMeta

router = APIRouter(prefix="/api/catalogue", tags=["Catalogue Meta"])


@router.get("/meta", response_model=CatalogueMeta)
async def get_catalogue_meta(
    container: Container = Depends(get_container),
) -> CatalogueMeta:
    return container.product_repo.meta()
