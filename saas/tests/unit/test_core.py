import asyncio

import pytest

from adbcaller_core.adb_client import parse_devices
from adbcaller_core.call_controller import CallController
from adbcaller_core.call_state import parse_current_calls
from adbcaller_core.models import AdbState, CallState
from adbcaller_core.number_validation import normalize_number
from adbcaller_core.samsung_ui import (
    find_merge_button_center,
    find_speaker_button_center,
)
from fake_adb import FakeAdb


def test_parse_devices_preserves_non_ready_states() -> None:
    devices = parse_devices(
        """List of devices attached
ONE device model:SM-A566E
TWO unauthorized usb:2-1
THREE offline
"""
    )

    assert [(device.serial, device.state) for device in devices] == [
        ("ONE", AdbState.DEVICE),
        ("TWO", AdbState.UNAUTHORIZED),
        ("THREE", AdbState.OFFLINE),
    ]


def test_historical_calls_are_not_parsed_as_current() -> None:
    telecom = """CallsManager:
  mCalls:
  mCallAudioManager:
    History:
      Call[id=TC@old, state=ACTIVE]
"""

    assert parse_current_calls(telecom) == []


def test_number_validation_rejects_letters() -> None:
    assert normalize_number("809-555-0101") == "8095550101"
    with pytest.raises(ValueError):
        normalize_number("809ABC0101")


def test_samsung_controls_use_accessible_selectors() -> None:
    merge = """<hierarchy><node text="Merge"
      resource-id="com.samsung.android.incallui:id/merge_button"
      clickable="true" enabled="true" bounds="[657,497][825,649]" /></hierarchy>"""
    speaker = """<hierarchy><node content-desc="Off, Speaker, Button"
      resource-id="com.samsung.android.incallui:id/fourth_button"
      clickable="true" enabled="true" bounds="[108,1590][378,1840]" /></hierarchy>"""

    assert find_merge_button_center(merge) == (741, 573)
    assert find_speaker_button_center(speaker) == (243, 1715)


def test_call_controller_uses_serial_and_normalized_number() -> None:
    async def scenario() -> None:
        adb = FakeAdb()
        controller = CallController(adb)

        state = await controller.call("SERIAL-1", "809-555-0101")

        assert state is CallState.DIALING
        assert (
            "SERIAL-1",
            (
                "am",
                "start",
                "-a",
                "android.intent.action.CALL",
                "-d",
                "tel:8095550101",
            ),
        ) in adb.commands

    asyncio.run(scenario())


def test_call_controller_rechecks_idle_before_dialing() -> None:
    async def scenario() -> None:
        adb = FakeAdb()
        adb.outputs[("dumpsys", "telecom")] = """CallsManager:
  mCalls:
    Call[id=TC@1, state=ACTIVE]
  mCallAudioManager:
"""
        controller = CallController(adb)

        with pytest.raises(RuntimeError, match="IDLE"):
            await controller.call("SERIAL-1", "8095550101")

        assert not any(command[:2] == ("am", "start") for _, command in adb.commands)

    asyncio.run(scenario())
