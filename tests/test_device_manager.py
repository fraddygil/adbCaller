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
        manager.assign_numbers(["8095550101"], "ONE")

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
        manager.assign_numbers(["8095550101", "8095550102"], "ONE")
        manager.assign_numbers(["8095550103"], "TWO")

        results = await manager.start_all_conferences()

        assert results["ONE"] == {"ok": True, "total": 2}
        assert results["TWO"] == {"ok": True, "total": 1}
        await manager.stop()

    asyncio.run(scenario())
