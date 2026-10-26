import uuid
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.errors import register_error_handlers
from app.api.routes_chat import router as chat_router
from app.api.routes_auth import router as auth_router
from app.api.routes_health import router as health_router
from app.api.routes_meta import router as meta_router
from app.api.routes_policy import router as policy_router
from app.api.routes_products import router as products_router
from app.container import build_container
from app.orchestrator import Orchestrator
from app.settings import Settings, load_settings
from app.store.auth_store import AuthStore


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    app = FastAPI(
        title="TruthCart AI API",
        description="The Conversational Shopping Assistant That Doesn't Lie",
        version="1.0.0",
    )

    # Build and attach DI container & orchestrator
    container = build_container(settings)
    orchestrator = Orchestrator(container)
    app.state.container = container
    app.state.orchestrator = orchestrator
    app.state.settings = settings
    auth_db_path = Path(settings.auth.database_path)
    if not auth_db_path.is_absolute():
        auth_db_path = settings.base_dir / auth_db_path
    app.state.auth_store = AuthStore(
        auth_db_path,
        session_ttl_seconds=settings.auth.session_ttl_seconds,
        csrf_ttl_seconds=settings.auth.csrf_ttl_seconds,
    )

    # Middleware: Request ID and timing
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        req_id = request.headers.get("X-Request-Id") or f"r_{uuid.uuid4().hex[:8]}"
        request.state.request_id = req_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = req_id
        return response

    # CORS
    cors_origins = settings.app.cors.get("origins", ["*"])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Error Handlers
    register_error_handlers(app)

    # Mount API routers
    app.include_router(chat_router)
    app.include_router(auth_router)
    app.include_router(products_router)
    app.include_router(policy_router)
    app.include_router(meta_router)
    app.include_router(health_router)

    # Static frontend mount if dist exists
    frontend_dist = settings.base_dir / "frontend" / "dist"
    if frontend_dist.exists() and frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

    return app


app = create_app()
