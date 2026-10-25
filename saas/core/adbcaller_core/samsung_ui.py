from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from typing import Protocol


MERGE_BUTTON_ID = "com.samsung.android.incallui:id/merge_button"
SPEAKER_BUTTON_ID = "com.samsung.android.incallui:id/fourth_button"
CURRENT_AUDIO_ROUTE = re.compile(r"Current route:\s*AudioRoute\[Type=(TYPE_[A-Z_]+)")
BOUNDS = re.compile(r"\[(\d+),(\d+)]\[(\d+),(\d+)]")


class AdbUiTransport(Protocol):
    async def ensure_awake(self, serial: str) -> None: ...

    async def shell(
        self, serial: str, *args: str, timeout: float | None = None
    ) -> str: ...


class SamsungUiDriver:
    def __init__(self, adb: AdbUiTransport) -> None:
        self.adb = adb

    async def merge_calls(self, serial: str) -> None:
        await self.adb.ensure_awake(serial)
        hierarchy = await self._read_ui_hierarchy(
            serial, "/sdcard/adbcaller-window.xml"
        )
        x, y = find_merge_button_center(hierarchy)
        await self.adb.shell(serial, "input", "tap", str(x), str(y))

    async def enable_speaker(self, serial: str) -> bool:
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
        return True

    async def _read_ui_hierarchy(self, serial: str, remote_path: str) -> str:
        try:
            await self.adb.shell(serial, "uiautomator", "dump", remote_path)
            return await self.adb.shell(serial, "cat", remote_path)
        finally:
            await self.adb.shell(serial, "rm", "-f", remote_path)


def find_merge_button_center(hierarchy: str) -> tuple[int, int]:
    root = parse_hierarchy(hierarchy)
    fallback = None
    for node in root.iter("node"):
        attributes = node.attrib
        if not is_available(attributes):
            continue
        searchable = " ".join(
            (attributes.get("text", ""), attributes.get("content-desc", ""))
        ).casefold()
        if attributes.get("resource-id") == MERGE_BUTTON_ID:
            return bounds_center(attributes.get("bounds", ""))
        if any(label in searchable for label in ("merge", "combinar", "conferencia")):
            fallback = attributes.get("bounds", "")
    if fallback:
        return bounds_center(fallback)
    raise RuntimeError("No se encontró el botón Combinar habilitado en One UI")


def find_speaker_button_center(hierarchy: str) -> tuple[int, int]:
    root = parse_hierarchy(hierarchy)
    fallback = None
    for node in root.iter("node"):
        attributes = node.attrib
        if not is_available(attributes):
            continue
        searchable = " ".join(
            (
                attributes.get("text", ""),
                attributes.get("content-desc", ""),
                attributes.get("resource-id", ""),
            )
        ).casefold()
        has_label = any(
            label in searchable for label in ("speaker", "altavoz", "speakerphone")
        )
        if has_label and attributes.get("resource-id") == SPEAKER_BUTTON_ID:
            return bounds_center(attributes.get("bounds", ""))
        if has_label:
            fallback = attributes.get("bounds", "")
    if fallback:
        return bounds_center(fallback)
    raise RuntimeError("No se encontró el botón Altavoz habilitado en One UI")


def parse_hierarchy(hierarchy: str) -> ET.Element:
    try:
        return ET.fromstring(hierarchy)
    except ET.ParseError as exc:
        raise RuntimeError("One UI devolvió una jerarquía inválida") from exc


def is_available(attributes: dict[str, str]) -> bool:
    return attributes.get("enabled") == "true" and attributes.get("clickable") == "true"


def bounds_center(value: str) -> tuple[int, int]:
    match = BOUNDS.fullmatch(value)
    if not match:
        raise RuntimeError("El control no tiene límites válidos")
    left, top, right, bottom = (int(item) for item in match.groups())
    return (left + right) // 2, (top + bottom) // 2
