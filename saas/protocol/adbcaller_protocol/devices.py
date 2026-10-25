from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import Field

from adbcaller_protocol.envelope import MessageBase, MessageType, StrictModel


class DeviceConnectionState(StrEnum):
    DEVICE = "device"
    UNAUTHORIZED = "unauthorized"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class DeviceCallState(StrEnum):
    IDLE = "IDLE"
    DIALING = "DIALING"
    RINGING = "RINGING"
    ACTIVE = "ACTIVE"
    HOLDING = "HOLDING"


class DeviceInfo(StrictModel):
    device_id: UUID
    local_serial: str = Field(min_length=1, max_length=255)
    model: str = Field(max_length=255)
    manufacturer: str = Field(max_length=100)
    android_version: str = Field(max_length=50)
    api_level: str = Field(max_length=20)
    one_ui_version: str = Field(max_length=50)
    build_fingerprint: str = Field(max_length=512)
    connection_state: DeviceConnectionState
    call_state: DeviceCallState
    call_count: int = Field(default=0, ge=0, le=100)
    capabilities: frozenset[str] = frozenset()


class DeviceListPayload(StrictModel):
    generation: int = Field(ge=0)
    devices: list[DeviceInfo] = Field(max_length=100)


class DeviceStatePayload(StrictModel):
    device: DeviceInfo


class DeviceListMessage(MessageBase):
    message_type: Literal[MessageType.DEVICE_LIST] = MessageType.DEVICE_LIST
    payload: DeviceListPayload


class DeviceStateMessage(MessageBase):
    message_type: Literal[MessageType.DEVICE_STATE] = MessageType.DEVICE_STATE
    payload: DeviceStatePayload
