from __future__ import annotations

import random
import time
from datetime import UTC, datetime

from fleetplane.core.health import classify_health
from fleetplane.domain.enums import DeviceLifecycle
from fleetplane.domain.models import AuditRecord, DeviceProvisionRequest, DeviceState
from fleetplane.ports.store import FleetStore


class DeviceLifecycleError(RuntimeError):
    pass


class DeviceNotFoundError(DeviceLifecycleError):
    pass


class DeviceAlreadyExistsError(DeviceLifecycleError):
    pass


class DeviceLifecycleConflictError(DeviceLifecycleError):
    pass


_ALLOWED_TRANSITIONS: dict[DeviceLifecycle, set[DeviceLifecycle]] = {
    DeviceLifecycle.PROVISIONED: {
        DeviceLifecycle.ACTIVE,
        DeviceLifecycle.QUARANTINED,
        DeviceLifecycle.DISABLED,
        DeviceLifecycle.DECOMMISSIONED,
    },
    DeviceLifecycle.ACTIVE: {
        DeviceLifecycle.QUARANTINED,
        DeviceLifecycle.DISABLED,
        DeviceLifecycle.DECOMMISSIONED,
    },
    DeviceLifecycle.QUARANTINED: {
        DeviceLifecycle.ACTIVE,
        DeviceLifecycle.DISABLED,
        DeviceLifecycle.DECOMMISSIONED,
    },
    DeviceLifecycle.DISABLED: {
        DeviceLifecycle.ACTIVE,
        DeviceLifecycle.QUARANTINED,
        DeviceLifecycle.DECOMMISSIONED,
    },
    DeviceLifecycle.DECOMMISSIONED: set(),
}


class DeviceLifecycleService:
    def __init__(self, store: FleetStore, conflict_deadline_seconds: float = 2.0) -> None:
        self.store = store
        self.conflict_deadline_seconds = conflict_deadline_seconds

    def provision(
        self, request: DeviceProvisionRequest, actor: str, request_correlation_id: str | None = None
    ) -> DeviceState:
        now = datetime.now(UTC)
        state = DeviceState(
            device_id=request.device_id,
            site_id=request.site_id,
            fleet_id=request.fleet_id,
            lifecycle=DeviceLifecycle.PROVISIONED,
            provisioned_at=now,
            lifecycle_changed_at=now,
            device_generation=request.device_generation,
        )
        state.health_state, state.health_reasons = classify_health(state, now)
        audit = AuditRecord(
            actor=actor,
            action="device.provisioned",
            target=request.device_id,
            correlation_id=f"device:{request.device_id}:provision",
            request_correlation_id=request_correlation_id,
            details={
                "site_id": request.site_id,
                "fleet_id": request.fleet_id,
                "device_generation": request.device_generation,
            },
        )
        if not self.store.provision_device(state=state, audit=audit):
            raise DeviceAlreadyExistsError(f"device already exists: {request.device_id}")
        return self.store.get_device_state(request.device_id) or state

    def transition(
        self,
        device_id: str,
        lifecycle: DeviceLifecycle,
        *,
        actor: str,
        reason: str,
        request_correlation_id: str | None = None,
    ) -> DeviceState:
        deadline = time.monotonic() + self.conflict_deadline_seconds
        attempt = 0
        while True:
            current = self.store.get_device_state(device_id)
            if current is None:
                raise DeviceNotFoundError(f"device not found: {device_id}")
            if current.lifecycle == lifecycle:
                return current
            if lifecycle not in _ALLOWED_TRANSITIONS[current.lifecycle]:
                raise DeviceLifecycleError(
                    f"invalid lifecycle transition: {current.lifecycle.value}->{lifecycle.value}"
                )
            expected = current.projection_version
            updated = current.model_copy(deep=True)
            previous = updated.lifecycle
            updated.lifecycle = lifecycle
            updated.lifecycle_changed_at = datetime.now(UTC)
            updated.health_state, updated.health_reasons = classify_health(updated)
            audit = AuditRecord(
                actor=actor,
                action="device.lifecycle.changed",
                target=device_id,
                correlation_id=f"device:{device_id}:lifecycle:{expected + 1}",
                request_correlation_id=request_correlation_id,
                details={
                    "from": previous.value,
                    "to": lifecycle.value,
                    "reason": reason,
                },
            )
            if self.store.transition_device_lifecycle(
                state=updated,
                expected_projection_version=expected,
                audit=audit,
            ):
                return self.store.get_device_state(device_id) or updated
            if time.monotonic() >= deadline:
                raise DeviceLifecycleConflictError("device lifecycle contention deadline exceeded")
            attempt += 1
            time.sleep(min(0.025, 0.0005 * (2 ** min(attempt, 5))) * random.uniform(0.5, 1.5))


def ensure_configurable(state: DeviceState | None, device_id: str) -> DeviceState:
    if state is None:
        raise DeviceNotFoundError(f"device not found: {device_id}")
    if state.lifecycle in {DeviceLifecycle.DISABLED, DeviceLifecycle.DECOMMISSIONED}:
        raise DeviceLifecycleError(
            f"device lifecycle does not allow configuration: {state.lifecycle.value}"
        )
    return state


def ensure_command_allowed(state: DeviceState | None, device_id: str, kind: object) -> DeviceState:
    from fleetplane.domain.enums import CommandKind

    if state is None:
        raise DeviceNotFoundError(f"device not found: {device_id}")
    if state.lifecycle != DeviceLifecycle.ACTIVE:
        if state.lifecycle == DeviceLifecycle.QUARANTINED and kind in {
            CommandKind.PING,
            CommandKind.COLLECT_DIAGNOSTICS,
        }:
            return state
        raise DeviceLifecycleError(
            f"device lifecycle does not allow command {getattr(kind, 'value', kind)}: "
            f"{state.lifecycle.value}"
        )
    return state
