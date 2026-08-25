from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol, Sequence

from fleetplane.domain.enums import CommandStatus, DeviceLifecycle, HealthState, OutboxStatus
from fleetplane.domain.models import (
    AuditPage,
    AuditRecord,
    CommandAck,
    CommandPage,
    ConfigAck,
    DesiredConfiguration,
    DevicePage,
    DeviceState,
    DirectCommand,
    FleetSummary,
    OutboxMessage,
    TelemetryEnvelope,
)

TelemetryCommitStatus = Literal["committed", "duplicate", "conflict"]


class FleetStore(Protocol):
    def close(self) -> None: ...

    def get_device_state(self, device_id: str) -> DeviceState | None: ...

    def provision_device(self, *, state: DeviceState, audit: AuditRecord) -> bool: ...

    def transition_device_lifecycle(
        self,
        *,
        state: DeviceState,
        expected_projection_version: int,
        audit: AuditRecord,
    ) -> bool: ...

    def commit_telemetry(
        self,
        event: TelemetryEnvelope,
        received_at: datetime,
        state: DeviceState,
        expected_projection_version: int,
    ) -> TelemetryCommitStatus: ...

    def compare_and_swap_device_state(
        self,
        state: DeviceState,
        expected_projection_version: int,
    ) -> bool: ...

    def list_device_states_page(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        health_state: HealthState | None = None,
        lifecycle: DeviceLifecycle | None = None,
        site_id: str | None = None,
        fleet_id: str | None = None,
    ) -> DevicePage: ...

    def get_fleet_summary(self) -> FleetSummary: ...

    def get_desired_config(self, device_id: str) -> DesiredConfiguration | None: ...

    def save_desired_with_outbox(
        self,
        *,
        device_id: str,
        config: DesiredConfiguration,
        expected_revision: int,
        state: DeviceState,
        expected_projection_version: int,
        audit: AuditRecord,
        outbox: OutboxMessage,
    ) -> bool: ...

    def get_latest_config_ack(self, device_id: str) -> ConfigAck | None: ...

    def save_config_ack_with_state(
        self,
        *,
        ack: ConfigAck,
        state: DeviceState,
        expected_projection_version: int,
        audit: AuditRecord,
    ) -> bool: ...

    def create_command_with_outbox(
        self,
        *,
        command: DirectCommand,
        audit: AuditRecord,
        outbox: OutboxMessage,
    ) -> DirectCommand: ...

    def get_command(self, command_id: str) -> DirectCommand | None: ...

    def transition_command(
        self,
        *,
        command_id: str,
        expected_statuses: Sequence[CommandStatus],
        new_status: CommandStatus,
        ack: CommandAck | None = None,
        audit: AuditRecord | None = None,
    ) -> DirectCommand | None: ...

    def list_commands_page(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        device_id: str | None = None,
        status: CommandStatus | None = None,
    ) -> CommandPage: ...

    def list_expired_commands(self, now: datetime, limit: int = 100) -> list[DirectCommand]: ...

    def append_audit(self, record: AuditRecord) -> None: ...

    def list_audit_page(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        correlation_id: str | None = None,
        request_correlation_id: str | None = None,
        target: str | None = None,
    ) -> AuditPage: ...

    def claim_outbox(
        self,
        *,
        owner: str,
        now: datetime,
        lease_seconds: int,
        limit: int,
    ) -> list[OutboxMessage]: ...

    def update_outbox(
        self,
        *,
        outbox_id: str,
        owner: str,
        status: OutboxStatus,
        available_at: datetime | None = None,
        error: str | None = None,
    ) -> bool: ...
