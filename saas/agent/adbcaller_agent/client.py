from __future__ import annotations

import asyncio
import json
import logging
import platform
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from uuid import UUID, uuid4, uuid5

from websockets.asyncio.client import connect

from adbcaller_agent import __version__
from adbcaller_agent.config import AgentConfig
from adbcaller_agent.executor import DeviceExecutor
from adbcaller_agent.journal import CommandJournal, JournalEntry
from adbcaller_core import AdbClient, AdbState, CallController
from adbcaller_core.device_discovery import DeviceDiscovery
from adbcaller_protocol.commands import (
    CommandMessage,
    CommandResultMessage,
    CommandResultPayload,
    CommandState,
)
from adbcaller_protocol.devices import (
    DeviceCallState,
    DeviceConnectionState,
    DeviceInfo,
    DeviceListMessage,
    DeviceListPayload,
)
from adbcaller_protocol.envelope import MessageType
from adbcaller_protocol.events import (
    HeartbeatMessage,
    HeartbeatPayload,
    HelloMessage,
    HelloPayload,
    HelloResultMessage,
)
from adbcaller_protocol.messages import parse_message
from adbcaller_protocol.version import MIN_PROTOCOL_VERSION, PROTOCOL_VERSION


logger = logging.getLogger(__name__)


def pair_agent(server_url: str, code: str, name: str) -> AgentConfig:
    body = json.dumps(
        {
            "code": code,
            "agent_name": name,
            "agent_version": __version__,
            "platform": f"linux/{platform.machine()}",
        }
    ).encode()
    request = urllib.request.Request(
        server_url.rstrip("/") + "/agent/pair",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"El servidor rechazó el pairing (HTTP {exc.code})") from exc
    return AgentConfig(
        server_url=server_url.rstrip("/"),
        agent_id=UUID(payload["agent_id"]),
        token=payload["token"],
        agent_name=name,
    )


class AgentClient:
    def __init__(self, config: AgentConfig, journal_path: Path) -> None:
        self.config = config
        self.adb = AdbClient(resolve_adb_path(config.adb_path))
        self.controller = CallController(self.adb)
        self.discovery = DeviceDiscovery(self.adb)
        self.executor = DeviceExecutor(self.controller, self.discovery)
        self.journal = CommandJournal(journal_path)
        self.serials: dict[UUID, str] = {}
        self.sequence = 0
        self.generation = 0

    async def run_forever(self) -> None:
        delay = 1
        while True:
            try:
                await self._run_connection()
                delay = 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Conexión perdida: %s; reintento en %ss", exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)

    async def _run_connection(self) -> None:
        async with connect(
            websocket_url(self.config.server_url),
            additional_headers={"Authorization": f"Bearer {self.config.token}"},
            open_timeout=15,
            ping_interval=20,
            ping_timeout=20,
            max_size=1_048_576,
        ) as websocket:
            await websocket.send(self._hello().model_dump_json())
            response = parse_message(await asyncio.wait_for(websocket.recv(), timeout=10))
            if not isinstance(response, HelloResultMessage) or not response.payload.accepted:
                error = getattr(response.payload, "error", None)
                raise RuntimeError(error or "El servidor rechazó la conexión")
            logger.info("Agente conectado con protocolo %s", response.payload.protocol_version)

            sender = asyncio.create_task(self._send_status(websocket))
            try:
                async for raw_message in websocket:
                    message = parse_message(raw_message)
                    if isinstance(message, CommandMessage):
                        await self._execute_command(websocket, message)
            finally:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)

    async def _send_status(self, websocket) -> None:
        while True:
            self.sequence += 1
            heartbeat = HeartbeatMessage(
                version=PROTOCOL_VERSION,
                message_id=uuid4(),
                message_type=MessageType.HEARTBEAT,
                timestamp=datetime.now(UTC),
                payload=HeartbeatPayload(
                    agent_id=self.config.agent_id,
                    sequence=self.sequence,
                ),
            )
            await websocket.send(heartbeat.model_dump_json())
            try:
                device_message = await self._device_list()
                await websocket.send(device_message.model_dump_json())
            except Exception as exc:
                logger.warning("No se pudo leer ADB: %s", exc)
            await asyncio.sleep(5)

    async def _device_list(self) -> DeviceListMessage:
        devices: list[DeviceInfo] = []
        serials: dict[UUID, str] = {}
        for snapshot in await self.discovery.scan():
            device_id = uuid5(self.config.agent_id, snapshot.serial)
            serials[device_id] = snapshot.serial
            calls = []
            if snapshot.adb_state is AdbState.DEVICE:
                snapshot.call_state, calls = await self.controller.get_call_snapshot(
                    snapshot.serial
                )
            capabilities = {"STATUS", "CALL", "ANSWER", "HANGUP"}
            if snapshot.manufacturer.casefold() == "samsung":
                capabilities.update({"ADD_CALL", "MERGE", "ENABLE_SPEAKER"})
            devices.append(
                DeviceInfo(
                    device_id=device_id,
                    local_serial=snapshot.serial,
                    model=snapshot.model,
                    manufacturer=snapshot.manufacturer,
                    android_version=snapshot.android_version,
                    api_level=snapshot.api_level,
                    one_ui_version=snapshot.one_ui_version,
                    build_fingerprint=snapshot.build_fingerprint,
                    connection_state=DeviceConnectionState(snapshot.adb_state.value),
                    call_state=DeviceCallState(snapshot.call_state.value),
                    call_count=len(calls),
                    capabilities=frozenset(capabilities),
                )
            )
        self.serials = serials
        self.generation += 1
        return DeviceListMessage(
            version=PROTOCOL_VERSION,
            message_id=uuid4(),
            message_type=MessageType.DEVICE_LIST,
            timestamp=datetime.now(UTC),
            payload=DeviceListPayload(generation=self.generation, devices=devices),
        )

    async def _execute_command(self, websocket, message: CommandMessage) -> None:
        payload = message.payload
        if payload.agent_id != self.config.agent_id:
            return
        existing = await asyncio.to_thread(self.journal.get, payload.command_id)
        if existing is not None:
            if existing.state == CommandState.RUNNING:
                existing = JournalEntry(
                    CommandState.FAILED,
                    {},
                    "INTERRUPTED_NOT_RETRIED",
                    "La ejecución anterior quedó interrumpida y no se repitió",
                )
            await websocket.send(self._result(payload.command_id, existing).model_dump_json())
            return

        await asyncio.to_thread(self.journal.start, payload.command_id)
        await websocket.send(
            self._result(payload.command_id, JournalEntry(CommandState.RUNNING, {}, None, None)).model_dump_json()
        )
        try:
            result = await self.executor.execute(payload, self.serials)
            entry = JournalEntry(CommandState.DONE, result, None, None)
        except Exception as exc:
            entry = JournalEntry(CommandState.FAILED, {}, type(exc).__name__, str(exc))
        await asyncio.to_thread(
            self.journal.finish,
            payload.command_id,
            entry.state,
            entry.result,
            entry.error_code,
            entry.error_message,
        )
        await websocket.send(self._result(payload.command_id, entry).model_dump_json())

    def _result(self, command_id: UUID, entry: JournalEntry) -> CommandResultMessage:
        return CommandResultMessage(
            version=PROTOCOL_VERSION,
            message_id=uuid4(),
            message_type=MessageType.COMMAND_RESULT,
            timestamp=datetime.now(UTC),
            payload=CommandResultPayload(
                command_id=command_id,
                state=CommandState(entry.state),
                result=entry.result,
                error_code=entry.error_code,
                error_message=entry.error_message,
            ),
        )

    def _hello(self) -> HelloMessage:
        return HelloMessage(
            version=PROTOCOL_VERSION,
            message_id=uuid4(),
            message_type=MessageType.HELLO,
            timestamp=datetime.now(UTC),
            payload=HelloPayload(
                agent_id=self.config.agent_id,
                agent_version=__version__,
                minimum_protocol_version=MIN_PROTOCOL_VERSION,
                maximum_protocol_version=PROTOCOL_VERSION,
            ),
        )


def websocket_url(server_url: str) -> str:
    parsed = urlparse(server_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse((scheme, parsed.netloc, "/agent/ws", "", "", ""))


def resolve_adb_path(configured: str) -> str:
    if configured != "adb":
        return configured
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root is not None:
        candidate = Path(bundle_root) / "platform-tools" / "adb"
        if candidate.is_file():
            return str(candidate)
    return configured
