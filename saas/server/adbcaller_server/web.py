from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Cookie, HTTPException, status
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import select

from adbcaller_server.auth import SESSION_COOKIE
from adbcaller_server.config import get_settings
from adbcaller_server.database import session_factory, set_tenant
from adbcaller_server.models import User, UserSession
from adbcaller_server.realtime import event_hub
from adbcaller_server.security import parse_scoped_secret, secret_hash


router = APIRouter(include_in_schema=False)
INDEX_PATH = Path(__file__).parent / "templates" / "index.html"


@router.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(INDEX_PATH.read_text(encoding="utf-8"))


@router.get("/events")
async def events(
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> StreamingResponse:
    if session_token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesión requerida")
    try:
        (tenant_id,) = parse_scoped_secret(session_token, 1)
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesión inválida") from exc

    settings = get_settings()
    async with session_factory() as session:
        await set_tenant(session, tenant_id)
        valid = (
            await session.execute(
                select(UserSession.id)
                .join(
                    User,
                    (User.tenant_id == UserSession.tenant_id)
                    & (User.id == UserSession.user_id),
                )
                .where(
                    UserSession.tenant_id == tenant_id,
                    UserSession.token_hash
                    == secret_hash(session_token, settings.token_pepper),
                    UserSession.revoked_at.is_(None),
                    UserSession.expires_at > datetime.now(UTC),
                    User.active.is_(True),
                )
            )
        ).scalar_one_or_none()
    if valid is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesión inválida o expirada")
    return StreamingResponse(
        event_hub.subscribe(tenant_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
