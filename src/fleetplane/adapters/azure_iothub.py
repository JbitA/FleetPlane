from __future__ import annotations

import os
from typing import Any, Protocol

from fleetplane.domain.enums import AckCode
from fleetplane.domain.models import CommandAck, DesiredConfiguration, DirectCommand
from fleetplane.ports.gateway import TransportResult


class IoTHubManager(Protocol):
    def get_twin(self, device_id: str) -> Any: ...

    def update_twin(self, device_id: str, device_twin: Any, etag: str | None = None) -> Any: ...

    def invoke_device_method(self, device_id: str, direct_method_request: Any) -> Any: ...


class AzureIoTHubConfigurationError(RuntimeError):
    pass


def _status_code(exc: BaseException) -> int | None:
    value = getattr(exc, "status_code", None)
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _retryable_exception(exc: BaseException) -> bool:
    status = _status_code(exc)
    if status is not None:
        return status in {408, 409, 429} or 500 <= status <= 599
    return isinstance(exc, (TimeoutError, ConnectionError, OSError))


class AzureIoTHubGateway:
    """IoT Hub service-side transport.

    A successful twin update means IoT Hub accepted the desired state; it does not mean the
    device applied it. Direct-method transport success is kept separate from the device's
    business acknowledgement.
    """

    def __init__(
        self,
        manager: IoTHubManager,
        *,
        twin_factory: Any | None = None,
        method_factory: Any | None = None,
    ) -> None:
        self.manager = manager
        self._twin_factory = twin_factory
        self._method_factory = method_factory

    @classmethod
    def from_environment(
        cls,
        *,
        hostname: str | None = None,
        credential: Any | None = None,
    ) -> "AzureIoTHubGateway":
        hostname = hostname or os.getenv("FLEETPLANE_IOTHUB_HOST_NAME")
        if not hostname:
            raise AzureIoTHubConfigurationError("FLEETPLANE_IOTHUB_HOST_NAME is required")
        try:
            from azure.identity import DefaultAzureCredential
            from azure.iot.hub import IoTHubRegistryManager
        except ImportError as exc:  # pragma: no cover - exercised only with optional Azure deps
            raise AzureIoTHubConfigurationError(
                "Azure dependencies are not installed; install fleetplane[azure]"
            ) from exc
        credential = credential or DefaultAzureCredential()
        manager = IoTHubRegistryManager.from_token_credential(
            url=hostname,
            token_credential=credential,
        )
        return cls(manager)

    def push_desired(self, device_id: str, config: DesiredConfiguration) -> TransportResult:
        if self._twin_factory is None:
            try:
                from azure.iot.hub.protocol.models import Twin, TwinProperties
            except ImportError as exc:  # pragma: no cover - optional provider dependency
                return TransportResult(accepted=False, retryable=False, error=type(exc).__name__)

            def twin_factory(value: DesiredConfiguration) -> Any:
                return Twin(
                    properties=TwinProperties(
                        desired={"fleetplane": value.model_dump(mode="json")}
                    )
                )
        else:
            twin_factory = self._twin_factory

        try:
            current = self.manager.get_twin(device_id)
            patch = twin_factory(config)
            self.manager.update_twin(device_id, patch, getattr(current, "etag", None))
            return TransportResult(accepted=True)
        except Exception as exc:  # noqa: BLE001 - provider errors are normalized at the port
            return TransportResult(
                accepted=False,
                retryable=_retryable_exception(exc),
                error=f"iothub_twin:{type(exc).__name__}",
            )

    def invoke_direct(self, command: DirectCommand) -> TransportResult:
        payload = {
            "command_id": command.command_id,
            "device_id": command.device_id,
            "expires_at": command.expires_at.isoformat(),
            "payload": command.payload,
        }
        if self._method_factory is not None:
            request = self._method_factory(command, payload)
        else:
            try:
                from azure.iot.hub.protocol.models import CloudToDeviceMethod
            except ImportError as exc:  # pragma: no cover - optional provider dependency
                return TransportResult(accepted=False, retryable=False, error=type(exc).__name__)
            request = CloudToDeviceMethod(
                method_name=command.kind.value,
                payload=payload,
                connect_timeout_in_seconds=5,
                response_timeout_in_seconds=30,
            )
        try:
            result = self.manager.invoke_device_method(command.device_id, request)
        except Exception as exc:  # noqa: BLE001 - provider errors are normalized at the port
            return TransportResult(
                accepted=False,
                retryable=_retryable_exception(exc),
                error=f"iothub_method:{type(exc).__name__}",
            )

        status = int(getattr(result, "status", 0) or 0)
        payload = getattr(result, "payload", None)
        ack = self._parse_ack(command, payload, status)
        return TransportResult(accepted=True, command_ack=ack)

    @staticmethod
    def _parse_ack(command: DirectCommand, payload: Any, status: int) -> CommandAck | None:
        if isinstance(payload, dict):
            try:
                candidate = dict(payload)
                candidate.setdefault("command_id", command.command_id)
                candidate.setdefault("device_id", command.device_id)
                return CommandAck.model_validate(candidate)
            except Exception:  # noqa: BLE001 - payload may be a non-FleetPlane method response
                pass
        if status and not 200 <= status <= 299:
            return CommandAck(
                command_id=command.command_id,
                device_id=command.device_id,
                code=AckCode.REJECTED,
                reason=f"device_method_status={status}",
                payload=payload if isinstance(payload, dict) else {},
            )
        return None
