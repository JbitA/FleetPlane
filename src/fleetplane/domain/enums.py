from __future__ import annotations

from enum import StrEnum


class IngestDisposition(StrEnum):
    ACCEPTED = "accepted"
    ACCEPTED_WITH_GAP = "accepted_with_gap"
    ACCEPTED_OUT_OF_ORDER = "accepted_out_of_order"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"


class DeviceLifecycle(StrEnum):
    PROVISIONED = "provisioned"
    ACTIVE = "active"
    QUARANTINED = "quarantined"
    DISABLED = "disabled"
    DECOMMISSIONED = "decommissioned"


class HealthState(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class OperatingMode(StrEnum):
    IDLE = "idle"
    ACTIVE = "active"
    MAINTENANCE = "maintenance"


class AckCode(StrEnum):
    APPLIED = "applied"
    ACCEPTED = "accepted"
    REJECTED_POLICY = "rejected_policy"
    REJECTED_STALE = "rejected_stale"
    REJECTED = "rejected"
    FAILED = "failed"


class CommandKind(StrEnum):
    PING = "ping"
    COLLECT_DIAGNOSTICS = "collect_diagnostics"
    RESTART_APPLICATION = "restart_application"


class CommandStatus(StrEnum):
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"
    DELIVERY_FAILED = "delivery_failed"


class OutboxKind(StrEnum):
    DESIRED_CONFIGURATION = "desired_configuration"
    DIRECT_COMMAND = "direct_command"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    DONE = "done"
    FAILED = "failed"
