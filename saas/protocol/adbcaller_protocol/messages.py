from __future__ import annotations

from typing import Annotated

from pydantic import Field, TypeAdapter

from adbcaller_protocol.commands import CommandMessage, CommandResultMessage
from adbcaller_protocol.devices import DeviceListMessage, DeviceStateMessage
from adbcaller_protocol.events import (
    ErrorMessage,
    EventBatchMessage,
    HeartbeatMessage,
    HelloMessage,
    HelloResultMessage,
)
from adbcaller_protocol.pairing import PairRequestMessage, PairResultMessage


ProtocolMessage = Annotated[
    PairRequestMessage
    | PairResultMessage
    | HelloMessage
    | HelloResultMessage
    | HeartbeatMessage
    | DeviceListMessage
    | DeviceStateMessage
    | CommandMessage
    | CommandResultMessage
    | EventBatchMessage
    | ErrorMessage,
    Field(discriminator="message_type"),
]

PROTOCOL_MESSAGE_ADAPTER = TypeAdapter(ProtocolMessage)


def parse_message(value: str | bytes) -> ProtocolMessage:
    return PROTOCOL_MESSAGE_ADAPTER.validate_json(value)
