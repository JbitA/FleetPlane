from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from fleetplane.domain.models import CommandAck, ConfigAck, TelemetryEnvelope
from fleetplane.runtime import Runtime


@dataclass(frozen=True)
class WireResult:
    accepted: bool
    kind: str | None = None
    reason: str | None = None


def process_wire_event(
    runtime: Runtime,
    raw: dict[str, Any],
    *,
    authenticated_device_id: str | None = None,
) -> WireResult:
    kind = raw.get("kind", "telemetry")
    try:
        if kind == "telemetry":
            payload = raw.get("payload", raw)
            event = TelemetryEnvelope.model_validate(payload)
            if authenticated_device_id is not None and event.device_id != authenticated_device_id:
                return WireResult(False, kind, "authenticated_device_id_mismatch")
            outcome = runtime.ingestion.ingest(event)
            return WireResult(outcome.disposition.value != "rejected", kind, outcome.reason)
        if kind == "config_ack":
            ack = ConfigAck.model_validate(raw["payload"])
            if authenticated_device_id is not None and ack.device_id != authenticated_device_id:
                return WireResult(False, kind, "authenticated_device_id_mismatch")
            runtime.configuration.record_ack(ack)
            return WireResult(True, kind)
        if kind == "command_ack":
            ack = CommandAck.model_validate(raw["payload"])
            if authenticated_device_id is not None and ack.device_id != authenticated_device_id:
                return WireResult(False, kind, "authenticated_device_id_mismatch")
            runtime.commands.record_ack(ack)
            return WireResult(True, kind)
        return WireResult(False, str(kind), "unsupported_wire_kind")
    except (ValidationError, KeyError, TypeError) as exc:
        return WireResult(False, str(kind), f"invalid_wire_payload:{type(exc).__name__}")
