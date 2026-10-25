from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from adbcaller_protocol.commands import (
    CallCommand,
    CallParameters,
    CommandMessage,
    CommandMessagePayload,
    HangupAllCommand,
)
from adbcaller_protocol.envelope import MessageType
from adbcaller_protocol.devices import (
    DeviceCallState,
    DeviceConnectionState,
    DeviceInfo,
)
from adbcaller_protocol.messages import parse_message
from adbcaller_protocol.version import PROTOCOL_VERSION, negotiate_version


def test_command_round_trip_uses_discriminated_action() -> None:
    now = datetime.now(UTC)
    message = CommandMessage(
        version=PROTOCOL_VERSION,
        message_id=uuid4(),
        message_type=MessageType.COMMAND,
        timestamp=now,
        payload=CommandMessagePayload(
            command_id=uuid4(),
            agent_id=uuid4(),
            device_id=uuid4(),
            created_at=now,
            expires_at=now + timedelta(seconds=30),
            command=CallCommand(parameters=CallParameters(number="+18095550101")),
        ),
    )

    restored = parse_message(message.model_dump_json())

    assert isinstance(restored, CommandMessage)
    assert isinstance(restored.payload.command, CallCommand)
    assert restored.payload.command.parameters.number == "+18095550101"


def test_protocol_rejects_unknown_fields_and_invalid_numbers() -> None:
    with pytest.raises(ValidationError):
        CallParameters(number="809ABC0101")
    with pytest.raises(ValidationError):
        CallParameters(number="8095550101", unexpected=True)  # type: ignore[call-arg]


def test_agent_level_command_rejects_device_target() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="no acepta device_id"):
        CommandMessagePayload(
            command_id=uuid4(),
            agent_id=uuid4(),
            device_id=uuid4(),
            created_at=now,
            expires_at=now + timedelta(seconds=30),
            command=HangupAllCommand(),
        )


def test_protocol_version_negotiation() -> None:
    assert negotiate_version(1, 1) == 1
    assert negotiate_version(2, 3) is None


def test_device_report_includes_stable_public_id() -> None:
    device_id = uuid4()
    device = DeviceInfo(
        device_id=device_id,
        local_serial="SERIAL-1",
        model="SM-A566E",
        manufacturer="Samsung",
        android_version="16",
        api_level="36",
        one_ui_version="80500",
        build_fingerprint="sanitized",
        connection_state=DeviceConnectionState.DEVICE,
        call_state=DeviceCallState.IDLE,
        capabilities=frozenset({"STATUS"}),
    )

    assert device.device_id == device_id
