from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fleetplane.domain.enums import DeviceLifecycle, HealthState
from fleetplane.domain.models import DeviceState


def classify_health(
    state: DeviceState,
    now: datetime | None = None,
) -> tuple[HealthState, list[str]]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    if state.lifecycle == DeviceLifecycle.DECOMMISSIONED:
        return HealthState.UNKNOWN, ["device_decommissioned"]
    if state.lifecycle == DeviceLifecycle.DISABLED:
        return HealthState.UNKNOWN, ["device_disabled"]
    if state.last_received_at is None:
        reasons = ["no_telemetry"]
        if state.lifecycle == DeviceLifecycle.QUARANTINED:
            reasons.append("device_quarantined")
        return HealthState.UNKNOWN, reasons

    reasons: list[str] = []
    freshness = timedelta(seconds=max(60, state.applied_telemetry_interval_s * 3))
    if state.last_received_at < now - freshness:
        return HealthState.OFFLINE, ["telemetry_stale"]

    if state.lifecycle == DeviceLifecycle.QUARANTINED:
        reasons.append("device_quarantined")
    if not state.last_sensor_ok:
        reasons.append("sensor_unhealthy")
    if state.last_battery_pct is not None and state.last_battery_pct < 15:
        reasons.append("battery_low")
    if state.last_spool_depth > 100:
        reasons.append("spool_backlog")
    if state.desired_revision > state.reported_revision:
        reasons.append("configuration_not_converged")

    return (HealthState.DEGRADED, reasons) if reasons else (HealthState.HEALTHY, [])
