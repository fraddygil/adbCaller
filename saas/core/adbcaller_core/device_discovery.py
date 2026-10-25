from __future__ import annotations

from typing import Protocol

from adbcaller_core.models import AdbDevice, AdbState, DeviceSnapshot


class DiscoveryTransport(Protocol):
    async def list_devices(self) -> list[AdbDevice]: ...

    async def get_properties(self, serial: str) -> dict[str, str]: ...


class DeviceDiscovery:
    def __init__(self, adb: DiscoveryTransport) -> None:
        self.adb = adb

    async def scan(self) -> list[DeviceSnapshot]:
        snapshots: list[DeviceSnapshot] = []
        for device in await self.adb.list_devices():
            snapshot = DeviceSnapshot(device.serial, device.model, device.state)
            if device.state is AdbState.DEVICE:
                properties = await self.adb.get_properties(device.serial)
                snapshot.manufacturer = properties.get("ro.product.manufacturer", "")
                snapshot.model = properties.get("ro.product.model", snapshot.model)
                snapshot.android_version = properties.get("ro.build.version.release", "")
                snapshot.api_level = properties.get("ro.build.version.sdk", "")
                snapshot.one_ui_version = properties.get("ro.build.version.oneui", "")
                snapshot.build_fingerprint = properties.get("ro.build.fingerprint", "")
            snapshots.append(snapshot)
        return snapshots
