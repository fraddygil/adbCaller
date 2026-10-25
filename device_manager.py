from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from adb_client import AdbClient
from call_controller import CallController, normalize_number
from config import get_device_logger
from db import Database
from models import AdbState, CallState, ConferenceState, DeviceSnapshot, ParticipantState


@dataclass(slots=True)
class WorkItem:
    name: str
    operation: Callable[[], Awaitable[Any]]
    future: asyncio.Future[Any]


@dataclass(slots=True)
class ManagedDevice:
    snapshot: DeviceSnapshot
    queue: asyncio.Queue[WorkItem] = field(default_factory=asyncio.Queue)
    pending_numbers: deque[str] = field(default_factory=deque)
    worker: asyncio.Task[None] | None = None
    auto_merge_pending: bool = False
    auto_merge_attempts: int = 0
    auto_merge_started_at: float | None = None
    auto_merge_running: bool = False
    merge_count: int = 0
    auto_merge_error: str = ""
    conference_task: asyncio.Task[None] | None = None
    conference_session_id: int | None = None
    conference_cancel_state: ConferenceState | None = None
    conference_cancel_error: str = ""
    call_state_changed: asyncio.Event = field(default_factory=asyncio.Event)


class DeviceUnavailableError(RuntimeError):
    pass


class DeviceManager:
    def __init__(
        self,
        adb: AdbClient,
        controller: CallController,
        database: Database,
        poll_interval: float = 2.5,
        auto_merge: bool = False,
        conference_call_timeout: float = 15,
        conference_merge_timeout: float = 30,
        max_conference_calls: int = 4,
        merge_attempts: int = 5,
        skip_failed_numbers: bool = True,
    ) -> None:
        self.adb = adb
        self.controller = controller
        self.database = database
        self.poll_interval = poll_interval
        self.auto_merge = auto_merge
        self.conference_call_timeout = conference_call_timeout
        self.conference_merge_timeout = conference_merge_timeout
        self.max_conference_calls = max_conference_calls
        self.merge_attempts = merge_attempts
        self.skip_failed_numbers = skip_failed_numbers
        self._devices: dict[str, ManagedDevice] = {}
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._poll_task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        try:
            await self.refresh_once()
        except Exception as exc:
            self._emit("poll_error", {"error": str(exc)})
        self._poll_task = asyncio.create_task(self._poll_loop(), name="device-poll")

    async def stop(self) -> None:
        self._running = False
        for record in self._devices.values():
            if record.conference_session_id is not None and record.snapshot.conference_state not in {
                ConferenceState.CANCELLED,
                ConferenceState.ENDED,
                ConferenceState.FAILED,
            }:
                await self._cancel_conference(
                    record,
                    ConferenceState.RECOVERED_PAUSED,
                    "Sesión pausada al detener el servidor",
                )
        tasks = [record.worker for record in self._devices.values() if record.worker]
        if self._poll_task:
            self._poll_task.cancel()
            tasks.append(self._poll_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def snapshots(self) -> list[DeviceSnapshot]:
        return [record.snapshot for _, record in sorted(self._devices.items())]

    def apply_settings(self, values: dict[str, Any]) -> None:
        self.poll_interval = float(values["poll_interval"])
        self.auto_merge = bool(values["auto_merge"])
        self.conference_call_timeout = float(values["conference_call_timeout"])
        self.conference_merge_timeout = float(values["conference_merge_timeout"])
        self.max_conference_calls = int(values["max_conference_calls"])
        self.merge_attempts = int(values["merge_attempts"])
        self.skip_failed_numbers = bool(values["skip_failed_numbers"])
        self.controller.auto_speaker = bool(values["auto_speaker"])

    async def refresh_once(self) -> None:
        discovered = {device.serial: device for device in await self.adb.list_devices()}

        for serial, record in self._devices.items():
            if serial not in discovered and record.snapshot.connected:
                if record.conference_session_id is not None and record.snapshot.conference_state not in {
                    ConferenceState.CANCELLED,
                    ConferenceState.ENDED,
                    ConferenceState.FAILED,
                    ConferenceState.RECOVERED_PAUSED,
                }:
                    await self._cancel_conference(
                        record,
                        ConferenceState.WAITING_DEVICE,
                        "Dispositivo desconectado; confirme para continuar al reconectar",
                    )
                record.snapshot.connected = False
                record.snapshot.adb_state = AdbState.OFFLINE
                if record.snapshot.call_state is not CallState.IDLE:
                    await self.database.finish_open_calls(serial, "DISCONNECTED")
                record.snapshot.call_state = CallState.IDLE
                await self.database.upsert_device(record.snapshot)
                self._emit("device_disconnected", record.snapshot.to_dict())

        for serial, adb_device in discovered.items():
            record = self._devices.get(serial)
            is_new = record is None
            was_connected = record.snapshot.connected if record else False
            if is_new:
                snapshot = DeviceSnapshot(serial, adb_device.model, adb_device.state)
                record = ManagedDevice(snapshot)
                self._devices[serial] = record
                record.worker = asyncio.create_task(
                    self._worker(serial, record), name=f"device-worker-{serial}"
                )
                await self._load_persistent_state(record)

            snapshot = record.snapshot
            snapshot.connected = True
            snapshot.adb_state = adb_device.state
            if adb_device.state is not AdbState.DEVICE:
                if snapshot.call_state is not CallState.IDLE:
                    await self.database.finish_open_calls(serial, adb_device.state.value.upper())
                snapshot.call_state = CallState.IDLE
            if adb_device.model != "Desconocido":
                snapshot.model = adb_device.model
            if adb_device.state is AdbState.DEVICE and (is_new or not was_connected):
                await self._load_properties(snapshot)
                if snapshot.conference_state is ConferenceState.WAITING_DEVICE:
                    snapshot.conference_state = ConferenceState.RECOVERED_PAUSED
                    snapshot.conference_error = (
                        "Dispositivo reconectado; confirme para continuar"
                    )
                    await self._persist_conference(record)
            await self.database.upsert_device(snapshot)
            if is_new or not was_connected:
                self._emit("device_connected", snapshot.to_dict())

        pollable = [
            record
            for record in self._devices.values()
            if record.snapshot.connected and record.snapshot.adb_state is AdbState.DEVICE
        ]
        await asyncio.gather(
            *(self._poll_call_state(record) for record in pollable), return_exceptions=True
        )

    async def submit(
        self, serial: str, name: str, operation: Callable[[], Awaitable[Any]]
    ) -> Any:
        record = self._require_available(serial)
        future = asyncio.get_running_loop().create_future()
        await record.queue.put(WorkItem(name, operation, future))
        return await future

    async def call(self, serial: str, number: str) -> CallState:
        number = normalize_number(number)

        async def operation() -> CallState:
            snapshot = self._devices[serial].snapshot
            if snapshot.conference_state in {
                ConferenceState.RECOVERED_PAUSED,
                ConferenceState.WAITING_DEVICE,
            }:
                raise RuntimeError(
                    "Confirme Reanudar o Cancelar para la sesión recuperada"
                )
            if snapshot.call_state is not CallState.IDLE:
                raise RuntimeError(f"El dispositivo {serial} ya tiene una llamada")
            state = await self.controller.call(serial, number)
            snapshot.call_state = state
            self._emit("call_state", snapshot.to_dict())
            return state

        return await self.submit(serial, "call", operation)

    async def hangup(self, serial: str) -> CallState:
        record = self._require_available(serial)
        if (
            record.conference_task
            and not record.conference_task.done()
            or record.snapshot.conference_state
            in {ConferenceState.RECOVERED_PAUSED, ConferenceState.WAITING_DEVICE}
        ):
            await self._cancel_conference(
                record,
                ConferenceState.CANCELLED,
                "Conferencia cancelada por el usuario",
            )
        else:
            record.auto_merge_pending = False

        async def operation() -> CallState:
            state = await self.controller.hangup(serial)
            snapshot = self._devices[serial].snapshot
            snapshot.call_state = state
            if snapshot.conference_state is ConferenceState.ACTIVE:
                snapshot.conference_state = ConferenceState.ENDED
            self._emit("call_state", snapshot.to_dict())
            return state

        return await self.submit(serial, "hangup", operation)

    async def add_call(self, serial: str, number: str) -> CallState:
        number = normalize_number(number)

        async def operation() -> CallState:
            if self._devices[serial].snapshot.conference_state in {
                ConferenceState.RECOVERED_PAUSED,
                ConferenceState.WAITING_DEVICE,
            }:
                raise RuntimeError(
                    "Confirme Reanudar o Cancelar para la sesión recuperada"
                )
            state = await self.controller.add_call(serial, number)
            record = self._devices[serial]
            record.snapshot.call_state = state
            record.auto_merge_pending = self.auto_merge
            record.auto_merge_attempts = 0
            record.auto_merge_started_at = None
            record.auto_merge_error = ""
            self._emit("call_state", record.snapshot.to_dict())
            return state

        return await self.submit(serial, "add_call", operation)

    async def current_calls(self, serial: str):
        return await self.submit(
            serial, "current_calls", lambda: self.controller.get_current_calls(serial)
        )

    async def merge_calls(self, serial: str) -> None:
        await self.submit(serial, "merge_calls", lambda: self.controller.merge_calls(serial))
        record = self._devices[serial]
        record.merge_count += 1
        record.auto_merge_pending = False
        record.auto_merge_attempts = 0
        record.auto_merge_started_at = None
        record.auto_merge_error = ""

    async def enable_speaker(self, serial: str) -> bool:
        return await self.submit(
            serial, "enable_speaker", lambda: self.controller.enable_speaker(serial)
        )

    async def start_conference(self, serial: str) -> int:
        record = self._require_available(serial)
        if not self.auto_merge:
            raise RuntimeError("AUTO_MERGE debe estar habilitado para la conferencia automática")
        if record.conference_task and not record.conference_task.done():
            raise RuntimeError(f"El dispositivo {serial} ya está creando una conferencia")
        if record.snapshot.conference_state in {
            ConferenceState.RECOVERED_PAUSED,
            ConferenceState.WAITING_DEVICE,
        }:
            raise RuntimeError(
                "Confirme Reanudar o Cancelar para la sesión recuperada"
            )
        if record.snapshot.call_state is not CallState.IDLE:
            raise RuntimeError(f"El dispositivo {serial} ya tiene una llamada")
        numbers = list(record.pending_numbers)[: self.max_conference_calls]
        if not numbers:
            raise ValueError("No hay números pendientes asignados al dispositivo")

        snapshot = record.snapshot
        snapshot.conference_state = ConferenceState.STARTING
        snapshot.conference_progress = 0
        snapshot.conference_total = len(numbers)
        snapshot.conference_error = ""
        session_id = await self.database.create_conference_session(serial, numbers)
        record.conference_session_id = session_id
        record.conference_task = asyncio.create_task(
            self._run_conference(record, list(enumerate(numbers)), existing_active=False),
            name=f"conference-{serial}",
        )
        self._emit("conference_state", snapshot.to_dict())
        return len(numbers)

    async def resume_conference(self, serial: str) -> int:
        record = self._require_available(serial)
        if record.snapshot.conference_state is not ConferenceState.RECOVERED_PAUSED:
            raise RuntimeError("No hay una conferencia recuperada para reanudar")
        if record.conference_session_id is None:
            raise RuntimeError("La sesión recuperada no tiene identificador")
        if record.snapshot.call_state not in {CallState.IDLE, CallState.ACTIVE}:
            raise RuntimeError(
                "La llamada actual debe estar IDLE o ACTIVE antes de reanudar"
            )
        pending = await self.database.get_session_pending_numbers(record.conference_session_id)
        if not pending:
            record.snapshot.conference_state = (
                ConferenceState.ACTIVE
                if record.snapshot.call_state is CallState.ACTIVE
                else ConferenceState.ENDED
            )
            record.snapshot.conference_error = ""
            await self._persist_conference(record)
            self._emit("conference_state", record.snapshot.to_dict())
            return 0
        record.snapshot.conference_error = ""
        record.snapshot.conference_state = (
            ConferenceState.ADDING
            if record.snapshot.call_state is CallState.ACTIVE
            else ConferenceState.STARTING
        )
        await self._persist_conference(record)
        record.conference_task = asyncio.create_task(
            self._run_conference(
                record,
                pending,
                existing_active=record.snapshot.call_state is CallState.ACTIVE,
            ),
            name=f"conference-resume-{serial}",
        )
        self._emit("conference_state", record.snapshot.to_dict())
        return len(pending)

    async def cancel_recovered_conference(self, serial: str) -> None:
        record = self._devices.get(serial)
        if record is None:
            raise KeyError(f"Dispositivo desconocido: {serial}")
        if record.snapshot.conference_state not in {
            ConferenceState.RECOVERED_PAUSED,
            ConferenceState.WAITING_DEVICE,
        }:
            raise RuntimeError("No hay una conferencia recuperada para cancelar")
        await self._cancel_conference(
            record, ConferenceState.CANCELLED, "Conferencia cancelada por el usuario"
        )
        self._emit("conference_state", record.snapshot.to_dict())

    async def call_all(self, assignments: dict[str, str] | None = None) -> dict[str, Any]:
        assignments = assignments or {}
        from_pending = not assignments
        if from_pending:
            assignments = {
                serial: record.pending_numbers[0]
                for serial, record in self._devices.items()
                if record.pending_numbers
                and record.snapshot.connected
                and record.snapshot.adb_state is AdbState.DEVICE
                and record.snapshot.conference_state
                not in {
                    ConferenceState.RECOVERED_PAUSED,
                    ConferenceState.WAITING_DEVICE,
                }
            }
        if not assignments:
            raise ValueError("No hay números asignados para llamar")

        async def run_one(serial: str, number: str) -> tuple[str, Any, bool]:
            try:
                state = await self.call(serial, number)
                return serial, state.value, True
            except Exception as exc:
                return serial, str(exc), False

        outcomes = await asyncio.gather(
            *(run_one(serial, number) for serial, number in assignments.items())
        )
        results: dict[str, Any] = {}
        for serial, value, succeeded in outcomes:
            if from_pending and succeeded:
                record = self._devices.get(serial)
                if record:
                    await self._consume_pending_number(record, assignments[serial])
            results[serial] = {"ok": succeeded, "state" if succeeded else "error": value}
        self._emit("assignments", {"remaining": self.pending_assignments()})
        return results

    async def hangup_all(self) -> dict[str, Any]:
        serials = [
            snapshot.serial
            for snapshot in self.snapshots()
            if snapshot.connected and snapshot.adb_state is AdbState.DEVICE
        ]

        async def run_one(serial: str) -> tuple[str, Any, bool]:
            try:
                state = await self.hangup(serial)
                return serial, state.value, True
            except Exception as exc:
                return serial, str(exc), False

        outcomes = await asyncio.gather(*(run_one(serial) for serial in serials))
        return {
            serial: {"ok": ok, "state" if ok else "error": value}
            for serial, value, ok in outcomes
        }

    async def start_all_conferences(self) -> dict[str, Any]:
        serials = [
            serial
            for serial, record in sorted(self._devices.items())
            if record.pending_numbers
            and record.snapshot.connected
            and record.snapshot.adb_state is AdbState.DEVICE
        ]
        if not serials:
            raise ValueError("No hay números pendientes asignados")

        results: dict[str, Any] = {}
        for serial in serials:
            try:
                total = await self.start_conference(serial)
                results[serial] = {"ok": True, "total": total}
            except Exception as exc:
                results[serial] = {"ok": False, "error": str(exc)}
        return results

    async def assign_numbers(
        self, numbers: list[str], target_serial: str | None = None
    ) -> dict[str, list[str]]:
        normalized = [normalize_number(number) for number in numbers]
        if not normalized:
            raise ValueError("No se encontraron números")
        if len(normalized) > 1000:
            raise ValueError("Máximo 1000 números por carga")

        if target_serial:
            records = [self._require_available(target_serial)]
        else:
            records = [
                record
                for _, record in sorted(self._devices.items())
                if record.snapshot.connected and record.snapshot.adb_state is AdbState.DEVICE
            ]
            if not records:
                raise DeviceUnavailableError("No hay dispositivos disponibles")

        assigned: dict[str, list[str]] = {record.snapshot.serial: [] for record in records}
        for index, number in enumerate(normalized):
            record = records[index % len(records)]
            record.pending_numbers.append(number)
            record.snapshot.pending_numbers = len(record.pending_numbers)
            assigned[record.snapshot.serial].append(number)
        for serial, serial_numbers in assigned.items():
            if serial_numbers:
                await self.database.enqueue_numbers(serial, serial_numbers)
        self._emit("assignments", {"remaining": self.pending_assignments()})
        return assigned

    def pending_assignments(self) -> dict[str, list[str]]:
        return {
            serial: list(record.pending_numbers)
            for serial, record in sorted(self._devices.items())
            if record.pending_numbers
        }

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.poll_interval)
                await self.refresh_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._emit("poll_error", {"error": str(exc)})

    async def _worker(self, serial: str, record: ManagedDevice) -> None:
        while True:
            item = await record.queue.get()
            try:
                if item.future.cancelled():
                    continue
                self._require_available(serial)
                result = await item.operation()
                if not item.future.done():
                    item.future.set_result(result)
                get_device_logger(serial).info("Acción %s completada", item.name)
            except asyncio.CancelledError:
                if not item.future.done():
                    item.future.set_exception(DeviceUnavailableError("Worker detenido"))
                raise
            except Exception as exc:
                if not item.future.done():
                    item.future.set_exception(exc)
                get_device_logger(serial).error("Acción %s falló: %s", item.name, exc)
            finally:
                record.queue.task_done()

    async def _load_properties(self, snapshot: DeviceSnapshot) -> None:
        try:
            properties = await self.adb.get_properties(snapshot.serial)
        except Exception as exc:
            get_device_logger(snapshot.serial).warning("No se pudo leer getprop: %s", exc)
            return
        snapshot.model = properties.get("ro.product.model", snapshot.model)
        snapshot.manufacturer = properties.get("ro.product.manufacturer", "")
        snapshot.android_version = properties.get("ro.build.version.release", "")
        snapshot.api_level = properties.get("ro.build.version.sdk", "")
        snapshot.one_ui_version = properties.get("ro.build.version.oneui", "")
        try:
            android_major = int(snapshot.android_version.split(".", 1)[0])
        except ValueError:
            android_major = 0
        snapshot.compatible = (
            snapshot.manufacturer.casefold() == "samsung" and android_major >= 14
        )

    async def _poll_call_state(self, record: ManagedDevice) -> None:
        snapshot = record.snapshot
        try:
            current = await self.controller.get_call_state(snapshot.serial)
            previous = snapshot.call_state
            if current is not previous:
                snapshot.call_state = current
                await self.controller.handle_state_change(snapshot.serial, previous, current)
                if (
                    current is CallState.IDLE
                    and snapshot.conference_state is ConferenceState.ACTIVE
                ):
                    snapshot.conference_state = ConferenceState.ENDED
                record.call_state_changed.set()
                self._emit("call_state", snapshot.to_dict())
            if record.auto_merge_pending and current is CallState.ACTIVE:
                await self._attempt_auto_merge(record)
        except Exception as exc:
            get_device_logger(snapshot.serial).warning("No se pudo leer estado: %s", exc)

    async def _attempt_auto_merge(self, record: ManagedDevice) -> None:
        if record.auto_merge_running or not record.auto_merge_pending:
            return
        record.auto_merge_running = True
        serial = record.snapshot.serial
        if record.auto_merge_started_at is None:
            record.auto_merge_started_at = time.monotonic()
        started = record.auto_merge_started_at
        try:
            await self.merge_calls(serial)
            self._emit("conference_merged", record.snapshot.to_dict())
            get_device_logger(serial).info(
                "Combinación automática solicitada %.2f s después de detectar ACTIVE",
                time.monotonic() - started,
            )
        except Exception as exc:
            record.auto_merge_attempts += 1
            get_device_logger(serial).warning(
                "Intento %s de combinación automática falló: %s",
                record.auto_merge_attempts,
                exc,
            )
            if record.auto_merge_attempts >= self.merge_attempts:
                record.auto_merge_pending = False
                record.auto_merge_started_at = None
                record.auto_merge_error = str(exc)
                self._emit(
                    "auto_merge_error",
                    {"serial": serial, "error": str(exc)},
                )
        finally:
            record.auto_merge_running = False

    async def _run_conference(
        self,
        record: ManagedDevice,
        participants: list[tuple[int, str]],
        existing_active: bool,
    ) -> None:
        snapshot = record.snapshot
        session_id = record.conference_session_id
        if session_id is None:
            snapshot.conference_state = ConferenceState.FAILED
            snapshot.conference_error = "Sesión de conferencia inexistente"
            return
        has_active = existing_active
        try:
            for position, number in participants:
                snapshot.conference_state = (
                    ConferenceState.ADDING if has_active else ConferenceState.STARTING
                )
                snapshot.conference_error = ""
                await self._persist_conference(record)
                self._emit("conference_state", snapshot.to_dict())

                baseline = {
                    call.call_id for call in await self.controller.get_current_calls(snapshot.serial)
                }
                await self.database.update_participant(
                    session_id, position, "DIALING"
                )
                await self._consume_pending_number(record, number)
                expected_merge = record.merge_count + 1
                dial_started = False

                try:
                    if has_active:
                        await self.add_call(snapshot.serial, number)
                    else:
                        await self.call(snapshot.serial, number)
                    dial_started = True
                    snapshot.conference_state = ConferenceState.WAITING_ACTIVE
                    await self._persist_conference(record)
                    self._emit("conference_state", snapshot.to_dict())
                    await self._wait_for_new_active_call(
                        record,
                        baseline,
                        expected_merge if has_active else None,
                    )
                    await self.database.update_participant(
                        session_id, position, ParticipantState.ACTIVE.value
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await self._skip_failed_participant(
                        record, position, exc, end_attempt=dial_started
                    )
                    if not self.skip_failed_numbers:
                        raise
                    continue

                if has_active:
                    snapshot.conference_state = ConferenceState.MERGING
                    await self._persist_conference(record)
                    self._emit("conference_state", snapshot.to_dict())
                    try:
                        await self._wait_for_merge(
                            record, expected_merge, self.conference_merge_timeout
                        )
                    except Exception as exc:
                        await self._skip_failed_participant(
                            record, position, exc, end_attempt=True
                        )
                        if not self.skip_failed_numbers:
                            raise
                        continue
                    await self.database.update_participant(
                        session_id, position, ParticipantState.MERGED.value
                    )
                has_active = True
                snapshot.conference_progress += 1
                await self._persist_conference(record)

            if not has_active:
                raise RuntimeError("Ningún número contestó")
            snapshot.conference_state = ConferenceState.ACTIVE
            await self._persist_conference(record)
            self._emit("conference_state", snapshot.to_dict())
        except asyncio.CancelledError:
            snapshot.conference_state = (
                record.conference_cancel_state or ConferenceState.CANCELLED
            )
            snapshot.conference_error = (
                record.conference_cancel_error or "Conferencia cancelada"
            )
            await self._persist_conference(record)
            self._emit("conference_state", snapshot.to_dict())
            raise
        except Exception as exc:
            snapshot.conference_state = ConferenceState.FAILED
            snapshot.conference_error = str(exc)
            await self._persist_conference(record)
            self._emit("conference_state", snapshot.to_dict())
            get_device_logger(snapshot.serial).error("Conferencia falló: %s", exc)

    async def _wait_for_new_active_call(
        self,
        record: ManagedDevice,
        baseline: set[str],
        expected_merge: int | None,
    ) -> None:
        deadline = asyncio.get_running_loop().time() + self.conference_call_timeout
        saw_new_call = False
        while asyncio.get_running_loop().time() < deadline:
            record.call_state_changed.clear()
            calls = await self.controller.get_current_calls(record.snapshot.serial)
            new_calls = [call for call in calls if call.call_id not in baseline]
            if new_calls:
                saw_new_call = True
            if expected_merge is not None and record.merge_count >= expected_merge:
                return
            if any(call.state is CallState.ACTIVE for call in new_calls):
                await self.controller.on_call_active(record.snapshot.serial)
                record.snapshot.call_state = CallState.ACTIVE
                return
            if saw_new_call and not new_calls:
                raise RuntimeError("La llamada fue rechazada o terminó sin contestar")
            try:
                await asyncio.wait_for(
                    record.call_state_changed.wait(),
                    timeout=min(
                        self.poll_interval,
                        max(0, deadline - asyncio.get_running_loop().time()),
                    ),
                )
            except TimeoutError:
                pass
        raise TimeoutError(
            f"El número no contestó en {self.conference_call_timeout:g} segundos"
        )

    async def _wait_for_merge(
        self, record: ManagedDevice, expected_count: int, timeout: float
    ) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if record.merge_count >= expected_count:
                return
            if record.auto_merge_error:
                raise RuntimeError(record.auto_merge_error)
            if record.snapshot.call_state is CallState.IDLE:
                raise RuntimeError("Las llamadas terminaron antes de combinarse")
            if record.auto_merge_pending:
                await self._attempt_auto_merge(record)
            if record.merge_count >= expected_count:
                return
            if record.auto_merge_error:
                raise RuntimeError(record.auto_merge_error)
            await asyncio.sleep(
                min(self.poll_interval, max(0, deadline - asyncio.get_running_loop().time()))
            )
        raise TimeoutError("Timeout esperando la combinación automática")

    async def _cancel_conference(
        self,
        record: ManagedDevice,
        state: ConferenceState,
        error: str,
    ) -> None:
        if record.conference_session_id is None:
            record.auto_merge_pending = False
            return
        record.conference_cancel_state = state
        record.conference_cancel_error = error
        task = record.conference_task
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        else:
            record.snapshot.conference_state = state
            record.snapshot.conference_error = error
            await self._persist_conference(record)
        record.auto_merge_pending = False
        record.conference_cancel_state = None
        record.conference_cancel_error = ""

    async def _skip_failed_participant(
        self,
        record: ManagedDevice,
        position: int,
        error: Exception,
        end_attempt: bool,
    ) -> None:
        if end_attempt:
            try:
                await self.controller.end_failed_attempt(record.snapshot.serial, "SKIPPED")
            except Exception as exc:
                get_device_logger(record.snapshot.serial).warning(
                    "No se pudo terminar el intento fallido: %s", exc
                )
        await asyncio.sleep(0.5)
        state = await self.controller.get_call_state(record.snapshot.serial)
        record.snapshot.call_state = state
        if record.conference_session_id is not None:
            participant_state = (
                ParticipantState.NO_ANSWER
                if isinstance(error, TimeoutError)
                else ParticipantState.REJECTED
                if "rechazada" in str(error).casefold()
                else ParticipantState.SKIPPED
            )
            await self.database.update_participant(
                record.conference_session_id,
                position,
                participant_state.value,
                str(error),
            )
        get_device_logger(record.snapshot.serial).warning(
            "Número omitido en posición %s: %s", position + 1, error
        )

    async def _persist_conference(self, record: ManagedDevice) -> None:
        if record.conference_session_id is None:
            return
        snapshot = record.snapshot
        await self.database.update_conference_session(
            record.conference_session_id,
            snapshot.conference_state.value,
            snapshot.conference_progress,
            snapshot.conference_error,
        )

    async def _load_persistent_state(self, record: ManagedDevice) -> None:
        serial = record.snapshot.serial
        queued = await self.database.load_queued_numbers(serial)
        record.pending_numbers = deque(queued)
        record.snapshot.pending_numbers = len(queued)
        session = await self.database.get_recoverable_session(serial)
        if not session:
            return
        session_id, _, total, progress, error = session
        record.conference_session_id = int(session_id)
        record.snapshot.conference_total = int(total)
        record.snapshot.conference_progress = int(progress)
        record.snapshot.conference_error = error or "Sesión recuperada; confirme para continuar"
        record.snapshot.conference_state = ConferenceState.RECOVERED_PAUSED
        await self.database.update_conference_session(
            record.conference_session_id,
            ConferenceState.RECOVERED_PAUSED.value,
            record.snapshot.conference_progress,
            record.snapshot.conference_error,
        )

    async def _consume_pending_number(self, record: ManagedDevice, number: str) -> None:
        if record.pending_numbers and record.pending_numbers[0] == number:
            record.pending_numbers.popleft()
        else:
            try:
                record.pending_numbers.remove(number)
            except ValueError:
                pass
        record.snapshot.pending_numbers = len(record.pending_numbers)
        await self.database.remove_queued_number(record.snapshot.serial, number)

    def _require_available(self, serial: str) -> ManagedDevice:
        record = self._devices.get(serial)
        if not record:
            raise KeyError(f"Dispositivo desconocido: {serial}")
        if not record.snapshot.connected or record.snapshot.adb_state is not AdbState.DEVICE:
            raise DeviceUnavailableError(
                f"Dispositivo {serial} no disponible ({record.snapshot.adb_state.value})"
            )
        return record

    def _emit(self, event: str, data: Any) -> None:
        message = {"event": event, "data": data}
        for queue in tuple(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(message)
