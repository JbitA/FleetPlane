from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping


class PermanentIngressError(ValueError):
    """An event that should be recorded/quarantined instead of retried indefinitely."""


@dataclass(frozen=True)
class DecodedIoTHubEvent:
    payload: dict[str, Any]
    authenticated_device_id: str


def _scalar(value: Any) -> Any:
    for attr in ("value", "data"):
        candidate = getattr(value, attr, None)
        if candidate is not None and not callable(candidate):
            return candidate
    return value


def _lookup(metadata: Mapping[str, Any], *names: str) -> str | None:
    lowered = {str(key).lower(): _scalar(value) for key, value in metadata.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value is not None and str(value):
            return str(value)
    return None


def extract_authenticated_device_id(event: Any) -> str:
    iothub_metadata = getattr(event, "iothub_metadata", None) or {}
    metadata = getattr(event, "metadata", None) or {}
    device_id = _lookup(
        iothub_metadata,
        "iothub-connection-device-id",
        "connection-device-id",
        "connectionDeviceId",
    ) or _lookup(
        metadata,
        "iothub-connection-device-id",
        "connectionDeviceId",
        "IoTHubConnectionDeviceId",
    )
    if not device_id:
        raise PermanentIngressError("missing_authenticated_device_id")
    return device_id


def decode_iothub_event(event: Any) -> DecodedIoTHubEvent:
    try:
        body = event.get_body()
    except Exception as exc:  # noqa: BLE001
        raise PermanentIngressError("event_body_unavailable") from exc
    if not isinstance(body, (bytes, bytearray)):
        raise PermanentIngressError("event_body_not_bytes")
    try:
        text = bytes(body).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PermanentIngressError("event_body_not_utf8") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PermanentIngressError("event_body_not_json") from exc
    if not isinstance(payload, dict):
        raise PermanentIngressError("event_body_not_object")
    return DecodedIoTHubEvent(
        payload=payload,
        authenticated_device_id=extract_authenticated_device_id(event),
    )
