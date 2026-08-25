from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fleetplane.domain.enums import AckCode, CommandKind, CommandStatus, OperatingMode
from fleetplane.domain.models import ConfigurationPatch, DirectCommand
from fleetplane.simulator.device import DevicePolicy, SimulatedDevice
from tests.conftest import provision_active, telemetry


def test_applied_configuration_controls_freshness(runtime):
    now = datetime.now(UTC)
    provision_active(runtime, "edge-1")
    runtime.ingestion.ingest(telemetry("edge-1", 0, observed_at=now), received_at=now)
    desired = runtime.configuration.set_desired(
        "edge-1", ConfigurationPatch(telemetry_interval_s=10), actor="operator"
    )
    # Desired is not applied: the prior 1200s interval remains the health freshness contract.
    runtime.reconciliation.reconcile_batch(limit=10, now=now + timedelta(seconds=100))
    state = runtime.store.get_device_state("edge-1")
    assert state.health_state.value != "offline"

    from fleetplane.domain.models import ConfigAck

    runtime.configuration.record_ack(
        ConfigAck(device_id="edge-1", revision=desired.revision, code=AckCode.APPLIED)
    )
    runtime.reconciliation.reconcile_batch(limit=10, now=now + timedelta(seconds=100))
    assert runtime.store.get_device_state("edge-1").health_state.value == "offline"


def test_device_command_journal_makes_replay_idempotent(tmp_path: Path):
    device = SimulatedDevice(
        "edge-1",
        tmp_path,
        telemetry_sender=lambda event: None,  # not used in this test
        seed=1,
        policy=DevicePolicy(),
    )
    try:
        command = DirectCommand(
            command_id="cmd-1",
            device_id="edge-1",
            kind=CommandKind.PING,
            actor="operator",
            expires_at=datetime.now(UTC) + timedelta(seconds=30),
        )
        first = device.handle_direct(command)
        second = device.handle_direct(command)
        assert first.ack_id == second.ack_id
        assert first.code == AckCode.ACCEPTED
    finally:
        device.close()


def test_local_autonomy_rejects_restart_while_active(tmp_path: Path):
    device = SimulatedDevice(
        "edge-1",
        tmp_path,
        telemetry_sender=lambda event: None,
        seed=1,
    )
    try:
        device.set_operating_mode(OperatingMode.ACTIVE)
        command = DirectCommand(
            device_id="edge-1",
            kind=CommandKind.RESTART_APPLICATION,
            actor="operator",
            expires_at=datetime.now(UTC) + timedelta(seconds=30),
        )
        ack = device.handle_direct(command)
        assert ack.code == AckCode.REJECTED_POLICY
    finally:
        device.close()
