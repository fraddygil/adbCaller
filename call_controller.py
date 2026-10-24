from __future__ import annotations

import asyncio
import re
import time
import xml.etree.ElementTree as ET

from adb_client import AdbClient
from config import get_device_logger
from db import Database, utc_now
from models import CallLeg, CallState


VALID_NUMBER = re.compile(r"^\+?[0-9]{3,20}$")
CURRENT_CALLS_BLOCK = re.compile(
    r"^  mCalls:\s*\n(?P<body>.*?)(?=^  \S.*:\s*$|\Z)", re.MULTILINE | re.DOTALL
)
TELECOM_STATE = re.compile(
    r"(?:mState|state)\s*[:=]\s*"
    r"(ACTIVE|HOLDING|ON_HOLD|DIALING|CONNECTING|SELECT_PHONE_ACCOUNT|RINGING)",
    re.IGNORECASE,
)
REGISTRY_STATE = re.compile(r"mCallState\s*=\s*([012])")
CALL_ID = re.compile(r"\bCall(?:\[id=|\s+id=|\s+)(?P<id>TC@[\w.-]+)", re.IGNORECASE)
BOUNDS = re.compile(r"\[(\d+),(\d+)]\[(\d+),(\d+)]")
MERGE_BUTTON_ID = "com.samsung.android.incallui:id/merge_button"
SPEAKER_BUTTON_ID = "com.samsung.android.incallui:id/fourth_button"
CURRENT_AUDIO_ROUTE = re.compile(r"Current route:\s*AudioRoute\[Type=(TYPE_[A-Z_]+)")


def normalize_number(number: str) -> str:
    normalized = re.sub(r"[\s().-]", "", number)
    if not VALID_NUMBER.fullmatch(normalized):
        raise ValueError("Número inválido: use entre 3 y 20 dígitos, con + opcional")
    return normalized


class CallController:
    def __init__(
        self,
        adb: AdbClient,
        database: Database | None = None,
        auto_speaker: bool = False,
    ) -> None:
        self.adb = adb
        self.database = database
        self.auto_speaker = auto_speaker

    async def call(self, serial: str, number: str) -> CallState:
        number = normalize_number(number)
        started_at = utc_now()
        logger = get_device_logger(serial)
        try:
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
            if self.database:
                await self.database.start_call(serial, number, started_at)
            logger.info("Llamada iniciada a %s", number)
            return CallState.DIALING
        except Exception as exc:
            if self.database:
                await self.database.record_failed_call(serial, number, started_at, str(exc))
            logger.error("Error llamando a %s: %s", number, exc)
            raise

    async def add_call(self, serial: str, number: str, max_calls: int = 4) -> CallState:
        state, calls = await self.get_call_snapshot(serial)
        if state is not CallState.ACTIVE:
            raise RuntimeError("Se necesita una llamada ACTIVE antes de agregar otra")
        if len(calls) >= max_calls:
            raise RuntimeError(f"El dispositivo ya alcanzó el máximo de {max_calls} llamadas")
        return await self.call(serial, number)

    async def merge_calls(self, serial: str) -> None:
        started = time.monotonic()
        state, _ = await self.get_call_snapshot(serial)
        if state is CallState.IDLE:
            raise RuntimeError("No hay llamadas actuales para combinar")

        remote_path = "/sdcard/adbcaller-window.xml"
        await self.adb.ensure_awake(serial)
        hierarchy = await self._read_ui_hierarchy(serial, remote_path)

        x, y = find_merge_button_center(hierarchy)
        await self.adb.shell(serial, "input", "tap", str(x), str(y))
        get_device_logger(serial).info(
            "Orden de combinar llamadas enviada en %.2f s",
            time.monotonic() - started,
        )

    async def enable_speaker(self, serial: str) -> bool:
        started = time.monotonic()
        telecom = await self.adb.shell(serial, "dumpsys", "telecom")
        route = CURRENT_AUDIO_ROUTE.search(telecom)
        if route and route.group(1) == "TYPE_SPEAKER":
            return False

        await self.adb.ensure_awake(serial)
        last_error: RuntimeError | None = None
        for attempt in range(3):
            hierarchy = await self._read_ui_hierarchy(
                serial, "/sdcard/adbcaller-speaker-window.xml"
            )
            try:
                x, y = find_speaker_button_center(hierarchy)
                break
            except RuntimeError as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.5)
        else:
            raise last_error or RuntimeError("No se encontró el botón Altavoz")
        await self.adb.shell(serial, "input", "tap", str(x), str(y))
        get_device_logger(serial).info(
            "Altavoz activado en %.2f s", time.monotonic() - started
        )
        return True

    async def _read_ui_hierarchy(self, serial: str, remote_path: str) -> str:
        try:
            await self.adb.shell(serial, "uiautomator", "dump", remote_path)
            return await self.adb.shell(serial, "cat", remote_path)
        finally:
            await self.adb.shell(serial, "rm", "-f", remote_path)

    async def hangup(self, serial: str) -> CallState:
        await self.adb.ensure_awake(serial)
        await self.adb.shell(serial, "input", "keyevent", "KEYCODE_ENDCALL")
        if self.database:
            await self.database.finish_open_call(serial, "HUNG_UP")
        get_device_logger(serial).info("Llamada colgada")
        return CallState.IDLE

    async def answer(self, serial: str) -> CallState:
        await self.adb.ensure_awake(serial)
        await self.adb.shell(serial, "input", "keyevent", "KEYCODE_CALL")
        get_device_logger(serial).info("Llamada contestada")
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
        calls = parse_current_calls(telecom)
        states = {call.state for call in calls}
        for state in (
            CallState.RINGING,
            CallState.DIALING,
            CallState.ACTIVE,
            CallState.HOLDING,
        ):
            if state in states:
                return state, calls

        registry_states = set(REGISTRY_STATE.findall(registry))
        if "1" in registry_states:
            return CallState.RINGING, calls
        if "2" in registry_states:
            return CallState.ACTIVE, calls
        return CallState.IDLE, calls

    async def handle_state_change(
        self, serial: str, previous: CallState, current: CallState
    ) -> None:
        if current is CallState.ACTIVE and previous is not CallState.ACTIVE:
            await self.on_call_active(serial)
        if current is CallState.IDLE and previous is not CallState.IDLE and self.database:
            await self.database.finish_open_calls(serial, "ENDED")

    async def on_call_active(self, serial: str) -> None:
        """Hook para que la Fase 3 agregue y combine llamadas."""
        if self.auto_speaker:
            await self.enable_speaker(serial)


def parse_current_calls(telecom: str) -> list[CallLeg]:
    block = CURRENT_CALLS_BLOCK.search(telecom)
    if not block:
        return []

    calls: dict[str, CallState] = {}
    current_id: str | None = None
    for line in block.group("body").splitlines():
        call_id = CALL_ID.search(line)
        if call_id:
            current_id = call_id.group("id")
        state_match = TELECOM_STATE.search(line)
        if current_id and state_match:
            calls[current_id] = _normalize_telecom_state(state_match.group(1))
    return [CallLeg(call_id, state) for call_id, state in calls.items()]


def _normalize_telecom_state(value: str) -> CallState:
    value = value.upper()
    if value in {"CONNECTING", "SELECT_PHONE_ACCOUNT"}:
        return CallState.DIALING
    if value == "ON_HOLD":
        return CallState.HOLDING
    return CallState(value)


def find_merge_button_center(hierarchy: str) -> tuple[int, int]:
    try:
        root = ET.fromstring(hierarchy)
    except ET.ParseError as exc:
        raise RuntimeError("One UI devolvió una jerarquía inválida") from exc

    fallback = None
    for node in root.iter("node"):
        attributes = node.attrib
        if attributes.get("enabled") != "true" or attributes.get("clickable") != "true":
            continue
        searchable = " ".join(
            (
                attributes.get("text", ""),
                attributes.get("content-desc", ""),
            )
        ).casefold()
        if attributes.get("resource-id") == MERGE_BUTTON_ID:
            return _bounds_center(attributes.get("bounds", ""))
        if any(label in searchable for label in ("merge", "combinar", "conferencia")):
            fallback = attributes.get("bounds", "")
    if fallback:
        return _bounds_center(fallback)
    raise RuntimeError("No se encontró el botón Combinar habilitado en One UI")


def find_speaker_button_center(hierarchy: str) -> tuple[int, int]:
    try:
        root = ET.fromstring(hierarchy)
    except ET.ParseError as exc:
        raise RuntimeError("One UI devolvió una jerarquía inválida") from exc

    fallback = None
    for node in root.iter("node"):
        attributes = node.attrib
        if attributes.get("enabled") != "true" or attributes.get("clickable") != "true":
            continue
        searchable = " ".join(
            (
                attributes.get("text", ""),
                attributes.get("content-desc", ""),
                attributes.get("resource-id", ""),
            )
        ).casefold()
        has_speaker_label = any(
            label in searchable for label in ("speaker", "altavoz", "speakerphone")
        )
        if has_speaker_label and attributes.get("resource-id") == SPEAKER_BUTTON_ID:
            return _bounds_center(attributes.get("bounds", ""))
        if has_speaker_label:
            fallback = attributes.get("bounds", "")
    if fallback:
        return _bounds_center(fallback)
    raise RuntimeError("No se encontró el botón Altavoz habilitado en One UI")


def _bounds_center(value: str) -> tuple[int, int]:
    match = BOUNDS.fullmatch(value)
    if not match:
        raise RuntimeError("El botón Combinar no tiene límites válidos")
    left, top, right, bottom = (int(item) for item in match.groups())
    return (left + right) // 2, (top + bottom) // 2
