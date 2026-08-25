from __future__ import annotations

from typing import Protocol

from prometheus_client import CollectorRegistry, Counter, Histogram

from fleetplane.domain.enums import CommandStatus, IngestDisposition


class MetricSink(Protocol):
    registry: CollectorRegistry

    def observe_ingest(
        self,
        disposition: IngestDisposition,
        processing_seconds: float,
        receive_lag_seconds: float | None,
    ) -> None: ...

    def observe_command(self, status: CommandStatus, latency_seconds: float | None) -> None: ...


class NullMetricSink:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()

    def observe_ingest(
        self,
        disposition: IngestDisposition,
        processing_seconds: float,
        receive_lag_seconds: float | None,
    ) -> None:
        del disposition, processing_seconds, receive_lag_seconds

    def observe_command(self, status: CommandStatus, latency_seconds: float | None) -> None:
        del status, latency_seconds


class PrometheusMetricSink:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.ingest_total = Counter(
            "fleetplane_ingest_total",
            "Telemetry ingestion outcomes",
            ["disposition"],
            registry=self.registry,
        )
        self.ingest_seconds = Histogram(
            "fleetplane_ingest_processing_seconds",
            "Telemetry ingestion processing latency",
            registry=self.registry,
        )
        self.receive_lag = Histogram(
            "fleetplane_ingest_receive_lag_seconds",
            "Device observation to control-plane receipt lag",
            registry=self.registry,
        )
        self.command_total = Counter(
            "fleetplane_command_total",
            "Command terminal/transport outcomes",
            ["status"],
            registry=self.registry,
        )
        self.command_latency = Histogram(
            "fleetplane_command_latency_seconds",
            "Command request to device response latency",
            registry=self.registry,
        )

    def observe_ingest(
        self,
        disposition: IngestDisposition,
        processing_seconds: float,
        receive_lag_seconds: float | None,
    ) -> None:
        self.ingest_total.labels(disposition=disposition.value).inc()
        self.ingest_seconds.observe(processing_seconds)
        if receive_lag_seconds is not None:
            self.receive_lag.observe(receive_lag_seconds)

    def observe_command(self, status: CommandStatus, latency_seconds: float | None) -> None:
        self.command_total.labels(status=status.value).inc()
        if latency_seconds is not None:
            self.command_latency.observe(latency_seconds)
