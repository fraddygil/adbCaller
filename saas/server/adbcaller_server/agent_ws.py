from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select, update

from adbcaller_protocol.devices import DeviceListMessage
from adbcaller_protocol.commands import CommandResultMessage
from adbcaller_protocol.envelope import MessageType
from adbcaller_protocol.events import (
    HeartbeatMessage,
    HelloMessage,
    HelloResultMessage,
    HelloResultPayload,
)
from adbcaller_protocol.messages import parse_message
from adbcaller_protocol.version import PROTOCOL_VERSION, negotiate_version
from adbcaller_server.agent_hub import agent_hub
from adbcaller_server.config import get_settings
from adbcaller_server.database import session_factory, set_tenant
from adbcaller_server.models import Agent, AgentToken, Call, ConferenceSession, Device, Tenant
from adbcaller_server.models.enums import AgentStatus, ConferenceState
from adbcaller_server.realtime import event_hub
from adbcaller_server.security import parse_scoped_secret, secret_hash


router = APIRouter(tags=["agent"])


@router.websocket("/agent/ws")
async def agent_websocket(websocket: WebSocket) -> None:
    authenticated = await authenticate_agent(websocket)
    if authenticated is None:
        await websocket.close(code=4401, reason="Token de agente inválido")
        return
    tenant_id, agent_id = authenticated
    await websocket.accept()
    try:
        raw_hello = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        hello = parse_message(raw_hello)
        if not isinstance(hello, HelloMessage) or hello.payload.agent_id != agent_id:
            await websocket.close(code=4400, reason="Hello inválido")
            return
        version = negotiate_version(
            hello.payload.minimum_protocol_version,
            hello.payload.maximum_protocol_version,
        )
        if version is None:
            await send_hello_result(websocket, False, None, "Protocolo incompatible")
            await websocket.close(code=4406, reason="Protocolo incompatible")
            return
        await send_hello_result(websocket, True, version, None)
        await mark_agent(tenant_id, agent_id, AgentStatus.ONLINE, hello.payload.agent_version)
        await agent_hub.connect(agent_id, websocket)
        from adbcaller_server.tasks import dispatch_agent

        await dispatch_agent(tenant_id, agent_id)

        while True:
            message = parse_message(await websocket.receive_text())
            if isinstance(message, HeartbeatMessage):
                if message.payload.agent_id != agent_id:
                    await websocket.close(code=4403, reason="agent_id inválido")
                    return
                await touch_agent(tenant_id, agent_id)
            elif isinstance(message, DeviceListMessage):
                await save_devices(tenant_id, agent_id, message)
            elif isinstance(message, CommandResultMessage):
                from adbcaller_server.tasks import apply_command_result

                await apply_command_result(tenant_id, agent_id, message)
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        if await agent_hub.disconnect(agent_id, websocket):
            from adbcaller_server.tasks import mark_inflight_uncertain

            await mark_inflight_uncertain(tenant_id, agent_id)
            await mark_agent(tenant_id, agent_id, AgentStatus.OFFLINE)


async def authenticate_agent(websocket: WebSocket) -> tuple[UUID, UUID] | None:
    authorization = websocket.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.casefold() != "bearer" or not token:
        return None
    try:
        tenant_id, agent_id = parse_scoped_secret(token, 2)
    except ValueError:
        return None

    settings = get_settings()
    async with session_factory() as session:
        await set_tenant(session, tenant_id)
        record = (
            await session.execute(
                select(AgentToken.id)
                .join(
                    Agent,
                    (Agent.tenant_id == AgentToken.tenant_id)
                    & (Agent.id == AgentToken.agent_id),
                )
                .where(
                    AgentToken.tenant_id == tenant_id,
                    AgentToken.agent_id == agent_id,
                    AgentToken.token_hash == secret_hash(token, settings.token_pepper),
                    AgentToken.revoked_at.is_(None),
                    (AgentToken.expires_at.is_(None))
                    | (AgentToken.expires_at > datetime.now(UTC)),
                    Agent.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()
    return (tenant_id, agent_id) if record is not None else None


async def send_hello_result(
    websocket: WebSocket, accepted: bool, version: int | None, error: str | None
) -> None:
    message = HelloResultMessage(
        version=PROTOCOL_VERSION,
        message_id=uuid4(),
        message_type=MessageType.HELLO_RESULT,
        timestamp=datetime.now(UTC),
        payload=HelloResultPayload(
            accepted=accepted,
            protocol_version=version,
            error=error,
        ),
    )
    await websocket.send_text(message.model_dump_json())


async def touch_agent(tenant_id: UUID, agent_id: UUID) -> None:
    async with session_factory() as session:
        await set_tenant(session, tenant_id)
        await session.execute(
            update(Agent)
            .where(Agent.tenant_id == tenant_id, Agent.id == agent_id)
            .values(last_seen_at=datetime.now(UTC))
        )
        await session.commit()


async def mark_agent(
    tenant_id: UUID,
    agent_id: UUID,
    state: AgentStatus,
    agent_version: str | None = None,
) -> None:
    values: dict[str, object] = {
        "status": state,
        "last_seen_at": datetime.now(UTC),
    }
    if agent_version is not None:
        values["agent_version"] = agent_version
    async with session_factory() as session:
        await set_tenant(session, tenant_id)
        await session.execute(
            update(Agent)
            .where(
                Agent.tenant_id == tenant_id,
                Agent.id == agent_id,
                Agent.revoked_at.is_(None),
            )
            .values(**values)
        )
        if state is AgentStatus.OFFLINE:
            await session.execute(
                update(Device)
                .where(Device.tenant_id == tenant_id, Device.agent_id == agent_id)
                .values(online=False)
            )
        await session.commit()
    await event_hub.publish(
        tenant_id, "agent.updated", {"agent_id": str(agent_id), "status": state.value}
    )


async def save_devices(
    tenant_id: UUID, agent_id: UUID, message: DeviceListMessage
) -> None:
    now = datetime.now(UTC)
    reported_ids = {item.device_id for item in message.payload.devices}
    async with session_factory() as session:
        await set_tenant(session, tenant_id)
        existing = {
            item.id: item
            for item in (
                await session.execute(
                    select(Device).where(
                        Device.tenant_id == tenant_id,
                        Device.agent_id == agent_id,
                    )
                )
            ).scalars()
        }
        max_devices = (
            await session.execute(
                select(Tenant.max_devices).where(Tenant.id == tenant_id)
            )
        ).scalar_one()
        allowed_ids = {
            device.id
            for device in sorted(existing.values(), key=lambda value: value.created_at)[
                :max_devices
            ]
        }
        for item in message.payload.devices:
            device = existing.get(item.device_id)
            if device is None:
                device = Device(
                    id=item.device_id,
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    adb_serial=item.local_serial,
                    adb_state=item.connection_state.value,
                )
                session.add(device)
                if len(allowed_ids) < max_devices:
                    allowed_ids.add(item.device_id)
            elif device.adb_serial != item.local_serial:
                continue
            device.model = item.model
            device.manufacturer = item.manufacturer
            device.android_version = item.android_version
            device.api_level = item.api_level
            device.one_ui_version = item.one_ui_version
            device.build_fingerprint = item.build_fingerprint
            device.adb_state = item.connection_state.value
            device.call_state = item.call_state.value
            device.call_count = item.call_count
            device.capabilities = {"actions": sorted(item.capabilities)}
            device.online = True
            device.blocked_by_limit = item.device_id not in allowed_ids
            device.last_seen_at = now
            if item.call_state.value == "IDLE":
                await session.execute(
                    update(ConferenceSession)
                    .where(
                        ConferenceSession.tenant_id == tenant_id,
                        ConferenceSession.device_id == item.device_id,
                        ConferenceSession.state == ConferenceState.ACTIVE,
                    )
                    .values(state=ConferenceState.DONE)
                )
                await session.execute(
                    update(Call)
                    .where(
                        Call.tenant_id == tenant_id,
                        Call.device_id == item.device_id,
                        Call.ended_at.is_(None),
                    )
                    .values(state="IDLE", result="ENDED", ended_at=now)
                )
            else:
                call_values: dict[str, object] = {"state": item.call_state.value}
                if item.call_state.value == "ACTIVE":
                    call_values["answered_at"] = func.coalesce(Call.answered_at, now)
                await session.execute(
                    update(Call)
                    .where(
                        Call.tenant_id == tenant_id,
                        Call.device_id == item.device_id,
                        Call.ended_at.is_(None),
                    )
                    .values(**call_values)
                )
        await session.execute(
            update(Device)
            .where(
                Device.tenant_id == tenant_id,
                Device.agent_id == agent_id,
                Device.id.not_in(reported_ids),
            )
            .values(online=False)
        )
        await session.commit()
    await event_hub.publish(
        tenant_id,
        "devices.updated",
        {"agent_id": str(agent_id), "generation": message.payload.generation},
    )
