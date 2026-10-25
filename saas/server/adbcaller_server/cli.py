from __future__ import annotations

import argparse
import asyncio
import getpass
import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from adbcaller_server.config import get_settings
from adbcaller_server.models import AuditLog, Tenant, User
from adbcaller_server.models.enums import UserRole
from adbcaller_server.security import hash_password


SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Administración de ADB Caller SaaS")
    subparsers = parser.add_subparsers(dest="command", required=True)
    bootstrap = subparsers.add_parser(
        "bootstrap-tenant", description="Crea un tenant y su primer administrador"
    )
    bootstrap.add_argument("--name", required=True)
    bootstrap.add_argument("--slug", required=True)
    bootstrap.add_argument("--email", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "bootstrap-tenant":
        password = getpass.getpass("Contraseña del administrador: ")
        confirmation = getpass.getpass("Repita la contraseña: ")
        if password != confirmation:
            raise SystemExit("Las contraseñas no coinciden")
        return asyncio.run(
            bootstrap_tenant(args.name, args.slug, args.email, password)
        )
    return 1


async def bootstrap_tenant(name: str, slug: str, email: str, password: str) -> int:
    name = name.strip()
    slug = slug.strip().casefold()
    email = email.strip().casefold()
    if not name or len(name) > 200:
        raise ValueError("El nombre debe tener entre 1 y 200 caracteres")
    if not SLUG_PATTERN.fullmatch(slug) or len(slug) > 100:
        raise ValueError("El slug solo admite minúsculas, números y guiones")
    if email.count("@") != 1 or len(email) > 320:
        raise ValueError("Email inválido")
    password_hash = await asyncio.to_thread(hash_password, password)

    settings = get_settings()
    engine = create_async_engine(settings.migration_database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            existing = (
                await session.execute(select(Tenant.id).where(Tenant.slug == slug))
            ).scalar_one_or_none()
            if existing is not None:
                raise ValueError(f"Ya existe el tenant {slug!r}")

            tenant = Tenant(name=name, slug=slug)
            session.add(tenant)
            await session.flush()
            user = User(
                tenant_id=tenant.id,
                email=email,
                password_hash=password_hash,
                role=UserRole.ADMIN,
            )
            session.add(user)
            await session.flush()
            session.add(
                AuditLog(
                    tenant_id=tenant.id,
                    user_id=user.id,
                    action="tenant.bootstrap",
                    outcome="SUCCESS",
                    metadata_json={},
                    created_at=datetime.now(UTC),
                )
            )
            await session.commit()
            print(f"Tenant {slug!r} creado con administrador {email!r}")
            return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
