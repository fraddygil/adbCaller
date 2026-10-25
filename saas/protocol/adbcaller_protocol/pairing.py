from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from adbcaller_protocol.envelope import MessageBase, MessageType, StrictModel


class PairRequestPayload(StrictModel):
    code: str = Field(min_length=8, max_length=128)
    agent_name: str = Field(min_length=1, max_length=100)
    agent_version: str = Field(min_length=1, max_length=50)
    platform: str = Field(min_length=1, max_length=50)


class PairResultPayload(StrictModel):
    agent_id: UUID
    token: str = Field(min_length=32, max_length=512)
    protocol_version: int


class PairRequestMessage(MessageBase):
    message_type: Literal[MessageType.PAIR_REQUEST] = MessageType.PAIR_REQUEST
    payload: PairRequestPayload


class PairResultMessage(MessageBase):
    message_type: Literal[MessageType.PAIR_RESULT] = MessageType.PAIR_RESULT
    payload: PairResultPayload
