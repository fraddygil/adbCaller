from __future__ import annotations

import asyncio
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from adbcaller_server.config import Settings, get_settings
from adbcaller_server.database import get_session, set_tenant
from adbcaller_server.models import AuditLog, Tenant, User, UserSession
from adbcaller_server.models.enums import TenantStatus, UserRole
from adbcaller_server.rate_limit import enforce_rate_limit
from adbcaller_server.security import (
    csrf_token,
    new_scoped_secret,
    parse_scoped_secret,
    secret_hash,
    verify_password,
)


SESSION_COOKIE = "adbcaller_session"
router = APIRouter(prefix="/auth", tags=["auth"])


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(ApiModel):
    tenant_slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().casefold()
        if value.count("@") != 1:
            raise ValueError("Email inválido")
        return value


class AuthResponse(ApiModel):
    tenant_id: UUID
    user_id: UUID
    email: str
    role: UserRole
    csrf_token: str


@dataclass(slots=True)
class AuthContext:
    token: str
    tenant_id: UUID
    user: User
    user_session: UserSession


async def current_user(
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    if session_token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesión requerida")
    try:
        (tenant_id,) = parse_scoped_secret(session_token, 1)
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesión inválida") from exc

    await set_tenant(session, tenant_id)
    now = datetime.now(UTC)
    row = await session.execute(
        select(UserSession, User)
        .join(
            User,
            (User.tenant_id == UserSession.tenant_id)
            & (User.id == UserSession.user_id),
        )
        .where(
            UserSession.tenant_id == tenant_id,
            UserSession.token_hash == secret_hash(session_token, settings.token_pepper),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
            User.active.is_(True),
        )
    )
    record = row.one_or_none()
    if record is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesión inválida o expirada")
    user_session, user = record
    return AuthContext(session_token, tenant_id, user, user_session)


async def valid_csrf(
    context: AuthContext = Depends(current_user),
    supplied_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    expected = csrf_token(context.token, settings.session_secret)
    if supplied_token is None or not hmac.compare_digest(supplied_token, expected):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Token CSRF inválido")
    return context


async def admin_user(context: AuthContext = Depends(current_user)) -> AuthContext:
    if context.user.role is not UserRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Se requiere rol administrador")
    return context


async def admin_csrf(context: AuthContext = Depends(valid_csrf)) -> AuthContext:
    if context.user.role is not UserRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Se requiere rol administrador")
    return context


@router.post("/login", response_model=AuthResponse)
async def login(
    request: LoginRequest,
    response: Response,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AuthResponse:
    tenant = (
        await session.execute(
            select(Tenant).where(
                Tenant.slug == request.tenant_slug,
                Tenant.status == TenantStatus.ACTIVE,
            )
        )
    ).scalar_one_or_none()
    if tenant is None:
        raise invalid_login()

    address = http_request.client.host if http_request.client else "unknown"
    bucket = secret_hash(
        f"{address}:{request.email}", settings.token_pepper
    ).hex()
    await enforce_rate_limit(tenant.id, f"login:{bucket}", 10)
    await set_tenant(session, tenant.id)
    user = (
        await session.execute(
            select(User).where(
                User.tenant_id == tenant.id,
                User.email == request.email,
                User.active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if user is None or not await asyncio.to_thread(
        verify_password, user.password_hash, request.password
    ):
        raise invalid_login()

    now = datetime.now(UTC)
    token = new_scoped_secret(tenant.id)
    login_session = UserSession(
        tenant_id=tenant.id,
        user_id=user.id,
        token_hash=secret_hash(token, settings.token_pepper),
        expires_at=now + timedelta(seconds=settings.session_ttl_seconds),
        last_seen_at=now,
    )
    user.last_login_at = now
    session.add(login_session)
    session.add(
        AuditLog(
            tenant_id=tenant.id,
            user_id=user.id,
            action="auth.login",
            outcome="SUCCESS",
            metadata_json={},
            created_at=now,
        )
    )
    await session.commit()

    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.public_url.startswith("https://"),
        samesite="strict",
        path="/",
    )
    return auth_response(user, tenant.id, token, settings)


@router.get("/me", response_model=AuthResponse)
async def me(
    context: AuthContext = Depends(current_user),
    settings: Settings = Depends(get_settings),
) -> AuthResponse:
    return auth_response(context.user, context.tenant_id, context.token, settings)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    context: AuthContext = Depends(valid_csrf),
    session: AsyncSession = Depends(get_session),
) -> None:
    now = datetime.now(UTC)
    context.user_session.revoked_at = now
    session.add(
        AuditLog(
            tenant_id=context.tenant_id,
            user_id=context.user.id,
            action="auth.logout",
            outcome="SUCCESS",
            metadata_json={},
            created_at=now,
        )
    )
    await session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


def auth_response(
    user: User, tenant_id: UUID, token: str, settings: Settings
) -> AuthResponse:
    return AuthResponse(
        tenant_id=tenant_id,
        user_id=user.id,
        email=user.email,
        role=user.role,
        csrf_token=csrf_token(token, settings.session_secret),
    )


def invalid_login() -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciales inválidas")
