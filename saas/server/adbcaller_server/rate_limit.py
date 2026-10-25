from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import case
from sqlalchemy.dialects.postgresql import insert

from adbcaller_server.database import session_factory, set_tenant
from adbcaller_server.models import RateLimitBucket


async def enforce_rate_limit(
    tenant_id: UUID, bucket_key: str, limit: int, window_seconds: int = 60
) -> None:
    now = datetime.now(UTC)
    stale_before = now - timedelta(seconds=window_seconds)
    statement = (
        insert(RateLimitBucket)
        .values(
            tenant_id=tenant_id,
            bucket_key=bucket_key,
            window_started_at=now,
            count=1,
        )
        .on_conflict_do_update(
            index_elements=["tenant_id", "bucket_key"],
            set_={
                "window_started_at": case(
                    (RateLimitBucket.window_started_at < stale_before, now),
                    else_=RateLimitBucket.window_started_at,
                ),
                "count": case(
                    (RateLimitBucket.window_started_at < stale_before, 1),
                    else_=RateLimitBucket.count + 1,
                ),
            },
        )
        .returning(RateLimitBucket.count)
    )
    async with session_factory() as session:
        await set_tenant(session, tenant_id)
        count = (await session.execute(statement)).scalar_one()
        await session.commit()
    if count > limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Demasiadas solicitudes; inténtelo de nuevo más tarde",
        )
