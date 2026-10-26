from __future__ import annotations

import hmac
import sqlite3
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request, Response
from argon2.exceptions import Argon2Error
from pwdlib import PasswordHash
from pwdlib.exceptions import PwdlibError

from app.api.errors import ApiError
from app.schemas.auth import (
    AuthUserResponse,
    CsrfResponse,
    LoginRequest,
    LogoutResponse,
    SignupRequest,
)
from app.store.auth_store import AuthStore, AuthUser


router = APIRouter(prefix="/api/auth", tags=["Authentication"])
SESSION_COOKIE = "truthcart_session"
CSRF_COOKIE = "truthcart_csrf"
CSRF_HEADER = "X-CSRF-Token"
password_hash = PasswordHash.recommended()
_DUMMY_PASSWORD_HASH = password_hash.hash("dummy-password-used-to-equalize-login-work")


def _store(request: Request) -> AuthStore:
    return request.app.state.auth_store


def _cookie_secure(request: Request) -> bool:
    return bool(request.app.state.settings.auth.cookie_secure or request.url.scheme == "https")


def _is_allowed_origin(request: Request, value: str) -> bool:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    normalized = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    allowed = {str(item).rstrip("/") for item in request.app.state.settings.app.cors.get("origins", [])}
    current = str(request.base_url).rstrip("/")
    return normalized == current or normalized in allowed


def _same_site_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin:
        parsed = urlsplit(origin)
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return False
        return _is_allowed_origin(request, origin)

    # Same-origin browser requests can omit Origin. Fetch Metadata or Referer
    # identifies the calling site; retain strict checks when Origin is present.
    if request.headers.get("sec-fetch-site", "").lower() == "same-origin":
        return True
    referer = request.headers.get("referer")
    return bool(referer and _is_allowed_origin(request, referer))


def _validate_csrf(request: Request, store: AuthStore) -> None:
    cookie_token = request.cookies.get(CSRF_COOKIE, "")
    header_token = request.headers.get(CSRF_HEADER, "")
    if (
        not _same_site_origin(request)
        or not cookie_token
        or not header_token
        or not hmac.compare_digest(cookie_token, header_token)
        or not store.consume_csrf_token(cookie_token)
    ):
        raise ApiError(
            code="CSRF_VALIDATION_FAILED",
            status_code=403,
            message="The request could not be verified. Refresh the page and try again.",
        )


def _public_user(user: AuthUser) -> AuthUserResponse:
    return AuthUserResponse(user_id=user.user_id, name=user.name, email=user.email)


def _set_session_cookie(request: Request, response: Response, store: AuthStore, user: AuthUser) -> None:
    old_token = request.cookies.get(SESSION_COOKIE)
    token, _expires_at = store.create_session(user.user_id, replacing_token=old_token)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=store.session_ttl_seconds,
        path="/",
        secure=_cookie_secure(request),
        httponly=True,
        samesite="lax",
    )


@router.get("/csrf", response_model=CsrfResponse)
async def issue_csrf_token(request: Request, response: Response, store: AuthStore = Depends(_store)) -> CsrfResponse:
    if not _same_site_origin(request):
        raise ApiError(
            code="CSRF_VALIDATION_FAILED",
            status_code=403,
            message="The request could not be verified. Refresh the page and try again.",
        )
    token, _expires_at = store.issue_csrf_token()
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        key=CSRF_COOKIE,
        value=token,
        max_age=store.csrf_ttl_seconds,
        path="/",
        secure=_cookie_secure(request),
        httponly=True,
        samesite="lax",
    )
    return CsrfResponse(csrf_token=token)


@router.post("/signup", response_model=AuthUserResponse, status_code=201)
async def signup(
    payload: SignupRequest,
    request: Request,
    response: Response,
    store: AuthStore = Depends(_store),
) -> AuthUserResponse:
    _validate_csrf(request, store)
    hashed = password_hash.hash(payload.password)
    try:
        user = store.create_user(payload.name, payload.email, hashed)
    except sqlite3.IntegrityError:
        raise ApiError(
            code="ACCOUNT_NOT_CREATED",
            status_code=409,
            message="An account could not be created with those details.",
        ) from None
    _set_session_cookie(request, response, store, user)
    return _public_user(user)


@router.post("/login", response_model=AuthUserResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    store: AuthStore = Depends(_store),
) -> AuthUserResponse:
    _validate_csrf(request, store)
    user = store.find_user_by_email(payload.email)
    candidate_hash = user.password_hash if user else _DUMMY_PASSWORD_HASH
    try:
        password_matches = password_hash.verify(payload.password, candidate_hash)
    except (PwdlibError, Argon2Error):
        password_matches = False
    if user is None or not password_matches:
        raise ApiError(
            code="INVALID_CREDENTIALS",
            status_code=401,
            message="Email or password is incorrect.",
        )
    _set_session_cookie(request, response, store, user)
    return _public_user(user)


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    response: Response,
    store: AuthStore = Depends(_store),
) -> LogoutResponse:
    _validate_csrf(request, store)
    store.revoke_session(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(
        key=SESSION_COOKIE,
        path="/",
        secure=_cookie_secure(request),
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        key=CSRF_COOKIE,
        path="/",
        secure=_cookie_secure(request),
        httponly=True,
        samesite="lax",
    )
    return LogoutResponse(logged_out=True)


@router.get("/me", response_model=AuthUserResponse)
async def current_user(request: Request, store: AuthStore = Depends(_store)) -> AuthUserResponse:
    user = store.user_for_session(request.cookies.get(SESSION_COOKIE))
    if user is None:
        raise ApiError(
            code="AUTH_REQUIRED",
            status_code=401,
            message="Sign in to access your account.",
        )
    return _public_user(user)
