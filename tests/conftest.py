from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fleetplane.domain.enums import DeviceLifecycle
from fleetplane.domain.models import DeviceProvisionRequest, TelemetryEnvelope, TelemetryHealth
from fleetplane.runtime import Runtime, Settings, build_runtime


@pytest.fixture
def runtime(tmp_path: Path) -> Runtime:
    value = build_runtime(Settings(sqlite_path=str(tmp_path / "cloud.db")))
    yield value
    value.close()


def telemetry(
    device_id: str,
    sequence: int,
    *,
    boot_id: str = "boot-a",
    generation: int = 1,
    config_revision: int = 0,
    observed_at: datetime | None = None,
    battery: float = 80,
) -> TelemetryEnvelope:
    return TelemetryEnvelope(
        event_id=f"evt-{device_id}-{generation}-{boot_id}-{sequence}",
        device_id=device_id,
        device_generation=generation,
        boot_id=boot_id,
        sequence=sequence,
        observed_at=observed_at or datetime.now(UTC) + timedelta(microseconds=sequence),
        config_revision=config_revision,
        health=TelemetryHealth(
            battery_pct=battery,
            spool_depth=0,
            sensor_ok=True,
            inference_latency_ms=5,
        ),
    )


def provision_active(
    runtime: Runtime,
    device_id: str,
    *,
    site_id: str = "test-site",
    fleet_id: str = "test-fleet",
    generation: int = 1,
) -> None:
    if runtime.store.get_device_state(device_id) is None:
        runtime.lifecycle.provision(
            DeviceProvisionRequest(
                device_id=device_id,
                site_id=site_id,
                fleet_id=fleet_id,
                device_generation=generation,
            ),
            actor="test",
        )
    state = runtime.store.get_device_state(device_id)
    assert state is not None
    if state.lifecycle == DeviceLifecycle.PROVISIONED:
        runtime.lifecycle.transition(
            device_id,
            DeviceLifecycle.ACTIVE,
            actor="test",
            reason="test activation",
        )
