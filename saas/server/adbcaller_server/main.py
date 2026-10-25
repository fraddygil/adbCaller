from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from adbcaller_server import __version__
from adbcaller_server.agent_ws import router as agent_router
from adbcaller_server.auth import router as auth_router
from adbcaller_server.config import get_settings
from adbcaller_server.devices import router as devices_router
from adbcaller_server.pairing import router as pairing_router
from adbcaller_server.number_lists import router as number_lists_router
from adbcaller_server.tasks import router as tasks_router
from adbcaller_server.users import router as users_router
from adbcaller_server.web import router as web_router
from adbcaller_server.conferences import (
    pause_running_conferences,
    router as conferences_router,
    stop_conference_tasks,
)
from adbcaller_server.history import router as history_router
from adbcaller_server.settings_api import router as settings_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    await pause_running_conferences()
    yield
    await stop_conference_tasks()


app = FastAPI(title="ADB Caller SaaS", version=__version__, lifespan=lifespan)
app.include_router(auth_router)
app.include_router(pairing_router)
app.include_router(number_lists_router)
app.include_router(agent_router)
app.include_router(devices_router)
app.include_router(tasks_router)
app.include_router(users_router)
app.include_router(web_router)
app.include_router(conferences_router)
app.include_router(history_router)
app.include_router(settings_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "adbcaller_server.main:app", host=settings.host, port=settings.port
    )
