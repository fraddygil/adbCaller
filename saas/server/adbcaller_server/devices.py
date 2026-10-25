from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from adbcaller_server.auth import ApiModel, AuthContext, current_user
from adbcaller_server.database import get_session
from adbcaller_server.models import Device


router = APIRouter(prefix="/devices", tags=["devices"])


class DeviceResponse(ApiModel):
    id: UUID
    agent_id: UUID
    adb_serial: str
    manufacturer: str
    model: str
    android_version: str
    api_level: str
    adb_state: str
    call_state: str
    call_count: int
    online: bool
    blocked_by_limit: bool
    capabilities: dict[str, Any] = Field(default_factory=dict)
    last_seen_at: datetime | None


@router.get("", response_model=list[DeviceResponse])
async def list_devices(
    context: AuthContext = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[DeviceResponse]:
    devices = (
        await session.execute(
            select(Device)
            .where(Device.tenant_id == context.tenant_id)
            .order_by(Device.created_at)
        )
    ).scalars()
    return [
        DeviceResponse(
            id=device.id,
            agent_id=device.agent_id,
            adb_serial=device.adb_serial,
            manufacturer=device.manufacturer,
            model=device.model,
            android_version=device.android_version,
            api_level=device.api_level,
            adb_state=device.adb_state,
            call_state=device.call_state,
            call_count=device.call_count,
            online=device.online,
            blocked_by_limit=device.blocked_by_limit,
            capabilities=device.capabilities,
            last_seen_at=device.last_seen_at,
        )
        for device in devices
    ]
