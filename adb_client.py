from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from models import AdbDevice, AdbState


@dataclass(slots=True)
class CommandResult:
    stdout: str
    stderr: str
    returncode: int


class AdbError(RuntimeError):
    def __init__(self, message: str, command: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.command = command


class AdbClient:
    def __init__(self, adb_path: str = "adb", timeout: float = 15.0) -> None:
        self.adb_path = adb_path
        self.timeout = timeout

    async def _exec(self, *args: str, timeout: float | None = None) -> CommandResult:
        command = (self.adb_path, *args)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise AdbError(f"No se encontró adb en: {self.adb_path}", command) from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout or self.timeout
            )
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise AdbError(f"Timeout ejecutando {' '.join(command)}", command) from exc

        result = CommandResult(
            stdout.decode(errors="replace").strip(),
            stderr.decode(errors="replace").strip(),
            process.returncode or 0,
        )
        if result.returncode != 0:
            detail = result.stderr or result.stdout or "error desconocido"
            raise AdbError(f"ADB falló ({result.returncode}): {detail}", command)
        return result

    async def run(self, serial: str, *args: str, timeout: float | None = None) -> str:
        if not serial.strip():
            raise ValueError("El serial no puede estar vacío")
        result = await self._exec("-s", serial, *args, timeout=timeout)
        return result.stdout

    async def shell(self, serial: str, *args: str, timeout: float | None = None) -> str:
        return await self.run(serial, "shell", *args, timeout=timeout)

    async def list_devices(self) -> list[AdbDevice]:
        # El descubrimiento es la única orden que no admite -s porque aún no hay serial.
        output = (await self._exec("devices", "-l")).stdout
        return parse_devices(output)

    async def get_properties(self, serial: str) -> dict[str, str]:
        output = await self.shell(serial, "getprop")
        return {
            key: value
            for key, value in re.findall(r"^\[([^]]+)\]: \[([^]]*)\]$", output, re.MULTILINE)
        }

    async def ensure_awake(self, serial: str) -> None:
        await self.shell(serial, "input", "keyevent", "KEYCODE_WAKEUP")
        await self.shell(serial, "svc", "power", "stayon", "usb")


def parse_devices(output: str) -> list[AdbDevice]:
    devices: list[AdbDevice] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, raw_state = parts[0], parts[1]
        try:
            state = AdbState(raw_state)
        except ValueError:
            state = AdbState.UNKNOWN
        fields = dict(item.split(":", 1) for item in parts[2:] if ":" in item)
        devices.append(AdbDevice(serial, fields.get("model", "Desconocido"), state))
    return devices
