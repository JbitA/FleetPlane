from __future__ import annotations

import random
import time

from fleetplane.core.health import classify_health
from fleetplane.core.lifecycle import ensure_configurable
from fleetplane.domain.enums import AckCode, HealthState, OutboxKind
from fleetplane.domain.models import (
    AuditRecord,
    ConfigAck,
    ConfigurationPatch,
    DesiredConfiguration,
    DeviceState,
    OutboxMessage,
)
from fleetplane.ports.store import FleetStore


class ConfigurationConflictError(RuntimeError):
    pass


class ConfigurationService:
    def __init__(self, store: FleetStore, conflict_deadline_seconds: float = 2.0) -> None:
        self.store = store
        self.conflict_deadline_seconds = conflict_deadline_seconds

    def set_desired(
        self,
        device_id: str,
        patch: ConfigurationPatch,
        actor: str,
        request_correlation_id: str | None = None,
    ) -> DesiredConfiguration:
        deadline = time.monotonic() + self.conflict_deadline_seconds
        attempt = 0
        while True:
            ensure_configurable(self.store.get_device_state(device_id), device_id)
            current = self.store.get_desired_config(device_id)
            expected_revision = current.revision if current is not None else 0
            revision = expected_revision + 1
            base = current or DesiredConfiguration(revision=revision)
            values = base.model_dump()
            values.update({k: v for k, v in patch.model_dump().items() if v is not None})
            values["revision"] = revision
            values.pop("issued_at", None)
            candidate = DesiredConfiguration.model_validate(values)

            existing_state = self.store.get_device_state(device_id)
            expected_projection = existing_state.projection_version if existing_state else 0
            state = existing_state.model_copy(deep=True) if existing_state else DeviceState(device_id=device_id)
            state.desired_revision = candidate.revision
            state.health_state, state.health_reasons = classify_health(state)
            operation_id = f"config:{device_id}:{candidate.revision}"
            audit = AuditRecord(
                actor=actor,
                action="configuration.desired.set",
                target=device_id,
                correlation_id=operation_id,
                request_correlation_id=request_correlation_id,
                details=candidate.model_dump(mode="json"),
            )
            outbox = OutboxMessage(
                device_id=device_id,
                kind=OutboxKind.DESIRED_CONFIGURATION,
                correlation_id=operation_id,
                request_correlation_id=request_correlation_id,
                payload=candidate.model_dump(mode="json"),
            )
            if self.store.save_desired_with_outbox(
                device_id=device_id,
                config=candidate,
                expected_revision=expected_revision,
                state=state,
                expected_projection_version=expected_projection,
                audit=audit,
                outbox=outbox,
            ):
                return candidate
            if time.monotonic() >= deadline:
                raise ConfigurationConflictError("desired configuration contention deadline exceeded")
            attempt += 1
            time.sleep(min(0.025, 0.0005 * (2 ** min(attempt, 5))) * random.uniform(0.5, 1.5))

    def record_ack(self, ack: ConfigAck, actor: str = "device") -> None:
        deadline = time.monotonic() + self.conflict_deadline_seconds
        attempt = 0
        while True:
            desired = self.store.get_desired_config(ack.device_id)
            existing = self.store.get_device_state(ack.device_id)
            expected = existing.projection_version if existing else 0
            state = existing.model_copy(deep=True) if existing else DeviceState(device_id=ack.device_id)
            if desired is not None:
                state.desired_revision = desired.revision
            ack_is_current = desired is None or ack.revision >= desired.revision
            if ack.code == AckCode.APPLIED:
                state.reported_revision = max(state.reported_revision, ack.revision)
                if desired is not None and ack.revision == desired.revision:
                    state.applied_telemetry_interval_s = desired.telemetry_interval_s
            state.health_state, reasons = classify_health(state)
            if ack.code != AckCode.APPLIED and ack_is_current:
                reasons = sorted(set([*reasons, f"config_{ack.code.value}"]))
                if state.health_state != HealthState.OFFLINE:
                    state.health_state = HealthState.DEGRADED
            state.health_reasons = reasons
            audit = AuditRecord(
                actor=actor,
                action="configuration.ack" if ack_is_current else "configuration.ack.stale",
                target=ack.device_id,
                correlation_id=f"config:{ack.device_id}:{ack.revision}",
                details={**ack.model_dump(mode="json"), "ack_id": ack.ack_id},
            )
            if self.store.save_config_ack_with_state(
                ack=ack,
                state=state,
                expected_projection_version=expected,
                audit=audit,
            ):
                return
            if time.monotonic() >= deadline:
                raise ConfigurationConflictError("configuration acknowledgement contention deadline exceeded")
            attempt += 1
            time.sleep(min(0.025, 0.0005 * (2 ** min(attempt, 5))) * random.uniform(0.5, 1.5))
