from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from fleetplane.domain.enums import (
    AckCode,
    CommandKind,
    CommandStatus,
    DeviceLifecycle,
    HealthState,
    IngestDisposition,
    OperatingMode,
    OutboxKind,
    OutboxStatus,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class TelemetryHealth(StrictModel):
    battery_pct: float = Field(ge=0, le=100)
    spool_depth: int = Field(ge=0)
    sensor_ok: bool = True
    inference_latency_ms: float = Field(ge=0)
    operating_mode: OperatingMode = OperatingMode.IDLE


class TelemetryEnvelope(StrictModel):
    schema_version: Literal[1] = 1
    event_id: str = Field(default_factory=lambda: str(uuid4()), min_length=8, max_length=128)
    device_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    device_generation: int = Field(default=1, ge=1)
    boot_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=0)
    observed_at: datetime
    model_version: str = Field(default="baseline", min_length=1, max_length=128)
    config_revision: int = Field(default=0, ge=0)
    anomaly_score: float = Field(default=0.0, ge=0, le=1)
    health: TelemetryHealth

    @field_validator("observed_at")
    @classmethod
    def normalize_observed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        return value.astimezone(UTC)


class DesiredConfiguration(StrictModel):
    revision: int = Field(ge=1)
    telemetry_interval_s: int = Field(default=1200, ge=10, le=86400)
    model_channel: str = Field(default="stable", min_length=1, max_length=64)
    anomaly_threshold: float = Field(default=0.82, ge=0, le=1)
    diagnostic_level: str = Field(default="normal", min_length=1, max_length=32)
    issued_at: datetime = Field(default_factory=utcnow)


class ConfigurationPatch(StrictModel):
    telemetry_interval_s: int | None = Field(default=None, ge=10, le=86400)
    model_channel: str | None = Field(default=None, min_length=1, max_length=64)
    anomaly_threshold: float | None = Field(default=None, ge=0, le=1)
    diagnostic_level: str | None = Field(default=None, min_length=1, max_length=32)


class ConfigAck(StrictModel):
    ack_id: str = Field(default_factory=lambda: str(uuid4()))
    device_id: str
    revision: int = Field(ge=1)
    code: AckCode
    reason: str | None = Field(default=None, max_length=512)
    responded_at: datetime = Field(default_factory=utcnow)


class DeviceState(StrictModel):
    device_id: str
    site_id: str = Field(default="default-site", min_length=1, max_length=128)
    fleet_id: str = Field(default="default-fleet", min_length=1, max_length=128)
    lifecycle: DeviceLifecycle = DeviceLifecycle.PROVISIONED
    provisioned_at: datetime = Field(default_factory=utcnow)
    lifecycle_changed_at: datetime = Field(default_factory=utcnow)
    device_generation: int = Field(default=1, ge=1)
    projection_version: int = Field(default=0, ge=0)
    last_boot_id: str | None = None
    last_sequence: int | None = Field(default=None, ge=0)
    last_observed_at: datetime | None = None
    last_received_at: datetime | None = None
    desired_revision: int = Field(default=0, ge=0)
    reported_revision: int = Field(default=0, ge=0)
    applied_telemetry_interval_s: int = Field(default=1200, ge=10, le=86400)
    model_version: str | None = None
    last_battery_pct: float | None = Field(default=None, ge=0, le=100)
    last_spool_depth: int = Field(default=0, ge=0)
    last_sensor_ok: bool = True
    last_inference_latency_ms: float | None = Field(default=None, ge=0)
    operating_mode: OperatingMode = OperatingMode.IDLE
    duplicate_count: int = Field(default=0, ge=0)
    out_of_order_count: int = Field(default=0, ge=0)
    gap_count: int = Field(default=0, ge=0)
    health_state: HealthState = HealthState.UNKNOWN
    health_reasons: list[str] = Field(default_factory=list)

    @property
    def config_converged(self) -> bool:
        return self.desired_revision > 0 and self.reported_revision >= self.desired_revision


class DeviceProvisionRequest(StrictModel):
    device_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    site_id: str = Field(default="default-site", min_length=1, max_length=128)
    fleet_id: str = Field(default="default-fleet", min_length=1, max_length=128)
    device_generation: int = Field(default=1, ge=1)


class DeviceLifecycleChange(StrictModel):
    lifecycle: DeviceLifecycle
    reason: str = Field(min_length=1, max_length=512)


class IngestOutcome(StrictModel):
    disposition: IngestDisposition
    device_id: str | None = None
    event_id: str | None = None
    reason: str | None = None
    missing_sequences: int = Field(default=0, ge=0)
    receive_lag_ms: float | None = Field(default=None, ge=0)


class DirectCommand(StrictModel):
    command_id: str = Field(default_factory=lambda: str(uuid4()))
    device_id: str
    kind: CommandKind
    actor: str
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=256)
    request_correlation_id: str | None = Field(default=None, max_length=128)
    requested_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    status: CommandStatus = CommandStatus.QUEUED


class CommandAck(StrictModel):
    ack_id: str = Field(default_factory=lambda: str(uuid4()))
    command_id: str
    device_id: str
    code: AckCode
    reason: str | None = Field(default=None, max_length=512)
    payload: dict[str, Any] = Field(default_factory=dict)
    responded_at: datetime = Field(default_factory=utcnow)


class AuditRecord(StrictModel):
    audit_id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=utcnow)
    actor: str
    action: str
    target: str
    correlation_id: str | None = None
    request_correlation_id: str | None = Field(default=None, max_length=128)
    details: dict[str, Any] = Field(default_factory=dict)


class OutboxMessage(StrictModel):
    outbox_id: str = Field(default_factory=lambda: str(uuid4()))
    device_id: str
    kind: OutboxKind
    correlation_id: str
    request_correlation_id: str | None = Field(default=None, max_length=128)
    payload: dict[str, Any]
    status: OutboxStatus = OutboxStatus.PENDING
    created_at: datetime = Field(default_factory=utcnow)
    available_at: datetime = Field(default_factory=utcnow)
    lease_owner: str | None = None
    lease_until: datetime | None = None
    attempts: int = Field(default=0, ge=0)
    last_error: str | None = None


class FleetSummary(StrictModel):
    total_devices: int = 0
    healthy: int = 0
    degraded: int = 0
    offline: int = 0
    unknown: int = 0
    config_converged: int = 0
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def config_not_converged(self) -> int:
        return max(0, self.total_devices - self.config_converged)


class DevicePage(StrictModel):
    items: list[DeviceState]
    next_cursor: str | None = None
    page_size: int


class CommandPage(StrictModel):
    items: list[DirectCommand]
    next_cursor: str | None = None
    page_size: int


class AuditPage(StrictModel):
    items: list[AuditRecord]
    next_cursor: str | None = None
    page_size: int
