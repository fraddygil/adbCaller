from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from adbcaller_protocol.envelope import MessageBase, MessageType, StrictModel


class HelloPayload(StrictModel):
    agent_id: UUID
    agent_version: str = Field(min_length=1, max_length=50)
    minimum_protocol_version: int = Field(ge=1)
    maximum_protocol_version: int = Field(ge=1)
    last_event_sequence: int = Field(default=0, ge=0)


class HelloResultPayload(StrictModel):
    accepted: bool
    protocol_version: int | None = None
    minimum_agent_version: str | None = Field(default=None, max_length=50)
    error: str | None = Field(default=None, max_length=500)


class HeartbeatPayload(StrictModel):
    agent_id: UUID
    sequence: int = Field(ge=0)
    active_commands: int = Field(default=0, ge=0, le=100)


class AgentEvent(StrictModel):
    event_id: UUID
    sequence: int = Field(ge=1)
    event_type: str = Field(min_length=1, max_length=100)
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class EventBatchPayload(StrictModel):
    events: list[AgentEvent] = Field(max_length=500)


class ErrorPayload(StrictModel):
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=1000)
    reference_id: UUID | None = None


class HelloMessage(MessageBase):
    message_type: Literal[MessageType.HELLO] = MessageType.HELLO
    payload: HelloPayload


class HelloResultMessage(MessageBase):
    message_type: Literal[MessageType.HELLO_RESULT] = MessageType.HELLO_RESULT
    payload: HelloResultPayload


class HeartbeatMessage(MessageBase):
    message_type: Literal[MessageType.HEARTBEAT] = MessageType.HEARTBEAT
    payload: HeartbeatPayload


class EventBatchMessage(MessageBase):
    message_type: Literal[MessageType.EVENT_BATCH] = MessageType.EVENT_BATCH
    payload: EventBatchPayload


class ErrorMessage(MessageBase):
    message_type: Literal[MessageType.ERROR] = MessageType.ERROR
    payload: ErrorPayload
