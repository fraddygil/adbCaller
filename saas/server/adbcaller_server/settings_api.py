from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from adbcaller_server.auth import ApiModel, AuthContext, admin_csrf, current_user
from adbcaller_server.config import get_settings
from adbcaller_server.database import get_session, session_factory, set_tenant
from adbcaller_server.models import TenantSetting
from adbcaller_server.realtime import event_hub


router = APIRouter(prefix="/settings", tags=["settings"])
AUTOMATION_KEY = "call_automation"


class AutomationSettings(ApiModel):
    auto_speaker: bool = True
    conference_call_timeout_seconds: int = Field(default=45, ge=10, le=300)
    conference_merge_attempts: int = Field(default=5, ge=1, le=10)
    max_conference_calls: int = Field(default=4, ge=2, le=20)


@router.get("", response_model=AutomationSettings)
async def read_settings(
    context: AuthContext = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> AutomationSettings:
    return await settings_from_session(session, context.tenant_id)


@router.put("", response_model=AutomationSettings)
async def update_settings(
    values: AutomationSettings,
    context: AuthContext = Depends(admin_csrf),
    session: AsyncSession = Depends(get_session),
) -> AutomationSettings:
    setting = (
        await session.execute(
            select(TenantSetting).where(
                TenantSetting.tenant_id == context.tenant_id,
                TenantSetting.key == AUTOMATION_KEY,
            )
        )
    ).scalar_one_or_none()
    payload = values.model_dump(mode="json")
    if setting is None:
        session.add(
            TenantSetting(
                tenant_id=context.tenant_id,
                key=AUTOMATION_KEY,
                value=payload,
                updated_by_user_id=context.user.id,
            )
        )
    else:
        setting.value = payload
        setting.updated_by_user_id = context.user.id
    await session.commit()
    await event_hub.publish(context.tenant_id, "settings.updated", {})
    return values


async def load_automation_settings(tenant_id) -> AutomationSettings:
    async with session_factory() as session:
        await set_tenant(session, tenant_id)
        return await settings_from_session(session, tenant_id)


async def settings_from_session(
    session: AsyncSession, tenant_id
) -> AutomationSettings:
    value = (
        await session.execute(
            select(TenantSetting.value).where(
                TenantSetting.tenant_id == tenant_id,
                TenantSetting.key == AUTOMATION_KEY,
            )
        )
    ).scalar_one_or_none()
    if value is not None:
        return AutomationSettings.model_validate(value)
    defaults = get_settings()
    return AutomationSettings(
        conference_call_timeout_seconds=defaults.conference_call_timeout_seconds,
        conference_merge_attempts=defaults.conference_merge_attempts,
        max_conference_calls=defaults.max_conference_calls,
    )
