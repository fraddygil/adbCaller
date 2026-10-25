from __future__ import annotations

import hashlib
import hmac
import secrets
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError


password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    validate_password(password)
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerifyMismatchError):
        return False


def validate_password(password: str) -> None:
    if len(password) < 12:
        raise ValueError("La contraseña debe tener al menos 12 caracteres")
    if len(password) > 1024:
        raise ValueError("La contraseña es demasiado larga")


def new_scoped_secret(tenant_id: UUID, subject_id: UUID | None = None) -> str:
    parts = [str(tenant_id)]
    if subject_id is not None:
        parts.append(str(subject_id))
    parts.append(secrets.token_urlsafe(32))
    return ".".join(parts)


def secret_hash(value: str, pepper: str) -> bytes:
    return hmac.new(pepper.encode(), value.encode(), hashlib.sha256).digest()


def csrf_token(session_token: str, session_secret: str) -> str:
    return hmac.new(
        session_secret.encode(), session_token.encode(), hashlib.sha256
    ).hexdigest()


def parse_scoped_secret(value: str, parts: int) -> tuple[UUID, ...]:
    values = value.split(".")
    if len(values) != parts + 1 or not values[-1]:
        raise ValueError("Credencial inválida")
    try:
        return tuple(UUID(item) for item in values[:-1])
    except ValueError as exc:
        raise ValueError("Credencial inválida") from exc
