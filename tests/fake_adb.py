from __future__ import annotations

from adb_client import AdbError
from models import AdbDevice


class FakeAdb:
    def __init__(self, devices: list[AdbDevice] | None = None) -> None:
        self.devices = devices or []
        self.commands: list[tuple[str, tuple[str, ...]]] = []
        self.outputs: dict[tuple[str, ...], str] = {}
        self.properties: dict[str, dict[str, str]] = {}
        self.fail_calls_for: set[str] = set()

    async def list_devices(self) -> list[AdbDevice]:
        return list(self.devices)

    async def get_properties(self, serial: str) -> dict[str, str]:
        return self.properties.get(
            serial,
            {
                "ro.product.manufacturer": "samsung",
                "ro.product.model": "SM-A566B",
                "ro.build.version.release": "15",
                "ro.build.version.sdk": "35",
                "ro.build.version.oneui": "70100",
            },
        )

    async def ensure_awake(self, serial: str) -> None:
        self.commands.append((serial, ("WAKE",)))

    async def shell(self, serial: str, *args: str, timeout=None) -> str:
        self.commands.append((serial, args))
        if args[:2] == ("am", "start") and serial in self.fail_calls_for:
            raise AdbError("fallo simulado")
        if args == ("dumpsys", "telephony.registry"):
            return self.outputs.get(args, "mCallState=0")
        if args == ("dumpsys", "telecom"):
            return self.outputs.get(args, "")
        return self.outputs.get(args, "")
