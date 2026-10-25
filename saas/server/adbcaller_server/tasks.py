from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import Field
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from adbcaller_protocol.commands import (
    AddCallCommand,
    CallCommand,
    CommandAction,
    CommandMessage,
    CommandMessagePayload,
    CommandPayload,
    CommandResultMessage,
    HangupAllCommand,
)
from adbcaller_protocol.envelope import MessageType
from adbcaller_protocol.version import PROTOCOL_VERSION
from adbcaller_server.agent_hub import agent_hub
from adbcaller_server.auth import ApiModel, AuthContext, current_user, valid_csrf
from adbcaller_server.database import get_session, session_factory, set_tenant
from adbcaller_server.models import Agent, AuditLog, Call, Device, NumberEntry, Task, Tenant
from adbcaller_server.models.enums import NumberState, TaskState
from adbcaller_server.rate_limit import enforce_rate_limit
from adbcaller_server.realtime import event_hub


router = APIRouter(tags=["tasks"])
ACTIVE_STATES = (TaskState.SENT, TaskState.RUNNING, TaskState.UNCERTAIN)


class CreateTaskRequest(ApiModel):
    command: CommandPayload


class TaskResponse(ApiModel):
    id: UUID
    agent_id: UUID
    device_id: UUID | None
    action: str
    state: TaskState
    created_at: datetime
    timeout_at: datetime
    result: dict[str, object]
    error_code: str | None
    error_message: str | None


@router.post(
    "/devices/{device_id}/commands",
    response_model=TaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_device_command(
    device_id: UUID,
    request: CreateTaskRequest,
    context: AuthContext = Depends(valid_csrf),
    session: AsyncSession = Depends(get_session),
) -> TaskResponse:
    if request.command.action in {
        CommandAction.LIST_DEVICES,
        CommandAction.HANGUP_ALL,
    }:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "La acción no es de dispositivo")
    device = (
        await session.execute(
            select(Device).where(
                Device.tenant_id == context.tenant_id,
                Device.id == device_id,
            )
        )
    ).scalar_one_or_none()
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dispositivo no encontrado")
    tenant_limits = (
        await session.execute(
            select(Tenant.command_rate_limit, Tenant.max_concurrent_calls).where(
                Tenant.id == context.tenant_id
            )
        )
    ).one()
    await enforce_rate_limit(
        context.tenant_id,
        f"commands:{context.user.id}",
        tenant_limits.command_rate_limit,
    )
    validate_device_command(device, request.command)
    if request.command.action is CommandAction.CALL:
        busy_devices = (
            await session.execute(
                select(Device.id).where(
                    Device.tenant_id == context.tenant_id,
                    Device.online.is_(True),
                    Device.blocked_by_limit.is_(False),
                    Device.call_state != "IDLE",
                )
            )
        ).all()
        pending_calls = (
            await session.execute(
                select(Task.id).where(
                    Task.tenant_id == context.tenant_id,
                    Task.action == CommandAction.CALL.value,
                    Task.state.in_(
                        (TaskState.QUEUED, TaskState.SENT, TaskState.RUNNING)
                    ),
                )
            )
        ).all()
        if len(busy_devices) + len(pending_calls) >= tenant_limits.max_concurrent_calls:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Se alcanzó el límite de llamadas simultáneas del plan",
            )
    if request.command.action is CommandAction.HANGUP:
        from adbcaller_server.conferences import cancel_device_conferences

        await cancel_device_conferences(session, context.tenant_id, device.id)

    now = datetime.now(UTC)
    task = Task(
        tenant_id=context.tenant_id,
        agent_id=device.agent_id,
        device_id=device.id,
        created_by_user_id=context.user.id,
        action=request.command.action.value,
        payload=request.command.model_dump(mode="json"),
        state=TaskState.QUEUED,
        idempotency_key=str(uuid4()),
        timeout_at=now + timedelta(seconds=30),
    )
    session.add(task)
    await session.flush()
    if isinstance(request.command, (CallCommand, AddCallCommand)):
        session.add(
            Call(
                tenant_id=context.tenant_id,
                agent_id=device.agent_id,
                device_id=device.id,
                task_id=task.id,
                normalized_number=request.command.parameters.number,
                state="DIALING",
                result="STARTED",
                started_at=now,
            )
        )
    session.add(
        AuditLog(
            tenant_id=context.tenant_id,
            user_id=context.user.id,
            agent_id=device.agent_id,
            device_id=device.id,
            task_id=task.id,
            action=f"command.{request.command.action.value.casefold()}",
            outcome="QUEUED",
            metadata_json={},
            created_at=now,
        )
    )
    await session.commit()
    await dispatch_agent(context.tenant_id, device.agent_id)
    await set_tenant(session, context.tenant_id)
    await session.refresh(task)
    await event_hub.publish(
        context.tenant_id, "task.updated", {"task_id": str(task.id), "state": task.state.value}
    )
    return task_response(task)


@router.post(
    "/agents/{agent_id}/hangup-all",
    response_model=TaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_hangup_all(
    agent_id: UUID,
    context: AuthContext = Depends(valid_csrf),
    session: AsyncSession = Depends(get_session),
) -> TaskResponse:
    agent_exists = (
        await session.execute(
            select(Agent.id).where(
                Agent.tenant_id == context.tenant_id,
                Agent.id == agent_id,
            )
        )
    ).scalar_one_or_none()
    if agent_exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agente no encontrado")
    now = datetime.now(UTC)
    from adbcaller_server.conferences import cancel_device_conferences

    device_ids = list(
        (
            await session.execute(
                select(Device.id).where(
                    Device.tenant_id == context.tenant_id,
                    Device.agent_id == agent_id,
                )
            )
        ).scalars()
    )
    for device_id in device_ids:
        await cancel_device_conferences(session, context.tenant_id, device_id)
    await cancel_queued_tasks(
        session,
        context.tenant_id,
        now,
        "Cancelada por Colgar todos los dispositivos del agente",
        agent_id,
    )
    command = HangupAllCommand()
    task = Task(
        tenant_id=context.tenant_id,
        agent_id=agent_id,
        device_id=None,
        created_by_user_id=context.user.id,
        action=command.action.value,
        payload=command.model_dump(mode="json"),
        state=TaskState.QUEUED,
        priority=0,
        idempotency_key=str(uuid4()),
        timeout_at=now + timedelta(seconds=30),
    )
    session.add(task)
    await session.flush()
    session.add(
        AuditLog(
            tenant_id=context.tenant_id,
            user_id=context.user.id,
            agent_id=agent_id,
            task_id=task.id,
            action="command.hangup_all",
            outcome="QUEUED",
            metadata_json={},
            created_at=now,
        )
    )
    await session.commit()
    await dispatch_agent(context.tenant_id, agent_id)
    await set_tenant(session, context.tenant_id)
    await session.refresh(task)
    await event_hub.publish(
        context.tenant_id, "task.updated", {"task_id": str(task.id), "state": task.state.value}
    )
    return task_response(task)


@router.post(
    "/hangup-all",
    response_model=list[TaskResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_global_hangup_all(
    context: AuthContext = Depends(valid_csrf),
    session: AsyncSession = Depends(get_session),
) -> list[TaskResponse]:
    agents = list(
        (
            await session.execute(
                select(Agent).where(
                    Agent.tenant_id == context.tenant_id,
                    Agent.revoked_at.is_(None),
                )
            )
        ).scalars()
    )
    if not agents:
        raise HTTPException(status.HTTP_409_CONFLICT, "No hay agentes disponibles")
    from adbcaller_server.conferences import cancel_all_conferences

    await cancel_all_conferences(session, context.tenant_id)
    now = datetime.now(UTC)
    await cancel_queued_tasks(
        session, context.tenant_id, now, "Cancelada por Colgar todos"
    )
    command = HangupAllCommand()
    tasks: list[Task] = []
    for agent in agents:
        task = Task(
            tenant_id=context.tenant_id,
            agent_id=agent.id,
            device_id=None,
            created_by_user_id=context.user.id,
            action=command.action.value,
            payload=command.model_dump(mode="json"),
            state=TaskState.QUEUED,
            priority=0,
            idempotency_key=str(uuid4()),
            timeout_at=now + timedelta(seconds=30),
        )
        session.add(task)
        tasks.append(task)
    await session.flush()
    for task in tasks:
        session.add(
            AuditLog(
                tenant_id=context.tenant_id,
                user_id=context.user.id,
                agent_id=task.agent_id,
                task_id=task.id,
                action="command.hangup_all",
                outcome="QUEUED",
                metadata_json={"scope": "tenant"},
                created_at=now,
            )
        )
    await session.commit()
    await asyncio.gather(
        *(dispatch_agent(context.tenant_id, agent.id) for agent in agents)
    )
    for task in tasks:
        await set_tenant(session, context.tenant_id)
        await session.refresh(task)
    await event_hub.publish(context.tenant_id, "hangup_all.queued", {})
    return [task_response(task) for task in tasks]


async def cancel_queued_tasks(
    session: AsyncSession,
    tenant_id: UUID,
    now: datetime,
    reason: str,
    agent_id: UUID | None = None,
) -> None:
    filters = [Task.tenant_id == tenant_id, Task.state == TaskState.QUEUED]
    if agent_id is not None:
        filters.append(Task.agent_id == agent_id)
    cancelled_task_ids = select(Task.id).where(*filters)
    await session.execute(
        update(NumberEntry)
        .where(
            NumberEntry.tenant_id == tenant_id,
            NumberEntry.task_id.in_(cancelled_task_ids),
        )
        .values(state=NumberState.CANCELLED, error=reason)
    )
    await session.execute(
        update(Call)
        .where(
            Call.tenant_id == tenant_id,
            Call.task_id.in_(cancelled_task_ids),
            Call.ended_at.is_(None),
        )
        .values(
            state="CANCELLED",
            result="CANCELLED",
            error=reason,
            ended_at=now,
        )
    )
    await session.execute(
        update(Task)
        .where(*filters)
        .values(
            state=TaskState.CANCELLED,
            finished_at=now,
            error_code="HANGUP_ALL",
            error_message=reason,
        )
    )


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: UUID,
    context: AuthContext = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> TaskResponse:
    task = (
        await session.execute(
            select(Task).where(Task.tenant_id == context.tenant_id, Task.id == task_id)
        )
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tarea no encontrada")
    return task_response(task)


async def dispatch_agent(tenant_id: UUID, agent_id: UUID) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        await set_tenant(session, tenant_id)
        await session.execute(
            update(Task)
            .where(
                Task.tenant_id == tenant_id,
                Task.agent_id == agent_id,
                Task.state == TaskState.QUEUED,
                Task.timeout_at <= now,
            )
            .values(state=TaskState.EXPIRED, finished_at=now)
        )
        blocked_devices = select(Task.device_id).where(
            Task.tenant_id == tenant_id,
            Task.agent_id == agent_id,
            Task.state.in_(ACTIVE_STATES),
            Task.device_id.is_not(None),
        )
        agent_command_active = (
            await session.execute(
                select(Task.id).where(
                    Task.tenant_id == tenant_id,
                    Task.agent_id == agent_id,
                    Task.device_id.is_(None),
                    Task.state.in_(ACTIVE_STATES),
                )
            )
        ).first() is not None
        queued = (
            await session.execute(
                select(Task)
                .where(
                    Task.tenant_id == tenant_id,
                    Task.agent_id == agent_id,
                    Task.state == TaskState.QUEUED,
                    Task.timeout_at > now,
                    or_(
                        Task.device_id.is_(None),
                        Task.device_id.not_in(blocked_devices),
                    ),
                )
                .order_by(Task.priority, Task.created_at)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
        sent_devices: set[UUID] = set()
        sent_agent_command = False
        for task in queued:
            if task.device_id is None:
                if agent_command_active or sent_agent_command:
                    continue
                sent_agent_command = True
            elif task.device_id in sent_devices:
                continue
            message = command_message(task)
            if not await agent_hub.send(agent_id, message):
                break
            task.state = TaskState.SENT
            task.sent_at = now
            task.attempts += 1
            sent_devices.add(task.device_id)
            if task.device_id is None:
                break
        await session.commit()


async def apply_command_result(
    tenant_id: UUID, agent_id: UUID, message: CommandResultMessage
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        await set_tenant(session, tenant_id)
        task = (
            await session.execute(
                select(Task).where(
                    Task.tenant_id == tenant_id,
                    Task.agent_id == agent_id,
                    Task.id == message.payload.command_id,
                )
            )
        ).scalar_one_or_none()
        if task is None or task.state in {
            TaskState.DONE,
            TaskState.FAILED,
            TaskState.CANCELLED,
            TaskState.EXPIRED,
        }:
            return
        if message.payload.state.value == "RUNNING":
            task.state = TaskState.RUNNING
            task.started_at = now
        else:
            task.state = TaskState(message.payload.state.value)
            task.finished_at = now
            task.result = message.payload.result
            task.error_code = message.payload.error_code
            task.error_message = message.payload.error_message
            number_state = (
                NumberState.COMPLETED
                if task.state is TaskState.DONE
                else NumberState.FAILED
            )
            await session.execute(
                update(NumberEntry)
                .where(
                    NumberEntry.tenant_id == tenant_id,
                    NumberEntry.task_id == task.id,
                )
                .values(
                    state=number_state,
                    error=message.payload.error_message or "",
                )
            )
            await session.execute(
                update(Call)
                .where(
                    Call.tenant_id == tenant_id,
                    Call.task_id == task.id,
                )
                .values(
                    state=str(message.payload.result.get("state", task.state.value)),
                    result=task.state.value,
                    error=message.payload.error_message,
                    ended_at=now if task.state is TaskState.FAILED else None,
                )
            )
        await session.commit()
        state = task.state.value
        task_id = task.id
    await event_hub.publish(
        tenant_id, "task.updated", {"task_id": str(task_id), "state": state}
    )
    if message.payload.state.value != "RUNNING":
        await dispatch_agent(tenant_id, agent_id)


async def mark_inflight_uncertain(tenant_id: UUID, agent_id: UUID) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        await set_tenant(session, tenant_id)
        await session.execute(
            update(Task)
            .where(
                Task.tenant_id == tenant_id,
                Task.agent_id == agent_id,
                Task.state.in_((TaskState.SENT, TaskState.RUNNING)),
            )
            .values(
                state=TaskState.UNCERTAIN,
                finished_at=now,
                error_code="AGENT_DISCONNECTED",
                error_message="El agente se desconectó durante la ejecución; no se reintentó",
            )
        )
        uncertain_tasks = select(Task.id).where(
            Task.tenant_id == tenant_id,
            Task.agent_id == agent_id,
            Task.state == TaskState.UNCERTAIN,
        )
        await session.execute(
            update(NumberEntry)
            .where(
                NumberEntry.tenant_id == tenant_id,
                NumberEntry.task_id.in_(uncertain_tasks),
            )
            .values(
                state=NumberState.FAILED,
                error="Resultado incierto por desconexión del agente",
            )
        )
        await session.execute(
            update(Call)
            .where(
                Call.tenant_id == tenant_id,
                Call.agent_id == agent_id,
                Call.ended_at.is_(None),
            )
            .values(
                state="UNKNOWN",
                result="DISCONNECTED",
                error="El agente se desconectó",
                ended_at=now,
            )
        )
        await session.commit()


def command_message(task: Task) -> CommandMessage:
    return CommandMessage(
        version=PROTOCOL_VERSION,
        message_id=uuid4(),
        message_type=MessageType.COMMAND,
        timestamp=datetime.now(UTC),
        payload=CommandMessagePayload(
            command_id=task.id,
            agent_id=task.agent_id,
            device_id=task.device_id,
            created_at=task.created_at,
            expires_at=task.timeout_at,
            command=task.payload,
        ),
    )


def validate_device_command(device: Device, command: CommandPayload) -> None:
    if device.blocked_by_limit:
        raise HTTPException(status.HTTP_409_CONFLICT, "Dispositivo bloqueado por el límite del plan")
    if not device.online:
        raise HTTPException(status.HTTP_409_CONFLICT, "Dispositivo desconectado")
    capabilities = set(device.capabilities.get("actions", []))
    if command.action.value not in capabilities:
        raise HTTPException(status.HTTP_409_CONFLICT, "Acción no soportada por el dispositivo")
    if command.action is CommandAction.CALL and device.call_state != "IDLE":
        raise HTTPException(status.HTTP_409_CONFLICT, "CALL requiere estado IDLE")
    if command.action is CommandAction.ADD_CALL and device.call_state != "ACTIVE":
        raise HTTPException(status.HTTP_409_CONFLICT, "ADD_CALL requiere estado ACTIVE")
    if command.action is CommandAction.MERGE and device.call_state == "IDLE":
        raise HTTPException(status.HTTP_409_CONFLICT, "No hay llamadas para combinar")
    if isinstance(command, (CallCommand, AddCallCommand)) and not command.parameters.number:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Número requerido")


def task_response(task: Task) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        agent_id=task.agent_id,
        device_id=task.device_id,
        action=task.action,
        state=task.state,
        created_at=task.created_at,
        timeout_at=task.timeout_at,
        result=task.result,
        error_code=task.error_code,
        error_message=task.error_message,
    )
