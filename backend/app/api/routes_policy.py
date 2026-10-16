from typing import List
from fastapi import APIRouter, Depends
from app.api.deps import get_container
from app.api.errors import ApiError
from app.container import Container
from app.schemas.product import PolicySectionDetail

router = APIRouter(prefix="/api/policy", tags=["Policy"])


@router.get("/sections", response_model=List[PolicySectionDetail])
async def list_policy_sections(
    container: Container = Depends(get_container),
) -> List[PolicySectionDetail]:
    secs = container.policy_repo.list_sections()
    return [
        PolicySectionDetail(
            section_id=s.section_id,
            heading=s.heading,
            body=s.body,
            parent_id=s.parent_id,
        )
        for s in secs
    ]


@router.get("/sections/{section_id}", response_model=PolicySectionDetail)
async def get_policy_section(
    section_id: str,
    container: Container = Depends(get_container),
) -> PolicySectionDetail:
    sec = container.policy_repo.get_section(section_id)
    if not sec:
        raise ApiError(
            code="POLICY_SECTION_NOT_FOUND",
            status_code=404,
            message=f"Policy section '{section_id}' was not found",
        )
    return PolicySectionDetail(
        section_id=sec.section_id,
        heading=sec.heading,
        body=sec.body,
        parent_id=sec.parent_id,
    )
