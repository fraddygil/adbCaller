from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from adbcaller_core.call_controller import CallController
from adbcaller_core.device_discovery import DeviceDiscovery
from adbcaller_protocol.commands import (
    AddCallCommand,
    CallCommand,
    CommandAction,
    CommandMessagePayload,
)


class CommandExpiredError(RuntimeError):
    pass


class UnknownDeviceError(RuntimeError):
    pass


class DeviceExecutor:
    """Ejecuta únicamente las acciones cerradas definidas por el protocolo."""

    def __init__(
        self, controller: CallController, discovery: DeviceDiscovery
    ) -> None:
        self.controller = controller
        self.discovery = discovery
        self._locks: dict[UUID, asyncio.Lock] = {}

    async def execute(
        self,
        payload: CommandMessagePayload,
        serials: Mapping[UUID, str],
    ) -> dict[str, object]:
        if payload.expires_at <= datetime.now(UTC):
            raise CommandExpiredError("El comando expiró antes de ejecutarse")
        if payload.command.action is CommandAction.LIST_DEVICES:
            devices = await self.discovery.scan()
            return {"devices": [device.to_dict() for device in devices]}
        if payload.command.action is CommandAction.HANGUP_ALL:
            states = await asyncio.gather(
                *(self.controller.hangup(serial) for serial in serials.values()),
                return_exceptions=True,
            )
            return {
                "results": {
                    str(device_id): _serialize_result(state)
                    for device_id, state in zip(serials, states, strict=True)
                }
            }

        device_id = payload.device_id
        if device_id is None or device_id not in serials:
            raise UnknownDeviceError("El dispositivo no pertenece a este agente")
        lock = self._locks.setdefault(device_id, asyncio.Lock())
        async with lock:
            return await self._execute_device(payload, serials[device_id])

    async def _execute_device(
        self, payload: CommandMessagePayload, serial: str
    ) -> dict[str, object]:
        command = payload.command
        action = command.action
        if action is CommandAction.STATUS:
            return {"state": (await self.controller.get_call_state(serial)).value}
        if action is CommandAction.CALL:
            assert isinstance(command, CallCommand)
            state = await self.controller.call(serial, command.parameters.number)
            return {"state": state.value}
        if action is CommandAction.ANSWER:
            return {"state": (await self.controller.answer(serial)).value}
        if action is CommandAction.HANGUP:
            return {"state": (await self.controller.hangup(serial)).value}
        if action is CommandAction.ADD_CALL:
            assert isinstance(command, AddCallCommand)
            state = await self.controller.add_call(serial, command.parameters.number)
            return {"state": state.value}
        if action is CommandAction.MERGE:
            await self.controller.merge_calls(serial)
            return {"result": "MERGE_REQUESTED"}
        if action is CommandAction.ENABLE_SPEAKER:
            changed = await self.controller.enable_speaker(serial)
            return {"changed": changed}
        raise ValueError(f"Acción no permitida: {action}")


def _serialize_result(result: object) -> dict[str, object]:
    if isinstance(result, Exception):
        return {"ok": False, "error": str(result)}
    return {"ok": True, "state": getattr(result, "value", str(result))}
