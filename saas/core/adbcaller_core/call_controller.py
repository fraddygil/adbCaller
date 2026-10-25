from __future__ import annotations

import asyncio
from typing import Protocol

from adbcaller_core.call_state import parse_call_state, parse_current_calls
from adbcaller_core.models import CallLeg, CallState
from adbcaller_core.number_validation import normalize_number
from adbcaller_core.samsung_ui import SamsungUiDriver


class AdbCallTransport(Protocol):
    async def ensure_awake(self, serial: str) -> None: ...

    async def shell(
        self, serial: str, *args: str, timeout: float | None = None
    ) -> str: ...


class CallController:
    def __init__(self, adb: AdbCallTransport) -> None:
        self.adb = adb
        self.ui = SamsungUiDriver(adb)

    async def call(self, serial: str, number: str) -> CallState:
        state, _ = await self.get_call_snapshot(serial)
        if state is not CallState.IDLE:
            raise RuntimeError("CALL requiere que el dispositivo esté IDLE")
        return await self._dial(serial, number)

    async def _dial(self, serial: str, number: str) -> CallState:
        number = normalize_number(number)
        await self.adb.ensure_awake(serial)
        await self.adb.shell(
            serial,
            "am",
            "start",
            "-a",
            "android.intent.action.CALL",
            "-d",
            f"tel:{number}",
        )
        return CallState.DIALING

    async def add_call(self, serial: str, number: str, max_calls: int = 4) -> CallState:
        state, calls = await self.get_call_snapshot(serial)
        if state is not CallState.ACTIVE:
            raise RuntimeError("Se necesita una llamada ACTIVE antes de agregar otra")
        if len(calls) >= max_calls:
            raise RuntimeError(f"El dispositivo ya alcanzó el máximo de {max_calls} llamadas")
        return await self._dial(serial, number)

    async def merge_calls(self, serial: str) -> None:
        state, _ = await self.get_call_snapshot(serial)
        if state is CallState.IDLE:
            raise RuntimeError("No hay llamadas actuales para combinar")
        await self.ui.merge_calls(serial)

    async def enable_speaker(self, serial: str) -> bool:
        return await self.ui.enable_speaker(serial)

    async def hangup(self, serial: str) -> CallState:
        await self.adb.ensure_awake(serial)
        await self.adb.shell(serial, "input", "keyevent", "KEYCODE_ENDCALL")
        return CallState.IDLE

    async def answer(self, serial: str) -> CallState:
        await self.adb.ensure_awake(serial)
        await self.adb.shell(serial, "input", "keyevent", "KEYCODE_CALL")
        return CallState.ACTIVE

    async def get_call_state(self, serial: str) -> CallState:
        state, _ = await self.get_call_snapshot(serial)
        return state

    async def get_current_calls(self, serial: str) -> list[CallLeg]:
        telecom = await self.adb.shell(serial, "dumpsys", "telecom")
        return parse_current_calls(telecom)

    async def get_call_snapshot(self, serial: str) -> tuple[CallState, list[CallLeg]]:
        telecom, registry = await asyncio.gather(
            self.adb.shell(serial, "dumpsys", "telecom"),
            self.adb.shell(serial, "dumpsys", "telephony.registry"),
        )
        return parse_call_state(telecom, registry)
