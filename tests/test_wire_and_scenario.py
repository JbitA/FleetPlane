from __future__ import annotations

from fleetplane.simulator.fleet import FleetSimulator
from fleetplane.wire import process_wire_event
from tests.conftest import telemetry


def test_wire_binds_payload_to_authenticated_device(runtime):
    event = telemetry("edge-1", 0)
    result = process_wire_event(
        runtime,
        {"kind": "telemetry", "payload": event.model_dump(mode="json")},
        authenticated_device_id="edge-2",
    )
    assert not result.accepted
    assert result.reason == "authenticated_device_id_mismatch"


def test_wire_rejects_unknown_and_bad_payload(runtime):
    assert not process_wire_event(runtime, {"kind": "mystery"}).accepted
    assert not process_wire_event(runtime, {"kind": "telemetry", "payload": {}}).accepted


def test_reference_scenario(runtime, tmp_path):
    simulator = FleetSimulator(
        runtime.store,
        tmp_path / "devices",
        device_count=20,
        restricted_devices=3,
        metrics=runtime.metrics,
        gateway=runtime.gateway,
    )
    try:
        result = simulator.run_reference_scenario()
        assert all(result["assertions"].values()), result
    finally:
        simulator.close()
