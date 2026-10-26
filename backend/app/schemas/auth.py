from __future__ import annotations

import re
from pydantic import Field, field_validator

from app.schemas.common import ContractModel


_EMAIL = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


def normalize_email(value: str) -> str:
    normalized = value.strip().casefold()
    local, separator, domain = normalized.partition("@")
    if (
        not separator
        or len(normalized) > 254
        or len(local) > 64
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
        or not _EMAIL.fullmatch(normalized)
    ):
        raise ValueError("Enter a valid email address")
    return normalized


class SignupRequest(ContractModel):
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        name = value.strip()
        if not name or any(ord(char) < 32 for char in name):
            raise ValueError("Enter a valid name")
        return name

    @field_validator("email")
    @classmethod
    def normalized_email(cls, value: str) -> str:
        return normalize_email(value)


class LoginRequest(ContractModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalized_email(cls, value: str) -> str:
        return normalize_email(value)


class AuthUserResponse(ContractModel):
    user_id: str
    name: str
    email: str


class CsrfResponse(ContractModel):
    csrf_token: str


class LogoutResponse(ContractModel):
    logged_out: bool
