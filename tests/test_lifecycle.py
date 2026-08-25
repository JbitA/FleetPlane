from __future__ import annotations

import pytest

from fleetplane.core.lifecycle import DeviceLifecycleError
from fleetplane.domain.enums import CommandKind, DeviceLifecycle
from fleetplane.domain.models import ConfigurationPatch, DeviceProvisionRequest
from tests.conftest import provision_active, telemetry


def test_unknown_device_telemetry_is_rejected(runtime):
    outcome = runtime.ingestion.ingest(telemetry("unknown-edge", 0))
    assert outcome.disposition.value == "rejected"
    assert outcome.reason == "device_not_provisioned"
    assert runtime.store.get_device_state("unknown-edge") is None


def test_generation_is_bound_to_provisioned_identity(runtime):
    provision_active(runtime, "edge-gen", generation=2)
    accepted = runtime.ingestion.ingest(telemetry("edge-gen", 0, generation=2))
    rejected = runtime.ingestion.ingest(telemetry("edge-gen", 1, generation=3))
    assert accepted.disposition.value == "accepted"
    assert rejected.disposition.value == "rejected"
    assert rejected.reason == "device_generation_mismatch"
    assert runtime.store.get_device_state("edge-gen").device_generation == 2


def test_lifecycle_controls_telemetry_configuration_and_commands(runtime):
    runtime.lifecycle.provision(
        DeviceProvisionRequest(device_id="edge-policy", site_id="oulu", fleet_id="inspection"),
        actor="operator",
    )
    provisioned = runtime.ingestion.ingest(telemetry("edge-policy", 0))
    assert provisioned.reason == "device_lifecycle_provisioned"

    runtime.lifecycle.transition(
        "edge-policy",
        DeviceLifecycle.ACTIVE,
        actor="operator",
        reason="commissioned",
    )
    assert runtime.ingestion.ingest(telemetry("edge-policy", 0)).disposition.value == "accepted"

    runtime.lifecycle.transition(
        "edge-policy",
        DeviceLifecycle.QUARANTINED,
        actor="operator",
        reason="investigate sensor drift",
    )
    assert runtime.ingestion.ingest(telemetry("edge-policy", 1)).disposition.value == "accepted"
    assert runtime.commands.issue(
        "edge-policy", CommandKind.COLLECT_DIAGNOSTICS, actor="operator"
    ).kind == CommandKind.COLLECT_DIAGNOSTICS
    with pytest.raises(DeviceLifecycleError):
        runtime.commands.issue(
            "edge-policy", CommandKind.RESTART_APPLICATION, actor="operator"
        )
    # Quarantine still permits remediation configuration.
    config = runtime.configuration.set_desired(
        "edge-policy", ConfigurationPatch(diagnostic_level="verbose"), actor="operator"
    )
    assert config.revision == 1

    runtime.lifecycle.transition(
        "edge-policy",
        DeviceLifecycle.DISABLED,
        actor="operator",
        reason="maintenance hold",
    )
    assert runtime.ingestion.ingest(telemetry("edge-policy", 2)).reason == "device_lifecycle_disabled"
    with pytest.raises(DeviceLifecycleError):
        runtime.configuration.set_desired(
            "edge-policy", ConfigurationPatch(anomaly_threshold=0.7), actor="operator"
        )
    with pytest.raises(DeviceLifecycleError):
        runtime.commands.issue("edge-policy", CommandKind.PING, actor="operator")


def test_decommissioned_is_terminal_and_scope_filters_work(runtime):
    provision_active(runtime, "north-1", site_id="north", fleet_id="rail")
    provision_active(runtime, "north-2", site_id="north", fleet_id="warehouse")
    provision_active(runtime, "south-1", site_id="south", fleet_id="rail")

    rail = runtime.store.list_device_states_page(limit=10, fleet_id="rail")
    assert {item.device_id for item in rail.items} == {"north-1", "south-1"}
    north = runtime.store.list_device_states_page(limit=10, site_id="north")
    assert {item.device_id for item in north.items} == {"north-1", "north-2"}

    state = runtime.lifecycle.transition(
        "north-1",
        DeviceLifecycle.DECOMMISSIONED,
        actor="operator",
        reason="asset retired",
    )
    assert state.lifecycle == DeviceLifecycle.DECOMMISSIONED
    page = runtime.store.list_device_states_page(
        limit=10, lifecycle=DeviceLifecycle.DECOMMISSIONED
    )
    assert [item.device_id for item in page.items] == ["north-1"]
    with pytest.raises(DeviceLifecycleError):
        runtime.lifecycle.transition(
            "north-1",
            DeviceLifecycle.ACTIVE,
            actor="operator",
            reason="attempted resurrection",
        )


def test_sqlite_upgrade_maps_existing_telemetry_devices_to_active(tmp_path):
    import json
    import sqlite3
    from datetime import UTC, datetime

    from fleetplane.adapters.sqlite_store import SQLiteFleetStore
    from fleetplane.domain.models import DeviceState

    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE device_state (device_id TEXT PRIMARY KEY, body TEXT NOT NULL, "
        "projection_version INTEGER NOT NULL, health_state TEXT NOT NULL, "
        "config_converged INTEGER NOT NULL)"
    )
    state = DeviceState(
        device_id="legacy-edge",
        last_received_at=datetime.now(UTC),
        last_observed_at=datetime.now(UTC),
        last_boot_id="boot-a",
        last_sequence=7,
    ).model_dump(mode="json")
    state.pop("site_id")
    state.pop("fleet_id")
    state.pop("lifecycle")
    state.pop("provisioned_at")
    state.pop("lifecycle_changed_at")
    conn.execute(
        "INSERT INTO device_state(device_id,body,projection_version,health_state,config_converged) "
        "VALUES(?,?,?,?,?)",
        ("legacy-edge", json.dumps(state), 1, "healthy", 0),
    )
    conn.commit()
    conn.close()

    store = SQLiteFleetStore(path)
    try:
        upgraded = store.get_device_state("legacy-edge")
        assert upgraded is not None
        assert upgraded.lifecycle == DeviceLifecycle.ACTIVE
        assert upgraded.site_id == "default-site"
        assert upgraded.fleet_id == "default-fleet"
    finally:
        store.close()
