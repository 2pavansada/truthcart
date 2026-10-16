from fastapi import APIRouter, Depends
from app.api.deps import get_container
from app.container import Container
from app.schemas.chat import HealthResponse, ReadinessReport

router = APIRouter(prefix="/api", tags=["Health"])


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/ready", response_model=ReadinessReport)
async def readiness_check(
    container: Container = Depends(get_container),
) -> ReadinessReport:
    return container.ready()
