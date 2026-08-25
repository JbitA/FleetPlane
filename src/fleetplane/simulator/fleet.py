from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fleetplane.adapters.inmemory_gateway import InMemoryDeviceGateway
from fleetplane.core.commands import CommandService
from fleetplane.core.configuration import ConfigurationService
from fleetplane.core.ingestion import TelemetryIngestionService
from fleetplane.core.lifecycle import DeviceLifecycleService
from fleetplane.core.reconciliation import ReconciliationService
from fleetplane.domain.enums import AckCode, CommandKind, DeviceLifecycle, IngestDisposition, OperatingMode
from fleetplane.domain.models import ConfigurationPatch, DeviceProvisionRequest, IngestOutcome, TelemetryEnvelope
from fleetplane.ports.metrics import MetricSink, NullMetricSink
from fleetplane.ports.store import FleetStore
from fleetplane.services.outbox import OutboxDispatcher
from fleetplane.simulator.device import DevicePolicy, SimulatedDevice


class FleetSimulator:
    def __init__(
        self,
        store: FleetStore,
        state_dir: str | Path,
        device_count: int = 100,
        restricted_devices: int = 5,
        metrics: MetricSink | None = None,
        gateway: InMemoryDeviceGateway | None = None,
    ) -> None:
        self.store = store
        self.metrics = metrics or NullMetricSink()
        self.ingestion = TelemetryIngestionService(store, self.metrics)
        self.lifecycle = DeviceLifecycleService(store)
        self.gateway = gateway or InMemoryDeviceGateway()
        self.configuration = ConfigurationService(store)
        self.commands = CommandService(store, self.metrics)
        self.reconciliation = ReconciliationService(store, self.metrics)
        self.dispatcher = OutboxDispatcher(
            store,
            self.gateway,
            self.configuration,
            self.commands,
            worker_id="simulator-dispatcher",
        )
        self.devices: dict[str, SimulatedDevice] = {}
        for index in range(device_count):
            device_id = f"edge-{index + 1:03d}"
            policy = DevicePolicy(min_telemetry_interval_s=60 if index < restricted_devices else 10)
            state = self.store.get_device_state(device_id)
            if state is None:
                state = self.lifecycle.provision(
                    DeviceProvisionRequest(
                        device_id=device_id,
                        site_id="showcase-site",
                        fleet_id="showcase-fleet",
                    ),
                    actor="simulator.bootstrap",
                )
            if state.lifecycle == DeviceLifecycle.PROVISIONED:
                state = self.lifecycle.transition(
                    device_id,
                    DeviceLifecycle.ACTIVE,
                    actor="simulator.bootstrap",
                    reason="reference simulator activation",
                )
            device = SimulatedDevice(
                device_id=device_id,
                state_dir=state_dir,
                telemetry_sender=self.send_telemetry,
                seed=1000 + index,
                policy=policy,
            )
            self.devices[device_id] = device
            self.gateway.register(device_id, device)

    def close(self) -> None:
        for device in self.devices.values():
            device.close()

    def send_telemetry(self, event: TelemetryEnvelope) -> IngestOutcome:
        return self.ingestion.ingest(event, received_at=datetime.now(UTC))

    def tick_all(self, rounds: int = 1) -> None:
        for _ in range(rounds):
            for device in self.devices.values():
                device.tick()

    def set_offline_fraction(self, fraction: float) -> list[str]:
        count = int(len(self.devices) * max(0.0, min(1.0, fraction)))
        offline = list(sorted(self.devices))[-count:] if count else []
        for device_id in offline:
            self.devices[device_id].set_online(False)
        return offline

    def reconnect(self, device_ids: list[str]) -> None:
        for device_id in device_ids:
            device = self.devices[device_id]
            device.set_online(True)
            desired = self.store.get_desired_config(device_id)
            if desired is not None:
                self.configuration.record_ack(device.handle_desired(desired))
            device.flush_spool()

    def run_reference_scenario(self) -> dict[str, object]:
        self.tick_all(rounds=2)

        duplicate_device = self.devices[sorted(self.devices)[-1]]
        duplicate_event = duplicate_device.build_event()
        first_duplicate_result = self.send_telemetry(duplicate_event)
        second_duplicate_result = self.send_telemetry(duplicate_event)

        ordering_device = self.devices[sorted(self.devices)[-2]]
        earlier = ordering_device.build_event()
        later = ordering_device.build_event()
        later_result = self.send_telemetry(later)
        earlier_result = self.send_telemetry(earlier)

        offline_ids = self.set_offline_fraction(0.2)
        self.tick_all(rounds=3)
        spooled_before_reconnect = sum(self.devices[d].store.spool_depth() for d in offline_ids)

        revision1_configs = {}
        for device_id in sorted(self.devices):
            revision1_configs[device_id] = self.configuration.set_desired(
                device_id,
                ConfigurationPatch(telemetry_interval_s=30),
                actor="scenario",
            )
        self.dispatcher.dispatch_once(limit=100)
        self.reconnect(offline_ids)

        revision1_acks = {
            device_id: (ack.code.value if (ack := self.store.get_latest_config_ack(device_id)) else None)
            for device_id in self.devices
        }

        for device_id in sorted(self.devices):
            self.configuration.set_desired(
                device_id,
                ConfigurationPatch(telemetry_interval_s=120, anomaly_threshold=0.84),
                actor="scenario",
            )
        # Drain the new desired-state work. Old retry messages may also be claimed and safely become stale.
        for _ in range(3):
            self.dispatcher.dispatch_once(limit=100)

        stale_rejections = 0
        for device_id, old in revision1_configs.items():
            ack = self.devices[device_id].handle_desired(old)
            if ack.code == AckCode.REJECTED_STALE:
                stale_rejections += 1

        active_device = self.devices[sorted(self.devices)[0]]
        active_device.set_operating_mode(OperatingMode.ACTIVE)
        restart = self.commands.issue(
            active_device.device_id,
            CommandKind.RESTART_APPLICATION,
            actor="scenario",
            idempotency_key="reference-restart",
        )
        ping = self.commands.issue(
            self.devices[sorted(self.devices)[-1]].device_id,
            CommandKind.PING,
            actor="scenario",
            idempotency_key="reference-ping",
        )
        self.dispatcher.dispatch_once(limit=100)
        restart = self.store.get_command(restart.command_id) or restart
        ping = self.store.get_command(ping.command_id) or ping

        self.tick_all(rounds=1)
        summary = self.reconciliation.fleet_summary()
        return {
            "devices": len(self.devices),
            "duplicate_first": first_duplicate_result.disposition.value,
            "duplicate_second": second_duplicate_result.disposition.value,
            "gap_result": later_result.disposition.value,
            "out_of_order_result": earlier_result.disposition.value,
            "spooled_before_reconnect": spooled_before_reconnect,
            "revision1_applied": sum(1 for value in revision1_acks.values() if value == AckCode.APPLIED.value),
            "revision1_rejected": sum(
                1 for value in revision1_acks.values() if value == AckCode.REJECTED_POLICY.value
            ),
            "stale_rejections": stale_rejections,
            "restart_status": restart.status.value,
            "ping_status": ping.status.value,
            "summary": summary.model_dump(mode="json"),
            "assertions": {
                "duplicate_detected": second_duplicate_result.disposition == IngestDisposition.DUPLICATE,
                "gap_detected": later_result.disposition == IngestDisposition.ACCEPTED_WITH_GAP,
                "out_of_order_detected": earlier_result.disposition == IngestDisposition.ACCEPTED_OUT_OF_ORDER,
                "offline_spool_used": spooled_before_reconnect > 0,
                "restricted_devices_rejected_revision1": any(
                    value == AckCode.REJECTED_POLICY.value for value in revision1_acks.values()
                ),
                "revision2_converged": summary.config_converged == len(self.devices),
                "stale_revision_rejected": stale_rejections == len(self.devices),
                "local_autonomy_blocked_restart": restart.status.value == "rejected",
                "direct_ping_succeeded": ping.status.value == "accepted",
            },
        }
