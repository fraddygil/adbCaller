from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import WebSocket

from adbcaller_protocol.commands import CommandMessage


class AgentHub:
    def __init__(self) -> None:
        self._connections: dict[UUID, WebSocket] = {}
        self._lock = asyncio.Lock()

    async def connect(self, agent_id: UUID, websocket: WebSocket) -> None:
        async with self._lock:
            previous = self._connections.get(agent_id)
            self._connections[agent_id] = websocket
        if previous is not None and previous is not websocket:
            await previous.close(code=4009, reason="Otra conexión reemplazó esta sesión")

    async def disconnect(
        self, agent_id: UUID, websocket: WebSocket | None = None
    ) -> bool:
        async with self._lock:
            current = self._connections.get(agent_id)
            if current is None or (websocket is not None and current is not websocket):
                return False
            del self._connections[agent_id]
        if websocket is None:
            await current.close(code=4001, reason="Agente desconectado")
        return True

    async def send(self, agent_id: UUID, message: CommandMessage) -> bool:
        async with self._lock:
            websocket = self._connections.get(agent_id)
        if websocket is None:
            return False
        try:
            await websocket.send_text(message.model_dump_json())
            return True
        except RuntimeError:
            return False


agent_hub = AgentHub()
