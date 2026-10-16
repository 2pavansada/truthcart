import uuid
from typing import Any
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from app.schemas.common import ErrorBody, ErrorEnvelope


class ApiError(Exception):
    def __init__(self, code: str, status_code: int, message: str, details: list[dict[str, Any]] | None = None):
        self.code = code
        self.status_code = status_code
        self.message = message
        self.details = details
        super().__init__(message)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError):
        req_id = getattr(request.state, "request_id", str(uuid.uuid4())[:8])
        envelope = ErrorEnvelope(
            error=ErrorBody(
                code=exc.code,
                message=exc.message,
                details=exc.details,
                request_id=req_id,
            )
        )
        return JSONResponse(status_code=exc.status_code, content=envelope.model_dump())

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        req_id = getattr(request.state, "request_id", str(uuid.uuid4())[:8])
        details = []
        for err in exc.errors():
            loc = ".".join(str(l) for l in err.get("loc", []))
            details.append({"field": loc, "issue": err.get("msg", "invalid")})

        envelope = ErrorEnvelope(
            error=ErrorBody(
                code="VALIDATION_ERROR",
                message="Request validation error",
                details=details,
                request_id=req_id,
            )
        )
        return JSONResponse(status_code=422, content=envelope.model_dump())

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        req_id = getattr(request.state, "request_id", str(uuid.uuid4())[:8])
        print(f"Unhandled exception [request_id={req_id}]: {exc}")
        envelope = ErrorEnvelope(
            error=ErrorBody(
                code="INTERNAL_ERROR",
                message="An internal server error occurred",
                details=None,
                request_id=req_id,
            )
        )
        return JSONResponse(status_code=500, content=envelope.model_dump())
