from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


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
    manufacturer: str = ""
    android_version: str = ""
    api_level: str = ""
    one_ui_version: str = ""
    build_fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
