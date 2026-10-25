from __future__ import annotations

import asyncio
import csv
import io
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from adbcaller_protocol.commands import CallCommand, CallParameters, CommandAction
from adbcaller_server.auth import ApiModel, AuthContext, current_user, valid_csrf
from adbcaller_server.database import get_session, set_tenant
from adbcaller_server.models import (
    AuditLog,
    Call,
    Device,
    NumberEntry,
    NumberList,
    Task,
    Tenant,
)
from adbcaller_server.models.enums import NumberState, TaskState
from adbcaller_server.realtime import event_hub
from adbcaller_server.tasks import TaskResponse, dispatch_agent, task_response


router = APIRouter(prefix="/number-lists", tags=["number-lists"])
NUMBER_PATTERN = re.compile(r"^\+?[0-9]{3,20}$")


class CreateNumberListRequest(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    numbers: list[str] = Field(min_length=1, max_length=1000)

    @field_validator("numbers")
    @classmethod
    def validate_numbers(cls, values: list[str]) -> list[str]:
        return [normalize_number(value) for value in values]


class NumberListResponse(ApiModel):
    id: UUID
    name: str
    total: int
    pending: int
    created_at: datetime


class NumberEntryResponse(ApiModel):
    id: UUID
    number: str
    position: int
    state: NumberState
    assigned_device_id: UUID | None
    task_id: UUID | None
    error: str


@router.post("", response_model=NumberListResponse, status_code=status.HTTP_201_CREATED)
async def create_number_list(
    request: CreateNumberListRequest,
    context: AuthContext = Depends(valid_csrf),
    session: AsyncSession = Depends(get_session),
) -> NumberListResponse:
    return await save_number_list(request.name, request.numbers, context, session)


@router.post("/upload", response_model=NumberListResponse, status_code=status.HTTP_201_CREATED)
async def upload_number_list(
    name: str = Form(min_length=1, max_length=200),
    csv_file: UploadFile = File(),
    context: AuthContext = Depends(valid_csrf),
    session: AsyncSession = Depends(get_session),
) -> NumberListResponse:
    content = await csv_file.read(1_000_001)
    if len(content) > 1_000_000:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "El CSV no puede superar 1 MB")
    try:
        numbers = parse_csv_numbers(content.decode("utf-8-sig"))
        normalized = [normalize_number(value) for value in numbers]
    except (UnicodeDecodeError, csv.Error, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"CSV inválido: {exc}") from exc
    if not normalized:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El CSV no contiene números")
    if len(normalized) > 1000:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Una lista admite como máximo 1000 números")
    return await save_number_list(name, normalized, context, session)


@router.get("", response_model=list[NumberListResponse])
async def list_number_lists(
    context: AuthContext = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[NumberListResponse]:
    rows = (
        await session.execute(
            select(
                NumberList,
                func.count(NumberEntry.id).label("total"),
                func.count(NumberEntry.id)
                .filter(NumberEntry.state == NumberState.PENDING)
                .label("pending"),
            )
            .outerjoin(
                NumberEntry,
                (NumberEntry.tenant_id == NumberList.tenant_id)
                & (NumberEntry.number_list_id == NumberList.id),
            )
            .where(NumberList.tenant_id == context.tenant_id)
            .group_by(NumberList.id)
            .order_by(NumberList.created_at.desc())
        )
    ).all()
    return [number_list_response(item, total, pending) for item, total, pending in rows]


@router.get("/{number_list_id}/numbers", response_model=list[NumberEntryResponse])
async def list_numbers(
    number_list_id: UUID,
    context: AuthContext = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[NumberEntryResponse]:
    values = (
        await session.execute(
            select(NumberEntry)
            .where(
                NumberEntry.tenant_id == context.tenant_id,
                NumberEntry.number_list_id == number_list_id,
            )
            .order_by(NumberEntry.position)
        )
    ).scalars()
    return [
        NumberEntryResponse(
            id=value.id,
            number=value.normalized_number,
            position=value.position,
            state=value.state,
            assigned_device_id=value.assigned_device_id,
            task_id=value.task_id,
            error=value.error,
        )
        for value in values
    ]


@router.post("/{number_list_id}/call-all", response_model=list[TaskResponse])
async def call_all(
    number_list_id: UUID,
    context: AuthContext = Depends(valid_csrf),
    session: AsyncSession = Depends(get_session),
) -> list[TaskResponse]:
    number_list = (
        await session.execute(
            select(NumberList).where(
                NumberList.tenant_id == context.tenant_id,
                NumberList.id == number_list_id,
            )
        )
    ).scalar_one_or_none()
    if number_list is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lista no encontrada")
    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == context.tenant_id))
    ).scalar_one()
    occupied_devices = select(Task.device_id).where(
        Task.tenant_id == context.tenant_id,
        Task.state.in_(
            (TaskState.QUEUED, TaskState.SENT, TaskState.RUNNING, TaskState.UNCERTAIN)
        ),
        Task.device_id.is_not(None),
    )
    busy_count = (
        await session.execute(
            select(func.count(Device.id)).where(
                Device.tenant_id == context.tenant_id,
                Device.call_state != "IDLE",
            )
        )
    ).scalar_one()
    call_slots = max(0, tenant.max_concurrent_calls - busy_count)
    if call_slots == 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Se alcanzó el límite de llamadas simultáneas del plan",
        )
    devices = list(
        (
            await session.execute(
                select(Device)
                .where(
                    Device.tenant_id == context.tenant_id,
                    Device.online.is_(True),
                    Device.blocked_by_limit.is_(False),
                    Device.call_state == "IDLE",
                    Device.id.not_in(occupied_devices),
                )
                .order_by(Device.created_at)
            )
        ).scalars()
    )
    devices = [
        device
        for device in devices
        if CommandAction.CALL.value in device.capabilities.get("actions", [])
    ][:call_slots]
    if not devices:
        raise HTTPException(status.HTTP_409_CONFLICT, "No hay dispositivos IDLE disponibles")
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
                .limit(len(devices))
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    if not numbers:
        raise HTTPException(status.HTTP_409_CONFLICT, "La lista no tiene números pendientes")

    now = datetime.now(UTC)
    tasks: list[Task] = []
    for device, number in zip(devices, numbers, strict=False):
        command = CallCommand(parameters=CallParameters(number=number.normalized_number))
        task = Task(
            tenant_id=context.tenant_id,
            agent_id=device.agent_id,
            device_id=device.id,
            created_by_user_id=context.user.id,
            action=command.action.value,
            payload=command.model_dump(mode="json"),
            state=TaskState.QUEUED,
            idempotency_key=str(uuid4()),
            timeout_at=now + timedelta(seconds=30),
        )
        session.add(task)
        await session.flush()
        session.add(
            Call(
                tenant_id=context.tenant_id,
                agent_id=device.agent_id,
                device_id=device.id,
                task_id=task.id,
                normalized_number=number.normalized_number,
                state="DIALING",
                result="STARTED",
                started_at=now,
            )
        )
        number.state = NumberState.ASSIGNED
        number.assigned_device_id = device.id
        number.task_id = task.id
        tasks.append(task)
    session.add(
        AuditLog(
            tenant_id=context.tenant_id,
            user_id=context.user.id,
            action="call_all.start",
            outcome="QUEUED",
            metadata_json={"number_list_id": str(number_list_id), "count": len(tasks)},
            created_at=now,
        )
    )
    await session.commit()
    await asyncio.gather(
        *(dispatch_agent(context.tenant_id, agent_id) for agent_id in {task.agent_id for task in tasks})
    )
    for task in tasks:
        await set_tenant(session, context.tenant_id)
        await session.refresh(task)
    await event_hub.publish(
        context.tenant_id,
        "call_all.queued",
        {"number_list_id": str(number_list_id), "count": len(tasks)},
    )
    return [task_response(task) for task in tasks]


async def save_number_list(
    name: str,
    numbers: list[str],
    context: AuthContext,
    session: AsyncSession,
) -> NumberListResponse:
    number_list = NumberList(
        tenant_id=context.tenant_id,
        name=name.strip(),
        created_by_user_id=context.user.id,
    )
    session.add(number_list)
    await session.flush()
    session.add_all(
        NumberEntry(
            tenant_id=context.tenant_id,
            number_list_id=number_list.id,
            normalized_number=number,
            position=position,
            state=NumberState.PENDING,
        )
        for position, number in enumerate(numbers)
    )
    session.add(
        AuditLog(
            tenant_id=context.tenant_id,
            user_id=context.user.id,
            action="number_list.create",
            outcome="SUCCESS",
            metadata_json={"number_list_id": str(number_list.id), "count": len(numbers)},
            created_at=datetime.now(UTC),
        )
    )
    await session.commit()
    await event_hub.publish(
        context.tenant_id,
        "number_list.created",
        {"number_list_id": str(number_list.id)},
    )
    return number_list_response(number_list, len(numbers), len(numbers))


def normalize_number(value: str) -> str:
    normalized = re.sub(r"[\s().-]", "", value)
    if not NUMBER_PATTERN.fullmatch(normalized):
        raise ValueError(f"Número inválido: {value!r}")
    return normalized


def parse_csv_numbers(value: str) -> list[str]:
    rows = csv.reader(io.StringIO(value))
    numbers: list[str] = []
    for index, row in enumerate(rows):
        if not row or not row[0].strip():
            continue
        cell = row[0].strip()
        if index == 0 and cell.casefold() in {"number", "phone", "numero", "número"}:
            continue
        numbers.append(cell)
    return numbers


def number_list_response(
    number_list: NumberList, total: int, pending: int
) -> NumberListResponse:
    return NumberListResponse(
        id=number_list.id,
        name=number_list.name,
        total=total,
        pending=pending,
        created_at=number_list.created_at,
    )
