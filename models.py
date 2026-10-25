from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AdbState(StrEnum):
    DEVICE = "device"
    UNAUTHORIZED = "unauthorized"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class CallState(StrEnum):
    IDLE = "IDLE"
    DIALING = "DIALING"
    RINGING = "RINGING"
    ACTIVE = "ACTIVE"
    HOLDING = "HOLDING"


class ConferenceState(StrEnum):
    IDLE = "IDLE"
    STARTING = "STARTING"
    WAITING_ACTIVE = "WAITING_ACTIVE"
    ADDING = "ADDING"
    MERGING = "MERGING"
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    ENDED = "ENDED"
    RECOVERED_PAUSED = "RECOVERED_PAUSED"
    WAITING_DEVICE = "WAITING_DEVICE"


class ParticipantState(StrEnum):
    PENDING = "PENDING"
    DIALING = "DIALING"
    ACTIVE = "ACTIVE"
    MERGED = "MERGED"
    NO_ANSWER = "NO_ANSWER"
    BUSY = "BUSY"
    REJECTED = "REJECTED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class CallLeg:
    call_id: str
    state: CallState


@dataclass(slots=True)
class AdbDevice:
    serial: str
    model: str = "Desconocido"
    state: AdbState = AdbState.UNKNOWN


@dataclass(slots=True)
class DeviceSnapshot:
    serial: str
    model: str
    adb_state: AdbState
    call_state: CallState = CallState.IDLE
    connected: bool = True
    manufacturer: str = ""
    android_version: str = ""
    api_level: str = ""
    one_ui_version: str = ""
    compatible: bool = False
    pending_numbers: int = 0
    conference_state: ConferenceState = ConferenceState.IDLE
    conference_progress: int = 0
    conference_total: int = 0
    conference_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CallRequest(BaseModel):
    number: str = Field(min_length=1, max_length=64)


class CallAllRequest(BaseModel):
    assignments: dict[str, str] = Field(default_factory=dict)
