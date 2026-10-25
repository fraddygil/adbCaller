from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from adbcaller_protocol.commands import (
    AddCallCommand,
    CallCommand,
    CallParameters,
    CommandAction,
    EnableSpeakerCommand,
    MergeCommand,
)
from adbcaller_server.auth import ApiModel, AuthContext, current_user, valid_csrf
from adbcaller_server.database import get_session, session_factory, set_tenant
from adbcaller_server.models import (
    AuditLog,
    Call,
    ConferenceParticipant,
    ConferenceSession,
    Device,
    NumberEntry,
    NumberList,
    Task,
    Tenant,
)
from adbcaller_server.models.enums import (
    ConferenceState,
    NumberState,
    ParticipantState,
    TaskState,
)
from adbcaller_server.realtime import event_hub
from adbcaller_server.settings_api import load_automation_settings
from adbcaller_server.tasks import dispatch_agent


router = APIRouter(tags=["conferences"])
conference_tasks: dict[UUID, asyncio.Task[None]] = {}


class ConferenceResponse(ApiModel):
    id: UUID
    device_id: UUID
    state: ConferenceState
    total: int
    progress: int
    error: str
    created_at: datetime


@router.post(
    "/number-lists/{number_list_id}/devices/{device_id}/conference",
    response_model=ConferenceResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_device_conference(
    number_list_id: UUID,
    device_id: UUID,
    context: AuthContext = Depends(valid_csrf),
    session: AsyncSession = Depends(get_session),
) -> ConferenceResponse:
    conference = await create_conference(
        session, context, number_list_id, device_id
    )
    launch_conference(context.tenant_id, conference.id)
    return conference_response(conference)


@router.post(
    "/number-lists/{number_list_id}/conference-all",
    response_model=list[ConferenceResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_all_conferences(
    number_list_id: UUID,
    context: AuthContext = Depends(valid_csrf),
    session: AsyncSession = Depends(get_session),
) -> list[ConferenceResponse]:
    number_list = await get_number_list(session, context.tenant_id, number_list_id)
    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == context.tenant_id))
    ).scalar_one()
    devices = list(
        (
            await session.execute(
                select(Device)
                .where(
                    Device.tenant_id == context.tenant_id,
                    Device.online.is_(True),
                    Device.blocked_by_limit.is_(False),
                    Device.call_state == "IDLE",
                )
                .order_by(Device.created_at)
                .limit(tenant.max_concurrent_calls)
            )
        ).scalars()
    )
    devices = [
        device
        for device in devices
        if {"CALL", "ADD_CALL", "MERGE"}
        <= set(device.capabilities.get("actions", []))
    ]
    if not devices:
        raise HTTPException(status.HTTP_409_CONFLICT, "No hay dispositivos disponibles")
    await ensure_no_active_conferences(
        session, context.tenant_id, [device.id for device in devices]
    )
    settings = await load_automation_settings(context.tenant_id)
    numbers = list(
        (
            await session.execute(
                select(NumberEntry)
                .where(
                    NumberEntry.tenant_id == context.tenant_id,
                    NumberEntry.number_list_id == number_list.id,
                    NumberEntry.state == NumberState.PENDING,
                )
                .order_by(NumberEntry.position)
                .limit(len(devices) * settings.max_conference_calls)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    conferences: list[ConferenceSession] = []
    offset = 0
    for device in devices:
        chunk = numbers[offset : offset + settings.max_conference_calls]
        offset += len(chunk)
        if len(chunk) < 2:
            break
        conferences.append(
            await persist_conference(session, context, device, chunk)
        )
    if not conferences:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Se necesitan al menos dos números pendientes por conferencia",
        )
    session.add(
        AuditLog(
            tenant_id=context.tenant_id,
            user_id=context.user.id,
            action="conference_all.start",
            outcome="QUEUED",
            metadata_json={
                "number_list_id": str(number_list_id),
                "sessions": len(conferences),
            },
            created_at=datetime.now(UTC),
        )
    )
    await session.commit()
    for conference in conferences:
        launch_conference(context.tenant_id, conference.id)
    return [conference_response(value) for value in conferences]


@router.get("/conferences", response_model=list[ConferenceResponse])
async def list_conferences(
    context: AuthContext = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ConferenceResponse]:
    values = (
        await session.execute(
            select(ConferenceSession)
            .where(ConferenceSession.tenant_id == context.tenant_id)
            .order_by(ConferenceSession.created_at.desc())
            .limit(100)
        )
    ).scalars()
    return [conference_response(value) for value in values]


@router.post("/conferences/{conference_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_conference_endpoint(
    conference_id: UUID,
    context: AuthContext = Depends(valid_csrf),
    session: AsyncSession = Depends(get_session),
) -> None:
    changed = await session.execute(
        update(ConferenceSession)
        .where(
            ConferenceSession.tenant_id == context.tenant_id,
            ConferenceSession.id == conference_id,
            ConferenceSession.state.not_in(
                (
                    ConferenceState.DONE,
                    ConferenceState.FAILED,
                    ConferenceState.CANCELLED,
                )
            ),
        )
        .values(state=ConferenceState.CANCELLED, error="Cancelada por el usuario")
        .returning(ConferenceSession.id)
    )
    if changed.scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conferencia activa no encontrada")
    await session.commit()
    task = conference_tasks.get(conference_id)
    if task is not None:
        task.cancel()
    await event_hub.publish(
        context.tenant_id,
        "conference.updated",
        {"conference_id": str(conference_id), "state": ConferenceState.CANCELLED.value},
    )


@router.post("/conferences/{conference_id}/resume", response_model=ConferenceResponse)
async def resume_conference(
    conference_id: UUID,
    context: AuthContext = Depends(valid_csrf),
    session: AsyncSession = Depends(get_session),
) -> ConferenceResponse:
    conference = (
        await session.execute(
            select(ConferenceSession).where(
                ConferenceSession.tenant_id == context.tenant_id,
                ConferenceSession.id == conference_id,
                ConferenceSession.state == ConferenceState.PAUSED,
            )
        )
    ).scalar_one_or_none()
    if conference is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "La conferencia no está pausada")
    conference.state = ConferenceState.QUEUED
    conference.error = ""
    await session.commit()
    launch_conference(context.tenant_id, conference.id)
    return conference_response(conference)


async def create_conference(
    session: AsyncSession,
    context: AuthContext,
    number_list_id: UUID,
    device_id: UUID,
) -> ConferenceSession:
    await get_number_list(session, context.tenant_id, number_list_id)
    device = (
        await session.execute(
            select(Device).where(
                Device.tenant_id == context.tenant_id,
                Device.id == device_id,
                Device.online.is_(True),
                Device.blocked_by_limit.is_(False),
                Device.call_state == "IDLE",
            )
        )
    ).scalar_one_or_none()
    if device is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "El dispositivo no está disponible e IDLE")
    required = {"CALL", "ADD_CALL", "MERGE"}
    if not required <= set(device.capabilities.get("actions", [])):
        raise HTTPException(status.HTTP_409_CONFLICT, "El dispositivo no soporta conferencias")
    await ensure_no_active_conferences(session, context.tenant_id, [device_id])
    automation = await load_automation_settings(context.tenant_id)
    numbers = list(
        (
            await session.execute(
                select(NumberEntry)
                .where(
                    NumberEntry.tenant_id == context.tenant_id,
                    NumberEntry.number_list_id == number_list_id,
                    NumberEntry.state == NumberState.PENDING,
                )
                .order_by(NumberEntry.position)
                .limit(automation.max_conference_calls)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    if len(numbers) < 2:
        raise HTTPException(status.HTTP_409_CONFLICT, "Se necesitan al menos dos números pendientes")
    conference = await persist_conference(session, context, device, numbers)
    await session.commit()
    return conference


async def persist_conference(
    session: AsyncSession,
    context: AuthContext,
    device: Device,
    numbers: list[NumberEntry],
) -> ConferenceSession:
    conference = ConferenceSession(
        tenant_id=context.tenant_id,
        device_id=device.id,
        created_by_user_id=context.user.id,
        state=ConferenceState.QUEUED,
        total=len(numbers),
    )
    session.add(conference)
    await session.flush()
    for position, number in enumerate(numbers):
        session.add(
            ConferenceParticipant(
                tenant_id=context.tenant_id,
                conference_session_id=conference.id,
                position=position,
                normalized_number=number.normalized_number,
                state=ParticipantState.PENDING,
                number_entry_id=number.id,
            )
        )
        number.state = NumberState.ASSIGNED
        number.assigned_device_id = device.id
    return conference


def launch_conference(tenant_id: UUID, conference_id: UUID) -> None:
    current = conference_tasks.get(conference_id)
    if current is not None and not current.done():
        return
    task = asyncio.create_task(
        run_conference(tenant_id, conference_id),
        name=f"conference-{conference_id}",
    )
    conference_tasks[conference_id] = task
    task.add_done_callback(lambda _: conference_tasks.pop(conference_id, None))


async def run_conference(tenant_id: UUID, conference_id: UUID) -> None:
    try:
        conference, device, participants = await load_conference(tenant_id, conference_id)
        await set_conference_state(tenant_id, conference_id, ConferenceState.RUNNING)
        has_active = device.call_state == "ACTIVE" and conference.progress > 0
        for participant in participants:
            if participant.state is not ParticipantState.PENDING:
                continue
            conference, device, _ = await load_conference(tenant_id, conference_id)
            if conference.state is ConferenceState.CANCELLED:
                return
            baseline = device.call_count
            command = (
                AddCallCommand(parameters=CallParameters(number=participant.normalized_number))
                if has_active
                else CallCommand(parameters=CallParameters(number=participant.normalized_number))
            )
            await set_participant_state(
                tenant_id, participant.id, ParticipantState.DIALING
            )
            task_id = await enqueue_conference_command(
                tenant_id, conference, device, command
            )
            await set_participant_task(tenant_id, participant.id, task_id)
            task = await wait_for_task(tenant_id, task_id)
            if task.state is not TaskState.DONE:
                await fail_participant(tenant_id, participant, task.error_message or "Falló la marcación")
                raise RuntimeError(task.error_message or "Falló la marcación")
            await wait_for_new_active_call(tenant_id, conference_id, baseline)
            await set_participant_state(tenant_id, participant.id, ParticipantState.ACTIVE)

            if not has_active:
                automation = await load_automation_settings(tenant_id)
                if (
                    automation.auto_speaker
                    and CommandAction.ENABLE_SPEAKER.value
                    in device.capabilities.get("actions", [])
                ):
                    speaker_task = await enqueue_conference_command(
                        tenant_id, conference, device, EnableSpeakerCommand()
                    )
                    await require_done(tenant_id, speaker_task, "No se pudo activar Altavoz")
            else:
                await merge_with_retries(tenant_id, conference, device)
                await set_participant_state(
                    tenant_id, participant.id, ParticipantState.MERGED
                )
            has_active = True
            await complete_participant(tenant_id, conference_id, participant)

        conference, _, _ = await load_conference(tenant_id, conference_id)
        if conference.progress == 0:
            raise RuntimeError("Ningún número llegó a ACTIVE")
        await set_conference_state(tenant_id, conference_id, ConferenceState.ACTIVE)
    except asyncio.CancelledError:
        await pause_if_running(tenant_id, conference_id)
        raise
    except Exception as exc:
        await set_conference_state(
            tenant_id, conference_id, ConferenceState.FAILED, str(exc)
        )


async def merge_with_retries(
    tenant_id: UUID, conference: ConferenceSession, device: Device
) -> None:
    last_error = "No se pudo combinar"
    automation = await load_automation_settings(tenant_id)
    for _ in range(automation.conference_merge_attempts):
        task_id = await enqueue_conference_command(
            tenant_id, conference, device, MergeCommand()
        )
        task = await wait_for_task(tenant_id, task_id)
        if task.state is TaskState.DONE:
            return
        last_error = task.error_message or last_error
        await asyncio.sleep(2)
    raise RuntimeError(last_error)


async def enqueue_conference_command(
    tenant_id: UUID,
    conference: ConferenceSession,
    device: Device,
    command,
) -> UUID:
    now = datetime.now(UTC)
    task = Task(
        tenant_id=tenant_id,
        agent_id=device.agent_id,
        device_id=device.id,
        created_by_user_id=conference.created_by_user_id,
        action=command.action.value,
        payload=command.model_dump(mode="json"),
        state=TaskState.QUEUED,
        idempotency_key=str(uuid4()),
        timeout_at=now + timedelta(seconds=60),
    )
    async with session_factory() as session:
        await set_tenant(session, tenant_id)
        session.add(task)
        await session.flush()
        if isinstance(command, (CallCommand, AddCallCommand)):
            session.add(
                Call(
                    tenant_id=tenant_id,
                    agent_id=device.agent_id,
                    device_id=device.id,
                    task_id=task.id,
                    normalized_number=command.parameters.number,
                    state="DIALING",
                    result="STARTED",
                    started_at=now,
                )
            )
        await session.commit()
    await dispatch_agent(tenant_id, device.agent_id)
    return task.id


async def wait_for_task(tenant_id: UUID, task_id: UUID) -> Task:
    deadline = asyncio.get_running_loop().time() + 65
    while asyncio.get_running_loop().time() < deadline:
        async with session_factory() as session:
            await set_tenant(session, tenant_id)
            task = (
                await session.execute(
                    select(Task).where(Task.tenant_id == tenant_id, Task.id == task_id)
                )
            ).scalar_one()
            if task.state in {
                TaskState.DONE,
                TaskState.FAILED,
                TaskState.CANCELLED,
                TaskState.EXPIRED,
                TaskState.UNCERTAIN,
            }:
                return task
        await asyncio.sleep(1)
    raise TimeoutError("Timeout esperando el resultado del agente")


async def require_done(tenant_id: UUID, task_id: UUID, message: str) -> None:
    task = await wait_for_task(tenant_id, task_id)
    if task.state is not TaskState.DONE:
        raise RuntimeError(task.error_message or message)


async def wait_for_new_active_call(
    tenant_id: UUID, conference_id: UUID, baseline: int
) -> None:
    automation = await load_automation_settings(tenant_id)
    deadline = asyncio.get_running_loop().time() + automation.conference_call_timeout_seconds
    saw_new_call = False
    while asyncio.get_running_loop().time() < deadline:
        conference, device, _ = await load_conference(tenant_id, conference_id)
        if conference.state is ConferenceState.CANCELLED:
            raise asyncio.CancelledError
        if device.call_count > baseline:
            saw_new_call = True
        if saw_new_call and device.call_state == "ACTIVE":
            return
        if saw_new_call and device.call_count <= baseline:
            raise RuntimeError("La llamada terminó o fue rechazada")
        await asyncio.sleep(1)
    raise TimeoutError("El número no llegó a ACTIVE dentro del tiempo permitido")


async def load_conference(
    tenant_id: UUID, conference_id: UUID
) -> tuple[ConferenceSession, Device, list[ConferenceParticipant]]:
    async with session_factory() as session:
        await set_tenant(session, tenant_id)
        conference = (
            await session.execute(
                select(ConferenceSession).where(
                    ConferenceSession.tenant_id == tenant_id,
                    ConferenceSession.id == conference_id,
                )
            )
        ).scalar_one()
        device = (
            await session.execute(
                select(Device).where(
                    Device.tenant_id == tenant_id,
                    Device.id == conference.device_id,
                )
            )
        ).scalar_one()
        participants = list(
            (
                await session.execute(
                    select(ConferenceParticipant)
                    .where(
                        ConferenceParticipant.tenant_id == tenant_id,
                        ConferenceParticipant.conference_session_id == conference_id,
                    )
                    .order_by(ConferenceParticipant.position)
                )
            ).scalars()
        )
        session.expunge(conference)
        session.expunge(device)
        for participant in participants:
            session.expunge(participant)
        return conference, device, participants


async def set_conference_state(
    tenant_id: UUID,
    conference_id: UUID,
    state: ConferenceState,
    error: str = "",
) -> None:
    async with session_factory() as session:
        await set_tenant(session, tenant_id)
        await session.execute(
            update(ConferenceSession)
            .where(
                ConferenceSession.tenant_id == tenant_id,
                ConferenceSession.id == conference_id,
            )
            .values(state=state, error=error)
        )
        await session.commit()
    await event_hub.publish(
        tenant_id,
        "conference.updated",
        {"conference_id": str(conference_id), "state": state.value},
    )


async def set_participant_state(
    tenant_id: UUID, participant_id: UUID, state: ParticipantState
) -> None:
    async with session_factory() as session:
        await set_tenant(session, tenant_id)
        await session.execute(
            update(ConferenceParticipant)
            .where(
                ConferenceParticipant.tenant_id == tenant_id,
                ConferenceParticipant.id == participant_id,
            )
            .values(state=state)
        )
        await session.commit()


async def set_participant_task(
    tenant_id: UUID, participant_id: UUID, task_id: UUID
) -> None:
    async with session_factory() as session:
        await set_tenant(session, tenant_id)
        await session.execute(
            update(ConferenceParticipant)
            .where(
                ConferenceParticipant.tenant_id == tenant_id,
                ConferenceParticipant.id == participant_id,
            )
            .values(task_id=task_id)
        )
        await session.commit()


async def complete_participant(
    tenant_id: UUID,
    conference_id: UUID,
    participant: ConferenceParticipant,
) -> None:
    async with session_factory() as session:
        await set_tenant(session, tenant_id)
        await session.execute(
            update(ConferenceSession)
            .where(
                ConferenceSession.tenant_id == tenant_id,
                ConferenceSession.id == conference_id,
            )
            .values(progress=ConferenceSession.progress + 1)
        )
        if participant.number_entry_id is not None:
            await session.execute(
                update(NumberEntry)
                .where(
                    NumberEntry.tenant_id == tenant_id,
                    NumberEntry.id == participant.number_entry_id,
                )
                .values(state=NumberState.COMPLETED, error="")
            )
        await session.commit()


async def fail_participant(
    tenant_id: UUID, participant: ConferenceParticipant, error: str
) -> None:
    async with session_factory() as session:
        await set_tenant(session, tenant_id)
        await session.execute(
            update(ConferenceParticipant)
            .where(
                ConferenceParticipant.tenant_id == tenant_id,
                ConferenceParticipant.id == participant.id,
            )
            .values(state=ParticipantState.FAILED, error=error)
        )
        if participant.number_entry_id is not None:
            await session.execute(
                update(NumberEntry)
                .where(
                    NumberEntry.tenant_id == tenant_id,
                    NumberEntry.id == participant.number_entry_id,
                )
                .values(state=NumberState.FAILED, error=error)
            )
        await session.commit()


async def pause_if_running(tenant_id: UUID, conference_id: UUID) -> None:
    async with session_factory() as session:
        await set_tenant(session, tenant_id)
        await session.execute(
            update(ConferenceSession)
            .where(
                ConferenceSession.tenant_id == tenant_id,
                ConferenceSession.id == conference_id,
                ConferenceSession.state.in_(
                    (ConferenceState.QUEUED, ConferenceState.RUNNING)
                ),
            )
            .values(state=ConferenceState.PAUSED, error="Ejecución interrumpida")
        )
        await session.commit()


async def cancel_device_conferences(
    session: AsyncSession, tenant_id: UUID, device_id: UUID
) -> None:
    await cancel_conferences(
        session,
        tenant_id,
        "Cancelada por Colgar",
        device_id,
    )


async def cancel_all_conferences(session: AsyncSession, tenant_id: UUID) -> None:
    await cancel_conferences(session, tenant_id, "Cancelada por Colgar todos")


async def cancel_conferences(
    session: AsyncSession,
    tenant_id: UUID,
    reason: str,
    device_id: UUID | None = None,
) -> None:
    active_states = (
        ConferenceState.QUEUED,
        ConferenceState.RUNNING,
        ConferenceState.PAUSED,
        ConferenceState.ACTIVE,
    )
    filters = [
        ConferenceSession.tenant_id == tenant_id,
        ConferenceSession.state.in_(active_states),
    ]
    if device_id is not None:
        filters.append(ConferenceSession.device_id == device_id)
    conference_ids = select(ConferenceSession.id).where(*filters)
    pending_participants = select(ConferenceParticipant.id).where(
        ConferenceParticipant.tenant_id == tenant_id,
        ConferenceParticipant.conference_session_id.in_(conference_ids),
        ConferenceParticipant.state.in_(
            (ParticipantState.PENDING, ParticipantState.DIALING)
        ),
    )
    number_ids = select(ConferenceParticipant.number_entry_id).where(
        ConferenceParticipant.id.in_(pending_participants),
        ConferenceParticipant.number_entry_id.is_not(None),
    )
    task_ids = select(ConferenceParticipant.task_id).where(
        ConferenceParticipant.id.in_(pending_participants),
        ConferenceParticipant.task_id.is_not(None),
    )
    now = datetime.now(UTC)
    await session.execute(
        update(Task)
        .where(
            Task.tenant_id == tenant_id,
            Task.id.in_(task_ids),
            Task.state == TaskState.QUEUED,
        )
        .values(
            state=TaskState.CANCELLED,
            finished_at=now,
            error_code="CONFERENCE_CANCELLED",
            error_message=reason,
        )
    )
    await session.execute(
        update(NumberEntry)
        .where(
            NumberEntry.tenant_id == tenant_id,
            NumberEntry.id.in_(number_ids),
            NumberEntry.state == NumberState.ASSIGNED,
        )
        .values(state=NumberState.CANCELLED, error=reason)
    )
    await session.execute(
        update(ConferenceParticipant)
        .where(ConferenceParticipant.id.in_(pending_participants))
        .values(state=ParticipantState.CANCELLED, error=reason)
    )
    ids = list(
        (
            await session.execute(
                update(ConferenceSession)
                .where(*filters)
                .values(state=ConferenceState.CANCELLED, error=reason)
                .returning(ConferenceSession.id)
            )
        ).scalars()
    )
    for conference_id in ids:
        task = conference_tasks.get(conference_id)
        if task is not None:
            task.cancel()


async def pause_running_conferences() -> None:
    async with session_factory() as session:
        tenant_ids = list((await session.execute(select(Tenant.id))).scalars())
        for tenant_id in tenant_ids:
            await set_tenant(session, tenant_id)
            await session.execute(
                update(ConferenceSession)
                .where(
                    ConferenceSession.tenant_id == tenant_id,
                    ConferenceSession.state == ConferenceState.RUNNING,
                )
                .values(state=ConferenceState.PAUSED, error="Servidor reiniciado")
            )
        await session.commit()


async def stop_conference_tasks() -> None:
    tasks = list(conference_tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def get_number_list(
    session: AsyncSession, tenant_id: UUID, number_list_id: UUID
) -> NumberList:
    value = (
        await session.execute(
            select(NumberList).where(
                NumberList.tenant_id == tenant_id,
                NumberList.id == number_list_id,
            )
        )
    ).scalar_one_or_none()
    if value is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lista no encontrada")
    return value


async def ensure_no_active_conferences(
    session: AsyncSession, tenant_id: UUID, device_ids: list[UUID]
) -> None:
    existing = (
        await session.execute(
            select(ConferenceSession.id).where(
                ConferenceSession.tenant_id == tenant_id,
                ConferenceSession.device_id.in_(device_ids),
                ConferenceSession.state.in_(
                    (
                        ConferenceState.QUEUED,
                        ConferenceState.RUNNING,
                        ConferenceState.PAUSED,
                        ConferenceState.ACTIVE,
                    )
                ),
            )
        )
    ).first()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya existe una conferencia activa")


def conference_response(value: ConferenceSession) -> ConferenceResponse:
    return ConferenceResponse(
        id=value.id,
        device_id=value.device_id,
        state=value.state,
        total=value.total,
        progress=value.progress,
        error=value.error,
        created_at=value.created_at,
    )
