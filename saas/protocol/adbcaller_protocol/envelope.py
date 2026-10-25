from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator


class MessageType(StrEnum):
    PAIR_REQUEST = "pair_request"
    PAIR_RESULT = "pair_result"
    HELLO = "hello"
    HELLO_RESULT = "hello_result"
    HEARTBEAT = "heartbeat"
    DEVICE_LIST = "device_list"
    DEVICE_STATE = "device_state"
    COMMAND = "command"
    COMMAND_RESULT = "command_result"
    EVENT_BATCH = "event_batch"
    ERROR = "error"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class MessageBase(StrictModel):
    version: int
    message_id: UUID
    message_type: MessageType
    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp debe incluir zona horaria")
        return value
