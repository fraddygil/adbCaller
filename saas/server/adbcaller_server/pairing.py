from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from adbcaller_server.agent_hub import agent_hub
from adbcaller_server.auth import ApiModel, AuthContext, admin_csrf, admin_user
from adbcaller_server.config import Settings, get_settings
from adbcaller_server.database import get_session, set_tenant
from adbcaller_server.models import Agent, AgentToken, AuditLog, Device, PairingCode, Tenant
from adbcaller_server.models.enums import AgentStatus
from adbcaller_server.rate_limit import enforce_rate_limit
from adbcaller_server.realtime import event_hub
from adbcaller_server.security import new_scoped_secret, parse_scoped_secret, secret_hash


router = APIRouter(tags=["pairing"])


class PairingCodeResponse(ApiModel):
    code: str
    expires_at: datetime


class PairAgentRequest(ApiModel):
    code: str = Field(min_length=8, max_length=128)
    agent_name: str = Field(min_length=1, max_length=100)
    agent_version: str = Field(min_length=1, max_length=50)
    platform: str = Field(min_length=1, max_length=50)


class PairAgentResponse(ApiModel):
    agent_id: UUID
    token: str
    protocol_version: int


class AgentResponse(ApiModel):
    id: UUID
    name: str
    status: AgentStatus
    agent_version: str
    platform: str
    last_seen_at: datetime | None


@router.post("/admin/pairing-codes", response_model=PairingCodeResponse)
async def create_pairing_code(
    context: AuthContext = Depends(admin_csrf),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PairingCodeResponse:
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=settings.pairing_ttl_seconds)
    code = new_scoped_secret(context.tenant_id)
    session.add(
        PairingCode(
            tenant_id=context.tenant_id,
            created_by_user_id=context.user.id,
            code_hash=secret_hash(code, settings.token_pepper),
            expires_at=expires_at,
        )
    )
    session.add(
        AuditLog(
            tenant_id=context.tenant_id,
            user_id=context.user.id,
            action="pairing.create",
            outcome="SUCCESS",
            metadata_json={},
            created_at=now,
        )
    )
    await session.commit()
    return PairingCodeResponse(code=code, expires_at=expires_at)


@router.post("/agent/pair", response_model=PairAgentResponse)
async def pair_agent(
    request: PairAgentRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PairAgentResponse:
    try:
        (tenant_id,) = parse_scoped_secret(request.code, 1)
    except ValueError as exc:
        raise invalid_pairing_code() from exc

    tenant_exists = (
        await session.execute(select(Tenant.id).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if tenant_exists is None:
        raise invalid_pairing_code()
    address = http_request.client.host if http_request.client else "unknown"
    bucket = secret_hash(address, settings.token_pepper).hex()
    await enforce_rate_limit(tenant_id, f"pair:{bucket}", 20)
    await set_tenant(session, tenant_id)
    now = datetime.now(UTC)
    pairing_code = (
        await session.execute(
            select(PairingCode)
            .where(
                PairingCode.tenant_id == tenant_id,
                PairingCode.code_hash
                == secret_hash(request.code, settings.token_pepper),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        pairing_code is None
        or pairing_code.used_at is not None
        or pairing_code.expires_at <= now
        or pairing_code.attempts >= pairing_code.max_attempts
    ):
        raise invalid_pairing_code()

    pairing_code.attempts += 1
    agent = Agent(
        tenant_id=tenant_id,
        name=request.agent_name.strip(),
        status=AgentStatus.OFFLINE,
        agent_version=request.agent_version,
        protocol_version=1,
        platform=request.platform,
    )
    session.add(agent)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya existe un agente con ese nombre") from exc

    token = new_scoped_secret(tenant_id, agent.id)
    session.add(
        AgentToken(
            tenant_id=tenant_id,
            agent_id=agent.id,
            token_hash=secret_hash(token, settings.token_pepper),
        )
    )
    pairing_code.used_at = now
    session.add(
        AuditLog(
            tenant_id=tenant_id,
            agent_id=agent.id,
            action="agent.pair",
            outcome="SUCCESS",
            metadata_json={"platform": request.platform},
            created_at=now,
        )
    )
    await session.commit()
    await event_hub.publish(tenant_id, "agent.paired", {"agent_id": str(agent.id)})
    return PairAgentResponse(agent_id=agent.id, token=token, protocol_version=1)


@router.get("/admin/agents", response_model=list[AgentResponse])
async def list_agents(
    context: AuthContext = Depends(admin_user),
    session: AsyncSession = Depends(get_session),
) -> list[AgentResponse]:
    agents = (
        await session.execute(
            select(Agent)
            .where(Agent.tenant_id == context.tenant_id)
            .order_by(Agent.created_at)
        )
    ).scalars()
    return [
        AgentResponse(
            id=agent.id,
            name=agent.name,
            status=agent.status,
            agent_version=agent.agent_version,
            platform=agent.platform,
            last_seen_at=agent.last_seen_at,
        )
        for agent in agents
    ]


@router.post("/admin/agents/{agent_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_agent(
    agent_id: UUID,
    context: AuthContext = Depends(admin_csrf),
    session: AsyncSession = Depends(get_session),
) -> None:
    now = datetime.now(UTC)
    agent = (
        await session.execute(
            select(Agent).where(
                Agent.tenant_id == context.tenant_id,
                Agent.id == agent_id,
            )
        )
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agente no encontrado")
    agent.status = AgentStatus.REVOKED
    agent.revoked_at = now
    await session.execute(
        update(AgentToken)
        .where(
            AgentToken.tenant_id == context.tenant_id,
            AgentToken.agent_id == agent_id,
            AgentToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    await session.execute(
        update(Device)
        .where(
            Device.tenant_id == context.tenant_id,
            Device.agent_id == agent_id,
        )
        .values(online=False)
    )
    session.add(
        AuditLog(
            tenant_id=context.tenant_id,
            user_id=context.user.id,
            agent_id=agent_id,
            action="agent.revoke",
            outcome="SUCCESS",
            metadata_json={},
            created_at=now,
        )
    )
    await session.commit()
    await agent_hub.disconnect(agent_id)
    await event_hub.publish(
        context.tenant_id, "agent.revoked", {"agent_id": str(agent_id)}
    )


def invalid_pairing_code() -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, "Código inválido o expirado")
