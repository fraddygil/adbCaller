import asyncio

import pytest

from call_controller import (
    CallController,
    find_merge_button_center,
    find_speaker_button_center,
    normalize_number,
    parse_current_calls,
)
from models import CallState
from tests.fake_adb import FakeAdb


def test_number_rejects_letters() -> None:
    with pytest.raises(ValueError, match="Número inválido"):
        normalize_number("809ABC1234")


def test_parse_multiple_current_calls() -> None:
    telecom = """CallsManager:
  mCalls:
    Call TC@1: mState: HOLDING
    Call[id=TC@2, state=ACTIVE]
  mCallAudioManager:
    History:
      Call[id=TC@0, state=RINGING]
"""

    calls = parse_current_calls(telecom)

    assert [(call.call_id, call.state) for call in calls] == [
        ("TC@1", CallState.HOLDING),
        ("TC@2", CallState.ACTIVE),
    ]


def test_add_call_requires_an_active_call() -> None:
    async def scenario() -> None:
        controller = CallController(FakeAdb())  # type: ignore[arg-type]

        with pytest.raises(RuntimeError, match="ACTIVE"):
            await controller.add_call("SERIAL-1", "8095550102")

    asyncio.run(scenario())


def test_add_call_starts_second_call() -> None:
    async def scenario() -> None:
        adb = FakeAdb()
        adb.outputs[("dumpsys", "telecom")] = """CallsManager:
  mCalls:
    Call[id=TC@1, state=ACTIVE]
  mCallAudioManager:
"""
        adb.outputs[("dumpsys", "telephony.registry")] = "mCallState=2"
        controller = CallController(adb)  # type: ignore[arg-type]

        state = await controller.add_call("SERIAL-1", "8095550102")

        assert state is CallState.DIALING
        assert ("SERIAL-1", ("am", "start", "-a", "android.intent.action.CALL", "-d", "tel:8095550102")) in adb.commands

    asyncio.run(scenario())


def test_find_merge_button_uses_samsung_resource_id() -> None:
    hierarchy = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node text="Merge" resource-id="com.samsung.android.incallui:id/merge_button"
        class="android.widget.Button" clickable="true" enabled="true"
        bounds="[657,497][825,649]" />
</hierarchy>"""

    assert find_merge_button_center(hierarchy) == (741, 573)


def test_merge_calls_taps_detected_button() -> None:
    async def scenario() -> None:
        adb = FakeAdb()
        adb.outputs[("dumpsys", "telecom")] = """CallsManager:
  mCalls:
    Call[id=TC@1, state=ACTIVE]
  mCallAudioManager:
"""
        adb.outputs[("dumpsys", "telephony.registry")] = "mCallState=2"
        adb.outputs[("cat", "/sdcard/adbcaller-window.xml")] = """<hierarchy>
  <node text="Merge" resource-id="com.samsung.android.incallui:id/merge_button"
        class="android.widget.Button" clickable="true" enabled="true"
        bounds="[657,497][825,649]" />
</hierarchy>"""
        controller = CallController(adb)  # type: ignore[arg-type]

        await controller.merge_calls("SERIAL-1")

        assert ("SERIAL-1", ("input", "tap", "741", "573")) in adb.commands

    asyncio.run(scenario())


def test_merge_calls_rejects_idle_device() -> None:
    async def scenario() -> None:
        controller = CallController(FakeAdb())  # type: ignore[arg-type]

        with pytest.raises(RuntimeError, match="No hay llamadas"):
            await controller.merge_calls("SERIAL-1")

    asyncio.run(scenario())


def test_find_speaker_button_uses_accessible_label() -> None:
    hierarchy = """<hierarchy>
  <node text="" content-desc="Off, Speaker, Button"
        resource-id="com.samsung.android.incallui:id/fourth_button"
        class="android.widget.LinearLayout" clickable="true" enabled="true"
        bounds="[108,1590][378,1840]" />
</hierarchy>"""

    assert find_speaker_button_center(hierarchy) == (243, 1715)


def test_active_hook_enables_speaker() -> None:
    async def scenario() -> None:
        adb = FakeAdb()
        adb.outputs[("dumpsys", "telecom")] = (
            "Current route: AudioRoute[Type=TYPE_EARPIECE, Address=invalid]"
        )
        adb.outputs[("cat", "/sdcard/adbcaller-speaker-window.xml")] = """<hierarchy>
  <node text="" content-desc="Off, Speaker, Button"
        resource-id="com.samsung.android.incallui:id/fourth_button"
        clickable="true" enabled="true" bounds="[108,1590][378,1840]" />
</hierarchy>"""
        controller = CallController(adb, auto_speaker=True)  # type: ignore[arg-type]

        await controller.on_call_active("SERIAL-1")

        assert ("SERIAL-1", ("input", "tap", "243", "1715")) in adb.commands

    asyncio.run(scenario())


def test_call_uses_requested_intent_and_serial() -> None:
    async def scenario() -> None:
        adb = FakeAdb()
        controller = CallController(adb)  # type: ignore[arg-type]

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


def test_telecom_state_has_priority_over_registry() -> None:
    async def scenario() -> None:
        adb = FakeAdb()
        adb.outputs[("dumpsys", "telecom")] = """CallsManager:
  mCalls:
    Call TC@1: mState: HOLDING
  mCallAudioManager:
    History:
      Call TC@0: state=RINGING
"""
        adb.outputs[("dumpsys", "telephony.registry")] = "mCallState=2"
        controller = CallController(adb)  # type: ignore[arg-type]

        assert await controller.get_call_state("SERIAL-1") is CallState.HOLDING

    asyncio.run(scenario())


def test_historical_telecom_calls_are_ignored() -> None:
    async def scenario() -> None:
        adb = FakeAdb()
        adb.outputs[("dumpsys", "telecom")] = """CallsManager:
  mCalls:
  mCallAudioManager:
    Call Route History:
      Call TC@1: state=RINGING
      Call TC@2: state=ACTIVE
"""
        adb.outputs[("dumpsys", "telephony.registry")] = "mCallState=0\nmCallState=0"
        controller = CallController(adb)  # type: ignore[arg-type]

        assert await controller.get_call_state("SERIAL-1") is CallState.IDLE

    asyncio.run(scenario())


def test_registry_is_used_as_fallback() -> None:
    async def scenario() -> None:
        adb = FakeAdb()
        adb.outputs[("dumpsys", "telephony.registry")] = "mCallState=1"
        controller = CallController(adb)  # type: ignore[arg-type]

        assert await controller.get_call_state("SERIAL-1") is CallState.RINGING

    asyncio.run(scenario())
