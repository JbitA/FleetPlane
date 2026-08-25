from __future__ import annotations

import random
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from fleetplane.core.health import classify_health
from fleetplane.domain.enums import DeviceLifecycle, IngestDisposition
from fleetplane.domain.models import DeviceState, IngestOutcome, TelemetryEnvelope
from fleetplane.ports.metrics import MetricSink, NullMetricSink
from fleetplane.ports.store import FleetStore


class TelemetryIngestionService:
    def __init__(
        self,
        store: FleetStore,
        metrics: MetricSink | None = None,
        max_future_skew: timedelta = timedelta(minutes=5),
        max_backlog_age: timedelta = timedelta(days=7),
        conflict_deadline_seconds: float = 2.0,
    ) -> None:
        self.store = store
        self.metrics = metrics or NullMetricSink()
        self.max_future_skew = max_future_skew
        self.max_backlog_age = max_backlog_age
        self.conflict_deadline_seconds = conflict_deadline_seconds

    def ingest(
        self,
        raw: TelemetryEnvelope | dict[str, Any],
        received_at: datetime | None = None,
    ) -> IngestOutcome:
        started = time.perf_counter()
        received_at = (received_at or datetime.now(UTC)).astimezone(UTC)
        try:
            event = raw if isinstance(raw, TelemetryEnvelope) else TelemetryEnvelope.model_validate(raw)
        except ValidationError as exc:
            outcome = IngestOutcome(
                disposition=IngestDisposition.REJECTED,
                reason=f"schema_validation:{exc.errors()[0]['type']}",
            )
            self._measure(outcome, started, None)
            return outcome

        rejected = self._validate_time(event, received_at)
        if rejected is not None:
            self._measure(rejected, started, None)
            return rejected

        existing_identity = self.store.get_device_state(event.device_id)
        if existing_identity is None:
            outcome = IngestOutcome(
                disposition=IngestDisposition.REJECTED,
                device_id=event.device_id,
                event_id=event.event_id,
                reason="device_not_provisioned",
            )
            self._measure(outcome, started, None)
            return outcome
        if existing_identity.lifecycle not in {DeviceLifecycle.ACTIVE, DeviceLifecycle.QUARANTINED}:
            outcome = IngestOutcome(
                disposition=IngestDisposition.REJECTED,
                device_id=event.device_id,
                event_id=event.event_id,
                reason=f"device_lifecycle_{existing_identity.lifecycle.value}",
            )
            self._measure(outcome, started, None)
            return outcome
        if event.device_generation != existing_identity.device_generation:
            outcome = IngestOutcome(
                disposition=IngestDisposition.REJECTED,
                device_id=event.device_id,
                event_id=event.event_id,
                reason="device_generation_mismatch",
            )
            self._measure(outcome, started, None)
            return outcome

        receive_lag = max(0.0, (received_at - event.observed_at).total_seconds())
        deadline = time.monotonic() + self.conflict_deadline_seconds
        attempt = 0
        while True:
            existing = self.store.get_device_state(event.device_id)
            expected_version = existing.projection_version if existing is not None else 0
            proposed, disposition, missing = project_telemetry(existing, event, received_at)
            status = self.store.commit_telemetry(
                event,
                received_at,
                proposed,
                expected_projection_version=expected_version,
            )
            if status == "committed":
                outcome = IngestOutcome(
                    disposition=disposition,
                    device_id=event.device_id,
                    event_id=event.event_id,
                    missing_sequences=missing,
                    receive_lag_ms=receive_lag * 1000,
                )
                self._measure(outcome, started, receive_lag)
                return outcome
            if status == "duplicate":
                self._note_duplicate(event.device_id, received_at)
                outcome = IngestOutcome(
                    disposition=IngestDisposition.DUPLICATE,
                    device_id=event.device_id,
                    event_id=event.event_id,
                    reason="device_generation_boot_sequence_already_seen",
                    receive_lag_ms=receive_lag * 1000,
                )
                self._measure(outcome, started, receive_lag)
                return outcome
            if time.monotonic() >= deadline:
                outcome = IngestOutcome(
                    disposition=IngestDisposition.REJECTED,
                    device_id=event.device_id,
                    event_id=event.event_id,
                    reason="projection_conflict_deadline_exceeded",
                    receive_lag_ms=receive_lag * 1000,
                )
                self._measure(outcome, started, receive_lag)
                return outcome
            attempt += 1
            time.sleep(min(0.025, 0.0005 * (2 ** min(attempt, 5))) * random.uniform(0.5, 1.5))

    def _validate_time(
        self,
        event: TelemetryEnvelope,
        received_at: datetime,
    ) -> IngestOutcome | None:
        if event.observed_at > received_at + self.max_future_skew:
            return IngestOutcome(
                disposition=IngestDisposition.REJECTED,
                device_id=event.device_id,
                event_id=event.event_id,
                reason="observed_at_too_far_in_future",
            )
        if event.observed_at < received_at - self.max_backlog_age:
            return IngestOutcome(
                disposition=IngestDisposition.REJECTED,
                device_id=event.device_id,
                event_id=event.event_id,
                reason="event_older_than_backlog_window",
            )
        return None

    def _note_duplicate(self, device_id: str, now: datetime) -> None:
        for _ in range(8):
            state = self.store.get_device_state(device_id)
            if state is None:
                return
            expected = state.projection_version
            state.duplicate_count += 1
            state.health_state, state.health_reasons = classify_health(state, now)
            if self.store.compare_and_swap_device_state(state, expected):
                return

    def _measure(
        self,
        outcome: IngestOutcome,
        started: float,
        receive_lag_seconds: float | None,
    ) -> None:
        self.metrics.observe_ingest(
            outcome.disposition,
            time.perf_counter() - started,
            receive_lag_seconds,
        )


def project_telemetry(
    existing: DeviceState | None,
    event: TelemetryEnvelope,
    received_at: datetime,
) -> tuple[DeviceState, IngestDisposition, int]:
    state = existing.model_copy(deep=True) if existing is not None else DeviceState(device_id=event.device_id)
    disposition = IngestDisposition.ACCEPTED
    missing = 0

    older_generation = event.device_generation < state.device_generation
    newer_generation = event.device_generation > state.device_generation
    historic_boot = (
        not newer_generation
        and not older_generation
        and state.last_boot_id is not None
        and state.last_boot_id != event.boot_id
        and state.last_observed_at is not None
        and event.observed_at < state.last_observed_at
    )

    if older_generation or historic_boot:
        disposition = IngestDisposition.ACCEPTED_OUT_OF_ORDER
        state.out_of_order_count += 1
    elif (
        not newer_generation
        and state.last_boot_id == event.boot_id
        and state.last_sequence is not None
    ):
        if event.sequence < state.last_sequence:
            disposition = IngestDisposition.ACCEPTED_OUT_OF_ORDER
            state.out_of_order_count += 1
        elif event.sequence > state.last_sequence + 1:
            disposition = IngestDisposition.ACCEPTED_WITH_GAP
            missing = event.sequence - state.last_sequence - 1
            state.gap_count += missing

    should_advance = not older_generation and not historic_boot and (
        newer_generation
        or state.last_boot_id != event.boot_id
        or state.last_sequence is None
        or event.sequence > state.last_sequence
    )
    if should_advance:
        state.device_generation = event.device_generation
        state.last_boot_id = event.boot_id
        state.last_sequence = event.sequence
        state.last_observed_at = event.observed_at
        state.model_version = event.model_version
        state.reported_revision = max(state.reported_revision, event.config_revision)
        state.last_battery_pct = event.health.battery_pct
        state.last_spool_depth = event.health.spool_depth
        state.last_sensor_ok = event.health.sensor_ok
        state.last_inference_latency_ms = event.health.inference_latency_ms
        state.operating_mode = event.health.operating_mode
        state.last_received_at = max_dt(state.last_received_at, received_at)

    state.health_state, state.health_reasons = classify_health(state, received_at)
    return state, disposition, missing


def max_dt(left: datetime | None, right: datetime) -> datetime:
    return right if left is None or right > left else left
