from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from adbcaller_server.auth import ApiModel, AuthContext, admin_csrf, admin_user
from adbcaller_server.database import get_session
from adbcaller_server.models import AuditLog, User, UserSession
from adbcaller_server.models.enums import UserRole
from adbcaller_server.security import hash_password
from adbcaller_server.realtime import event_hub


router = APIRouter(prefix="/admin/users", tags=["users"])


class CreateUserRequest(ApiModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=1024)
    role: UserRole

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().casefold()
        if value.count("@") != 1:
            raise ValueError("Email inválido")
        return value


class UserResponse(ApiModel):
    id: UUID
    email: str
    role: UserRole
    active: bool
    created_at: datetime


@router.get("", response_model=list[UserResponse])
async def list_users(
    context: AuthContext = Depends(admin_user),
    session: AsyncSession = Depends(get_session),
) -> list[UserResponse]:
    users = (
        await session.execute(
            select(User)
            .where(User.tenant_id == context.tenant_id)
            .order_by(User.created_at)
        )
    ).scalars()
    return [user_response(user) for user in users]


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    request: CreateUserRequest,
    context: AuthContext = Depends(admin_csrf),
    session: AsyncSession = Depends(get_session),
) -> UserResponse:
    password_hash = await asyncio.to_thread(hash_password, request.password)
    user = User(
        tenant_id=context.tenant_id,
        email=request.email,
        password_hash=password_hash,
        role=request.role,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "El email ya está registrado") from exc
    session.add(
        AuditLog(
            tenant_id=context.tenant_id,
            user_id=context.user.id,
            action="user.create",
            outcome="SUCCESS",
            metadata_json={"created_user_id": str(user.id), "role": request.role.value},
            created_at=datetime.now(UTC),
        )
    )
    await session.commit()
    await event_hub.publish(
        context.tenant_id, "user.created", {"user_id": str(user.id)}
    )
    return user_response(user)


@router.post("/{user_id}/deactivate", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_user(
    user_id: UUID,
    context: AuthContext = Depends(admin_csrf),
    session: AsyncSession = Depends(get_session),
) -> None:
    if user_id == context.user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "No puede desactivar su propia cuenta")
    user = (
        await session.execute(
            select(User).where(
                User.tenant_id == context.tenant_id,
                User.id == user_id,
            )
        )
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado")
    now = datetime.now(UTC)
    user.active = False
    await session.execute(
        update(UserSession)
        .where(
            UserSession.tenant_id == context.tenant_id,
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    session.add(
        AuditLog(
            tenant_id=context.tenant_id,
            user_id=context.user.id,
            action="user.deactivate",
            outcome="SUCCESS",
            metadata_json={"deactivated_user_id": str(user_id)},
            created_at=now,
        )
    )
    await session.commit()
    await event_hub.publish(
        context.tenant_id, "user.deactivated", {"user_id": str(user_id)}
    )


def user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        active=user.active,
        created_at=user.created_at,
    )
