from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from adbcaller_server.auth import ApiModel, AuthContext, admin_user, current_user
from adbcaller_server.database import get_session
from adbcaller_server.models import AuditLog, Call


router = APIRouter(tags=["history"])


class CallResponse(ApiModel):
    id: UUID
    device_id: UUID
    number: str
    state: str
    result: str
    error: str | None
    started_at: datetime
    ended_at: datetime | None


class AuditResponse(ApiModel):
    id: UUID
    user_id: UUID | None
    agent_id: UUID | None
    device_id: UUID | None
    task_id: UUID | None
    action: str
    outcome: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


@router.get("/calls", response_model=list[CallResponse])
async def list_calls(
    limit: int = Query(default=100, ge=1, le=500),
    context: AuthContext = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[CallResponse]:
    values = (
        await session.execute(
            select(Call)
            .where(Call.tenant_id == context.tenant_id)
            .order_by(Call.started_at.desc())
            .limit(limit)
        )
    ).scalars()
    return [
        CallResponse(
            id=value.id,
            device_id=value.device_id,
            number=value.normalized_number,
            state=value.state,
            result=value.result,
            error=value.error,
            started_at=value.started_at,
            ended_at=value.ended_at,
        )
        for value in values
    ]


@router.get("/audit", response_model=list[AuditResponse])
async def list_audit(
    limit: int = Query(default=100, ge=1, le=500),
    context: AuthContext = Depends(admin_user),
    session: AsyncSession = Depends(get_session),
) -> list[AuditResponse]:
    values = (
        await session.execute(
            select(AuditLog)
            .where(AuditLog.tenant_id == context.tenant_id)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
    ).scalars()
    return [
        AuditResponse(
            id=value.id,
            user_id=value.user_id,
            agent_id=value.agent_id,
            device_id=value.device_id,
            task_id=value.task_id,
            action=value.action,
            outcome=value.outcome,
            metadata=value.metadata_json,
            created_at=value.created_at,
        )
        for value in values
    ]
