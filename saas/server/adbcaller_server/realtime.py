from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID


class EventHub:
    def __init__(self) -> None:
        self._subscribers: dict[UUID, set[asyncio.Queue[str]]] = defaultdict(set)

    async def publish(
        self, tenant_id: UUID, event_type: str, payload: dict[str, Any]
    ) -> None:
        message = json.dumps({"type": event_type, "payload": payload})
        for queue in tuple(self._subscribers.get(tenant_id, ())):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(message)

    async def subscribe(self, tenant_id: UUID) -> AsyncIterator[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
        self._subscribers[tenant_id].add(queue)
        try:
            yield "event: ready\ndata: {}\n\n"
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"event: update\ndata: {message}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            subscribers = self._subscribers.get(tenant_id)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    self._subscribers.pop(tenant_id, None)


event_hub = EventHub()
