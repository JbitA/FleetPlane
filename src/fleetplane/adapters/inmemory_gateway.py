from __future__ import annotations

from typing import Protocol

from fleetplane.domain.models import DesiredConfiguration, DirectCommand
from fleetplane.ports.gateway import TransportResult


class LocalDevice(Protocol):
    online: bool
    def handle_desired(self, config: DesiredConfiguration): ...
    def handle_direct(self, command: DirectCommand): ...


class InMemoryDeviceGateway:
    def __init__(self) -> None:
        self.devices: dict[str, LocalDevice] = {}

    def register(self, device_id: str, device: LocalDevice) -> None:
        self.devices[device_id] = device

    def push_desired(self, device_id: str, config: DesiredConfiguration) -> TransportResult:
        device = self.devices.get(device_id)
        if device is None or not device.online:
            return TransportResult(accepted=False, retryable=True, error="device_unavailable")
        return TransportResult(accepted=True, config_ack=device.handle_desired(config))

    def invoke_direct(self, command: DirectCommand) -> TransportResult:
        device = self.devices.get(command.device_id)
        if device is None or not device.online:
            return TransportResult(accepted=False, retryable=True, error="device_unavailable")
        return TransportResult(accepted=True, command_ack=device.handle_direct(command))
