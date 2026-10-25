from __future__ import annotations

from adbcaller_core.models import AdbDevice


class FakeAdb:
    def __init__(self, devices: list[AdbDevice] | None = None) -> None:
        self.devices = devices or []
        self.commands: list[tuple[str, tuple[str, ...]]] = []
        self.outputs: dict[tuple[str, ...], str] = {}
        self.properties: dict[str, dict[str, str]] = {}

    async def list_devices(self) -> list[AdbDevice]:
        return list(self.devices)

    async def get_properties(self, serial: str) -> dict[str, str]:
        return self.properties.get(serial, {})

    async def ensure_awake(self, serial: str) -> None:
        self.commands.append((serial, ("WAKE",)))

    async def shell(
        self, serial: str, *args: str, timeout: float | None = None
    ) -> str:
        self.commands.append((serial, args))
        if args == ("dumpsys", "telephony.registry"):
            return self.outputs.get(args, "mCallState=0")
        return self.outputs.get(args, "")
