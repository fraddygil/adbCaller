import asyncio
from pathlib import Path

from call_controller import CallController
from db import Database
from device_manager import DeviceManager
from models import AdbDevice, AdbState, CallState, ConferenceState
from tests.fake_adb import FakeAdb


def test_refresh_detects_disconnect(tmp_path: Path) -> None:
    async def scenario() -> None:
        adb = FakeAdb([AdbDevice("ONE", "SM-A566B", AdbState.DEVICE)])
        database = Database(tmp_path / "test.db")
        await database.initialize()
        controller = CallController(adb, database)  # type: ignore[arg-type]
        manager = DeviceManager(adb, controller, database, poll_interval=60)  # type: ignore[arg-type]
        await manager.refresh_once()
        assert manager.snapshots()[0].connected is True
        assert manager.snapshots()[0].compatible is True

        adb.devices = []
        await manager.refresh_once()
        assert manager.snapshots()[0].connected is False
        await manager.stop()

    asyncio.run(scenario())


def test_call_all_isolates_a_device_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        adb = FakeAdb(
            [
                AdbDevice("ONE", "SM-A566B", AdbState.DEVICE),
                AdbDevice("TWO", "SM-A566B", AdbState.DEVICE),
            ]
        )
        adb.fail_calls_for.add("TWO")
        database = Database(tmp_path / "test.db")
        await database.initialize()
        controller = CallController(adb, database)  # type: ignore[arg-type]
        manager = DeviceManager(adb, controller, database, poll_interval=60)  # type: ignore[arg-type]
        await manager.refresh_once()

        results = await manager.call_all({"ONE": "8095550101", "TWO": "8095550102"})

        assert results["ONE"]["ok"] is True
        assert results["TWO"]["ok"] is False
        await manager.stop()

    asyncio.run(scenario())


def test_add_call_runs_through_device_worker(tmp_path: Path) -> None:
    async def scenario() -> None:
        adb = FakeAdb([AdbDevice("ONE", "SM-A566B", AdbState.DEVICE)])
        adb.outputs[("dumpsys", "telecom")] = """CallsManager:
  mCalls:
    Call[id=TC@1, state=ACTIVE]
  mCallAudioManager:
"""
        adb.outputs[("dumpsys", "telephony.registry")] = "mCallState=2"
        database = Database(tmp_path / "test.db")
        await database.initialize()
        controller = CallController(adb, database)  # type: ignore[arg-type]
        manager = DeviceManager(adb, controller, database, poll_interval=60)  # type: ignore[arg-type]
        await manager.refresh_once()

        state = await manager.add_call("ONE", "8095550102")

        assert state is CallState.DIALING
        assert ("ONE", ("am", "start", "-a", "android.intent.action.CALL", "-d", "tel:8095550102")) in adb.commands
        await manager.stop()

    asyncio.run(scenario())


def test_added_call_is_automatically_merged_when_active(tmp_path: Path) -> None:
    async def scenario() -> None:
        adb = FakeAdb([AdbDevice("ONE", "SM-A566B", AdbState.DEVICE)])
        adb.outputs[("dumpsys", "telecom")] = """CallsManager:
  mCalls:
    Call[id=TC@1, state=ACTIVE]
  mCallAudioManager:
"""
        adb.outputs[("dumpsys", "telephony.registry")] = "mCallState=2"
        adb.outputs[("cat", "/sdcard/adbcaller-window.xml")] = """<hierarchy>
  <node text="Merge" resource-id="com.samsung.android.incallui:id/merge_button"
        clickable="true" enabled="true" bounds="[657,497][825,649]" />
</hierarchy>"""
        database = Database(tmp_path / "test.db")
        await database.initialize()
        controller = CallController(adb, database)  # type: ignore[arg-type]
        manager = DeviceManager(
            adb,
            controller,
            database,
            poll_interval=60,
            auto_merge=True,
        )  # type: ignore[arg-type]
        await manager.refresh_once()
        await manager.add_call("ONE", "8095550102")

        await manager.refresh_once()

        assert ("ONE", ("input", "tap", "741", "573")) in adb.commands
        await manager.stop()

    asyncio.run(scenario())


def test_pending_number_starts_background_conference(tmp_path: Path) -> None:
    async def scenario() -> None:
        adb = FakeAdb([AdbDevice("ONE", "SM-A566B", AdbState.DEVICE)])
        database = Database(tmp_path / "test.db")
        await database.initialize()
        controller = CallController(adb, database)  # type: ignore[arg-type]
        manager = DeviceManager(
            adb,
            controller,
            database,
            poll_interval=60,
            auto_merge=True,
            conference_call_timeout=2,
        )  # type: ignore[arg-type]
        await manager.refresh_once()
        await manager.assign_numbers(["8095550101"], "ONE")

        total = await manager.start_conference("ONE")
        for _ in range(100):
            if any(command[1][:2] == ("am", "start") for command in adb.commands):
                break
            await asyncio.sleep(0.01)
        adb.outputs[("dumpsys", "telecom")] = """CallsManager:
  mCalls:
    Call[id=TC@1, state=ACTIVE]
  mCallAudioManager:
"""
        adb.outputs[("dumpsys", "telephony.registry")] = "mCallState=2"
        await manager.refresh_once()
        task = manager._devices["ONE"].conference_task
        assert task is not None
        await asyncio.wait_for(task, timeout=2)

        snapshot = manager.snapshots()[0]
        assert total == 1
        assert snapshot.conference_state is ConferenceState.ACTIVE
        assert snapshot.conference_progress == 1
        assert snapshot.pending_numbers == 0
        await manager.stop()

    asyncio.run(scenario())


def test_start_all_conferences_uses_each_devices_pending_numbers(tmp_path: Path) -> None:
    async def scenario() -> None:
        adb = FakeAdb(
            [
                AdbDevice("ONE", "SM-A566B", AdbState.DEVICE),
                AdbDevice("TWO", "SM-A566B", AdbState.DEVICE),
            ]
        )
        database = Database(tmp_path / "test.db")
        await database.initialize()
        controller = CallController(adb, database)  # type: ignore[arg-type]
        manager = DeviceManager(
            adb, controller, database, poll_interval=60, auto_merge=True
        )  # type: ignore[arg-type]
        await manager.refresh_once()
        await manager.assign_numbers(["8095550101", "8095550102"], "ONE")
        await manager.assign_numbers(["8095550103"], "TWO")

        results = await manager.start_all_conferences()

        assert results["ONE"] == {"ok": True, "total": 2}
        assert results["TWO"] == {"ok": True, "total": 1}
        await manager.stop()

    asyncio.run(scenario())


def test_call_all_removes_number_from_persistent_queue(tmp_path: Path) -> None:
    async def scenario() -> None:
        adb = FakeAdb([AdbDevice("ONE", "SM-A566B", AdbState.DEVICE)])
        database = Database(tmp_path / "test.db")
        await database.initialize()
        controller = CallController(adb, database)  # type: ignore[arg-type]
        manager = DeviceManager(adb, controller, database, poll_interval=60)  # type: ignore[arg-type]
        await manager.refresh_once()
        await manager.assign_numbers(["8095550101"], "ONE")

        results = await manager.call_all()

        assert results["ONE"]["ok"] is True
        assert await database.load_queued_numbers("ONE") == []
        await manager.stop()

    asyncio.run(scenario())


def test_hangup_cancels_conference_before_next_number(tmp_path: Path) -> None:
    async def scenario() -> None:
        adb = FakeAdb([AdbDevice("ONE", "SM-A566B", AdbState.DEVICE)])
        database = Database(tmp_path / "test.db")
        await database.initialize()
        controller = CallController(adb, database)  # type: ignore[arg-type]
        manager = DeviceManager(
            adb,
            controller,
            database,
            poll_interval=60,
            auto_merge=True,
            conference_call_timeout=30,
        )  # type: ignore[arg-type]
        await manager.refresh_once()
        await manager.assign_numbers(["8095550101", "8095550102"], "ONE")
        await manager.start_conference("ONE")
        for _ in range(100):
            if any(command[1][:2] == ("am", "start") for command in adb.commands):
                break
            await asyncio.sleep(0.01)

        await manager.hangup("ONE")
        await asyncio.sleep(0.05)

        dialed = [
            command[-1]
            for _, command in adb.commands
            if command[:2] == ("am", "start")
        ]
        assert dialed == ["tel:8095550101"]
        assert manager.snapshots()[0].conference_state is ConferenceState.CANCELLED
        await manager.stop()

    asyncio.run(scenario())


def test_restart_recovers_paused_without_redialing_uncertain_number(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        path = tmp_path / "test.db"
        adb = FakeAdb([AdbDevice("ONE", "SM-A566B", AdbState.DEVICE)])
        database = Database(path)
        await database.initialize()
        controller = CallController(adb, database)  # type: ignore[arg-type]
        manager = DeviceManager(
            adb,
            controller,
            database,
            poll_interval=60,
            auto_merge=True,
            conference_call_timeout=30,
        )  # type: ignore[arg-type]
        await manager.refresh_once()
        await manager.assign_numbers(["8095550101", "8095550102"], "ONE")
        await manager.start_conference("ONE")
        for _ in range(100):
            if any(command[1][:2] == ("am", "start") for command in adb.commands):
                break
            await asyncio.sleep(0.01)
        await manager.stop()

        recovered_adb = FakeAdb(
            [AdbDevice("ONE", "SM-A566B", AdbState.DEVICE)]
        )
        recovered_controller = CallController(recovered_adb, database)  # type: ignore[arg-type]
        recovered = DeviceManager(
            recovered_adb,
            recovered_controller,
            database,
            poll_interval=60,
            auto_merge=True,
            conference_call_timeout=30,
        )  # type: ignore[arg-type]
        await recovered.refresh_once()

        snapshot = recovered.snapshots()[0]
        assert snapshot.conference_state is ConferenceState.RECOVERED_PAUSED
        assert recovered.pending_assignments() == {"ONE": ["8095550102"]}
        try:
            await recovered.call_all()
        except ValueError:
            pass
        else:
            raise AssertionError("call-all no debe consumir una sesión recuperada")
        assert not any(
            command[1][:2] == ("am", "start")
            for command in recovered_adb.commands
        )

        await recovered.resume_conference("ONE")
        for _ in range(100):
            if any(command[1][:2] == ("am", "start") for command in recovered_adb.commands):
                break
            await asyncio.sleep(0.01)
        dialed = [
            command[-1]
            for _, command in recovered_adb.commands
            if command[:2] == ("am", "start")
        ]
        assert dialed == ["tel:8095550102"]
        await recovered.hangup("ONE")
        await recovered.stop()

    asyncio.run(scenario())


def test_unanswered_numbers_are_skipped_until_queue_is_exhausted(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adb = FakeAdb([AdbDevice("ONE", "SM-A566B", AdbState.DEVICE)])
        database = Database(tmp_path / "test.db")
        await database.initialize()
        controller = CallController(adb, database)  # type: ignore[arg-type]
        manager = DeviceManager(
            adb,
            controller,
            database,
            poll_interval=60,
            auto_merge=True,
            conference_call_timeout=0.05,
        )  # type: ignore[arg-type]
        await manager.refresh_once()
        await manager.assign_numbers(["8095550101", "8095550102"], "ONE")
        await manager.start_conference("ONE")
        task = manager._devices["ONE"].conference_task
        assert task is not None

        await asyncio.wait_for(task, timeout=3)

        dialed = [
            command[-1]
            for _, command in adb.commands
            if command[:2] == ("am", "start")
        ]
        assert dialed == ["tel:8095550101", "tel:8095550102"]
        assert manager.snapshots()[0].conference_state is ConferenceState.FAILED
        await manager.stop()

    asyncio.run(scenario())


def test_disconnect_pauses_conference_until_device_reconnects(tmp_path: Path) -> None:
    async def scenario() -> None:
        adb = FakeAdb([AdbDevice("ONE", "SM-A566B", AdbState.DEVICE)])
        database = Database(tmp_path / "test.db")
        await database.initialize()
        controller = CallController(adb, database)  # type: ignore[arg-type]
        manager = DeviceManager(
            adb,
            controller,
            database,
            poll_interval=60,
            auto_merge=True,
            conference_call_timeout=30,
        )  # type: ignore[arg-type]
        await manager.refresh_once()
        await manager.assign_numbers(["8095550101", "8095550102"], "ONE")
        await manager.start_conference("ONE")
        for _ in range(100):
            if any(command[1][:2] == ("am", "start") for command in adb.commands):
                break
            await asyncio.sleep(0.01)

        adb.devices = []
        await manager.refresh_once()
        assert manager.snapshots()[0].conference_state is ConferenceState.WAITING_DEVICE

        adb.devices = [AdbDevice("ONE", "SM-A566B", AdbState.DEVICE)]
        await manager.refresh_once()
        assert manager.snapshots()[0].conference_state is ConferenceState.RECOVERED_PAUSED

        adb.devices = []
        await manager.refresh_once()
        await manager.cancel_recovered_conference("ONE")
        assert manager.snapshots()[0].conference_state is ConferenceState.CANCELLED
        await manager.stop()

    asyncio.run(scenario())
