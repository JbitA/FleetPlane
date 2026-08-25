from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from fleetplane.domain.models import CommandAck, ConfigAck, DesiredConfiguration, DirectCommand


@dataclass(frozen=True)
class TransportResult:
    accepted: bool
    retryable: bool = False
    error: str | None = None
    command_ack: CommandAck | None = None
    config_ack: ConfigAck | None = None


class DeviceGateway(Protocol):
    def push_desired(self, device_id: str, config: DesiredConfiguration) -> TransportResult: ...

    def invoke_direct(self, command: DirectCommand) -> TransportResult: ...
