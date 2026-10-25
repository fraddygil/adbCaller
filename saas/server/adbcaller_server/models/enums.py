from enum import StrEnum


class TenantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class UserRole(StrEnum):
    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"


class AgentStatus(StrEnum):
    OFFLINE = "OFFLINE"
    ONLINE = "ONLINE"
    REVOKED = "REVOKED"


class NumberState(StrEnum):
    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskState(StrEnum):
    QUEUED = "QUEUED"
    SENT = "SENT"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    UNCERTAIN = "UNCERTAIN"


class ConferenceState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    ACTIVE = "ACTIVE"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ParticipantState(StrEnum):
    PENDING = "PENDING"
    DIALING = "DIALING"
    ACTIVE = "ACTIVE"
    MERGED = "MERGED"
    NO_ANSWER = "NO_ANSWER"
    REJECTED = "REJECTED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
