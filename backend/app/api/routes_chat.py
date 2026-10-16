import uuid
from fastapi import APIRouter, Depends, Request
from app.api.deps import get_orchestrator
from app.api.errors import ApiError
from app.orchestrator import Orchestrator
from app.schemas.chat import ChatRequest, ResetRequest, ResetResponse
from app.schemas.claims import ChatResponse

router = APIRouter(prefix="/api", tags=["Chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(
    req: ChatRequest,
    request: Request,
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> ChatResponse:
    # Validate message constraints: message 0-500 chars; empty only if constraint_ops or resolution present
    if not req.message.strip() and not req.constraint_ops and not req.resolution:
        raise ApiError(
            code="VALIDATION_ERROR",
            status_code=422,
            message="message must be 1-500 characters unless constraint_ops or resolution is provided",
            details=[{"field": "message", "issue": "cannot be empty without ops or resolution"}],
        )

    req_id = getattr(request.state, "request_id", f"r_{uuid.uuid4().hex[:8]}")
    resp = orchestrator.handle(req, req_id)
    return resp


@router.post("/session/reset", response_model=ResetResponse)
async def reset_session_endpoint(
    req: ResetRequest,
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> ResetResponse:
    orchestrator.reset(req.session_id)
    return ResetResponse(session_id=req.session_id, reset=True)
