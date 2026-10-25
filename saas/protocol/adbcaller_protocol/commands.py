from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from adbcaller_protocol.envelope import MessageBase, MessageType, StrictModel


class CommandAction(StrEnum):
    LIST_DEVICES = "LIST_DEVICES"
    STATUS = "STATUS"
    CALL = "CALL"
    ANSWER = "ANSWER"
    HANGUP = "HANGUP"
    HANGUP_ALL = "HANGUP_ALL"
    ADD_CALL = "ADD_CALL"
    MERGE = "MERGE"
    ENABLE_SPEAKER = "ENABLE_SPEAKER"


class CommandState(StrEnum):
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


class NoParameters(StrictModel):
    pass


class ListDevicesCommand(StrictModel):
    action: Literal[CommandAction.LIST_DEVICES] = CommandAction.LIST_DEVICES
    parameters: NoParameters = NoParameters()


class StatusCommand(StrictModel):
    action: Literal[CommandAction.STATUS] = CommandAction.STATUS
    parameters: NoParameters = NoParameters()


class CallParameters(StrictModel):
    number: str = Field(pattern=r"^\+?[0-9]{3,20}$")


class CallCommand(StrictModel):
    action: Literal[CommandAction.CALL] = CommandAction.CALL
    parameters: CallParameters


class AnswerCommand(StrictModel):
    action: Literal[CommandAction.ANSWER] = CommandAction.ANSWER
    parameters: NoParameters = NoParameters()


class HangupCommand(StrictModel):
    action: Literal[CommandAction.HANGUP] = CommandAction.HANGUP
    parameters: NoParameters = NoParameters()


class HangupAllCommand(StrictModel):
    action: Literal[CommandAction.HANGUP_ALL] = CommandAction.HANGUP_ALL
    parameters: NoParameters = NoParameters()


class AddCallCommand(StrictModel):
    action: Literal[CommandAction.ADD_CALL] = CommandAction.ADD_CALL
    parameters: CallParameters


class MergeCommand(StrictModel):
    action: Literal[CommandAction.MERGE] = CommandAction.MERGE
    parameters: NoParameters = NoParameters()


class EnableSpeakerCommand(StrictModel):
    action: Literal[CommandAction.ENABLE_SPEAKER] = CommandAction.ENABLE_SPEAKER
    parameters: NoParameters = NoParameters()


CommandPayload = Annotated[
    ListDevicesCommand
    | StatusCommand
    | CallCommand
    | AnswerCommand
    | HangupCommand
    | HangupAllCommand
    | AddCallCommand
    | MergeCommand
    | EnableSpeakerCommand,
    Field(discriminator="action"),
]


class CommandMessagePayload(StrictModel):
    command_id: UUID
    agent_id: UUID
    device_id: UUID | None = None
    created_at: datetime
    expires_at: datetime
    command: CommandPayload

    @model_validator(mode="after")
    def validate_target_and_expiration(self) -> "CommandMessagePayload":
        agent_actions = {CommandAction.LIST_DEVICES, CommandAction.HANGUP_ALL}
        if self.command.action in agent_actions and self.device_id is not None:
            raise ValueError("La acción dirigida al agente no acepta device_id")
        if self.command.action not in agent_actions and self.device_id is None:
            raise ValueError("La acción requiere device_id")
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("Las fechas del comando deben incluir zona horaria")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at debe ser posterior a created_at")
        return self


class CommandResultPayload(StrictModel):
    command_id: UUID
    state: CommandState
    result: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = Field(default=None, max_length=100)
    error_message: str | None = Field(default=None, max_length=1000)


class CommandMessage(MessageBase):
    message_type: Literal[MessageType.COMMAND] = MessageType.COMMAND
    payload: CommandMessagePayload


class CommandResultMessage(MessageBase):
    message_type: Literal[MessageType.COMMAND_RESULT] = MessageType.COMMAND_RESULT
    payload: CommandResultPayload
