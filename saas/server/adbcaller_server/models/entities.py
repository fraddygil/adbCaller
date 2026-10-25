from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from adbcaller_server.models.base import (
    Base,
    IdMixin,
    TenantOwnedMixin,
    TimestampMixin,
)
from adbcaller_server.models.enums import (
    AgentStatus,
    ConferenceState,
    NumberState,
    ParticipantState,
    TaskState,
    TenantStatus,
    UserRole,
)


def enum_column(enum_class: type, name: str) -> Enum:
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
    )


class Tenant(IdMixin, TimestampMixin, Base):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    status: Mapped[TenantStatus] = mapped_column(
        enum_column(TenantStatus, "tenant_status"),
        default=TenantStatus.ACTIVE,
        nullable=False,
    )
    plan: Mapped[str] = mapped_column(String(100), default="pilot", nullable=False)
    max_devices: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    max_concurrent_calls: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    command_rate_limit: Mapped[int] = mapped_column(Integer, default=60, nullable=False)


class User(IdMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "email"),
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        enum_column(UserRole, "user_role"), nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(IdMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "user_sessions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="CASCADE",
        ),
        Index("ix_user_sessions_token_hash", "token_hash", unique=True),
    )

    user_id: Mapped[UUID] = mapped_column(nullable=False)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PairingCode(IdMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "pairing_codes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="CASCADE",
        ),
        Index("ix_pairing_codes_code_hash", "code_hash", unique=True),
    )

    created_by_user_id: Mapped[UUID] = mapped_column(nullable=False)
    code_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)


class Agent(IdMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "name"),
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[AgentStatus] = mapped_column(
        enum_column(AgentStatus, "agent_status"),
        default=AgentStatus.OFFLINE,
        nullable=False,
    )
    agent_version: Mapped[str] = mapped_column(String(50), nullable=False)
    protocol_version: Mapped[int] = mapped_column(Integer, nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentToken(IdMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "agent_tokens"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id"],
            ["agents.tenant_id", "agents.id"],
            ondelete="CASCADE",
        ),
        Index("ix_agent_tokens_token_hash", "token_hash", unique=True),
    )

    agent_id: Mapped[UUID] = mapped_column(nullable=False)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Device(IdMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "devices"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "agent_id", "adb_serial"),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id"],
            ["agents.tenant_id", "agents.id"],
            ondelete="CASCADE",
        ),
    )

    agent_id: Mapped[UUID] = mapped_column(nullable=False)
    adb_serial: Mapped[str] = mapped_column(String(255), nullable=False)
    manufacturer: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    model: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    android_version: Mapped[str] = mapped_column(String(50), default="", nullable=False)
    api_level: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    one_ui_version: Mapped[str] = mapped_column(String(50), default="", nullable=False)
    build_fingerprint: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    adb_state: Mapped[str] = mapped_column(String(30), nullable=False)
    call_state: Mapped[str] = mapped_column(String(30), default="IDLE", nullable=False)
    call_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    online: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    blocked_by_limit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    capabilities: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TenantSetting(TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "tenant_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        primary_key=True, nullable=False, index=True
    )
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_by_user_id: Mapped[UUID | None] = mapped_column()

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
    )


class NumberList(IdMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "number_lists"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(nullable=False)


class NumberEntry(IdMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "numbers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "number_list_id"],
            ["number_lists.tenant_id", "number_lists.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "assigned_device_id"],
            ["devices.tenant_id", "devices.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["tasks.tenant_id", "tasks.id"],
            ondelete="RESTRICT",
        ),
        Index("ix_numbers_tenant_list_state", "tenant_id", "number_list_id", "state"),
    )

    number_list_id: Mapped[UUID] = mapped_column(nullable=False)
    normalized_number: Mapped[str] = mapped_column(String(21), nullable=False)
    assigned_device_id: Mapped[UUID | None] = mapped_column()
    task_id: Mapped[UUID | None] = mapped_column()
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[NumberState] = mapped_column(
        enum_column(NumberState, "number_state"),
        default=NumberState.PENDING,
        nullable=False,
    )
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)


class Task(IdMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "idempotency_key"),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id"],
            ["agents.tenant_id", "agents.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "device_id"],
            ["devices.tenant_id", "devices.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
        Index("ix_tasks_dispatch", "state", "priority", "created_at"),
        Index("ix_tasks_device_order", "tenant_id", "device_id", "created_at"),
    )

    agent_id: Mapped[UUID] = mapped_column(nullable=False)
    device_id: Mapped[UUID | None] = mapped_column()
    created_by_user_id: Mapped[UUID] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    state: Mapped[TaskState] = mapped_column(
        enum_column(TaskState, "task_state"), default=TaskState.QUEUED, nullable=False
    )
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    timeout_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)


class Call(IdMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "calls"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id"],
            ["agents.tenant_id", "agents.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "device_id"],
            ["devices.tenant_id", "devices.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["tasks.tenant_id", "tasks.id"],
            ondelete="RESTRICT",
        ),
        Index("ix_calls_tenant_device_started", "tenant_id", "device_id", "started_at"),
    )

    agent_id: Mapped[UUID] = mapped_column(nullable=False)
    device_id: Mapped[UUID] = mapped_column(nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    normalized_number: Mapped[str] = mapped_column(String(21), nullable=False)
    state: Mapped[str] = mapped_column(String(30), nullable=False)
    result: Mapped[str] = mapped_column(String(50), default="STARTED", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConferenceSession(IdMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "conference_sessions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "device_id"],
            ["devices.tenant_id", "devices.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
    )

    device_id: Mapped[UUID] = mapped_column(nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(nullable=False)
    state: Mapped[ConferenceState] = mapped_column(
        enum_column(ConferenceState, "conference_state"), nullable=False
    )
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)


class ConferenceParticipant(IdMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "conference_participants"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "conference_session_id", "position"),
        ForeignKeyConstraint(
            ["tenant_id", "conference_session_id"],
            ["conference_sessions.tenant_id", "conference_sessions.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["tasks.tenant_id", "tasks.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "number_entry_id"],
            ["numbers.tenant_id", "numbers.id"],
            ondelete="RESTRICT",
        ),
    )

    conference_session_id: Mapped[UUID] = mapped_column(nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    normalized_number: Mapped[str] = mapped_column(String(21), nullable=False)
    state: Mapped[ParticipantState] = mapped_column(
        enum_column(ParticipantState, "participant_state"), nullable=False
    )
    task_id: Mapped[UUID | None] = mapped_column()
    number_entry_id: Mapped[UUID | None] = mapped_column()
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)


class AgentEvent(IdMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "agent_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "agent_id", "event_id"),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id"],
            ["agents.tenant_id", "agents.id"],
            ondelete="CASCADE",
        ),
    )

    agent_id: Mapped[UUID] = mapped_column(nullable=False)
    event_id: Mapped[UUID] = mapped_column(nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditLog(IdMixin, TenantOwnedMixin, Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        Index("ix_audit_tenant_created", "tenant_id", "created_at"),
    )

    user_id: Mapped[UUID | None] = mapped_column()
    agent_id: Mapped[UUID | None] = mapped_column()
    device_id: Mapped[UUID | None] = mapped_column()
    task_id: Mapped[UUID | None] = mapped_column()
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    outcome: Mapped[str] = mapped_column(String(50), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, nullable=False
    )
    ip_address: Mapped[str | None] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RateLimitBucket(TenantOwnedMixin, Base):
    __tablename__ = "rate_limit_buckets"

    bucket_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True, nullable=False, index=True)
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], ondelete="CASCADE"
        ),
    )
