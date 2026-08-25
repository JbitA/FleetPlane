from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from fleetplane.adapters.inmemory_gateway import InMemoryDeviceGateway
from fleetplane.adapters.sqlite_store import SQLiteFleetStore
from fleetplane.core.commands import CommandService
from fleetplane.core.configuration import ConfigurationService
from fleetplane.core.ingestion import TelemetryIngestionService
from fleetplane.core.lifecycle import DeviceLifecycleService
from fleetplane.core.reconciliation import ReconciliationService
from fleetplane.core.summary_projection import SummaryProjector
from fleetplane.ports.gateway import DeviceGateway
from fleetplane.ports.metrics import MetricSink, PrometheusMetricSink
from fleetplane.ports.store import FleetStore
from fleetplane.microsoft_iot import MicrosoftIoTPlatform
from fleetplane.services.outbox import OutboxDispatcher


@dataclass(frozen=True)
class Settings:
    mode: str = "local"
    sqlite_path: str = ".fleetplane/cloud.db"
    admin_key: str | None = None
    max_request_bytes: int = 262144
    cors_origins: tuple[str, ...] = ()
    cosmos_endpoint: str | None = None
    cosmos_database: str = "fleetplane"
    cosmos_container: str = "control"
    cosmos_summary_container: str | None = None
    iothub_host_name: str | None = None
    log_level: str = "INFO"
    device_registry_namespace: str = "fleetplane-prod"
    dps_id_scope: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        cors = tuple(
            item.strip()
            for item in os.getenv("FLEETPLANE_CORS_ORIGINS", "").split(",")
            if item.strip()
        )
        return cls(
            mode=os.getenv("FLEETPLANE_MODE", "local").strip().lower(),
            sqlite_path=os.getenv("FLEETPLANE_SQLITE_PATH", ".fleetplane/cloud.db"),
            admin_key=os.getenv("FLEETPLANE_ADMIN_KEY") or None,
            max_request_bytes=int(os.getenv("FLEETPLANE_MAX_REQUEST_BYTES", "262144")),
            cors_origins=cors,
            cosmos_endpoint=os.getenv("FLEETPLANE_COSMOS_ENDPOINT") or None,
            cosmos_database=os.getenv("FLEETPLANE_COSMOS_DATABASE", "fleetplane"),
            cosmos_container=os.getenv("FLEETPLANE_COSMOS_CONTAINER", "control"),
            cosmos_summary_container=os.getenv("FLEETPLANE_COSMOS_SUMMARY_CONTAINER") or None,
            iothub_host_name=os.getenv("FLEETPLANE_IOTHUB_HOST_NAME") or None,
            log_level=os.getenv("FLEETPLANE_LOG_LEVEL", "INFO"),
            device_registry_namespace=os.getenv("FLEETPLANE_DEVICE_REGISTRY_NAMESPACE", "fleetplane-prod"),
            dps_id_scope=os.getenv("FLEETPLANE_DPS_ID_SCOPE") or None,
        )


@dataclass
class Runtime:
    settings: Settings
    store: FleetStore
    gateway: DeviceGateway
    metrics: MetricSink
    ingestion: TelemetryIngestionService
    lifecycle: DeviceLifecycleService
    configuration: ConfigurationService
    commands: CommandService
    reconciliation: ReconciliationService
    dispatcher: OutboxDispatcher
    summary_projector: SummaryProjector | None = None
    microsoft_iot: MicrosoftIoTPlatform | None = None

    def close(self) -> None:
        self.store.close()


def build_runtime(
    settings: Settings | None = None,
    *,
    store: FleetStore | None = None,
    gateway: DeviceGateway | None = None,
    metrics: MetricSink | None = None,
) -> Runtime:
    settings = settings or Settings.from_env()
    mode = settings.mode.lower()
    if mode not in {"local", "azure"}:
        raise ValueError(f"unsupported FleetPlane mode: {settings.mode}")

    if store is None:
        if mode == "local":
            Path(settings.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
            store = SQLiteFleetStore(settings.sqlite_path)
        else:
            from fleetplane.adapters.cosmos_store import CosmosFleetStore

            store = CosmosFleetStore.from_environment(
                endpoint=settings.cosmos_endpoint,
                database=settings.cosmos_database,
                container=settings.cosmos_container,
                summary_container=settings.cosmos_summary_container,
            )

    if gateway is None:
        if mode == "local":
            gateway = InMemoryDeviceGateway()
        else:
            from fleetplane.adapters.azure_iothub import AzureIoTHubGateway

            gateway = AzureIoTHubGateway.from_environment(hostname=settings.iothub_host_name)

    metric_sink = metrics or PrometheusMetricSink()
    configuration = ConfigurationService(store)
    commands = CommandService(store, metric_sink)
    summary_store = getattr(store, "summary_store", None)
    return Runtime(
        settings=settings,
        store=store,
        gateway=gateway,
        metrics=metric_sink,
        ingestion=TelemetryIngestionService(store, metric_sink),
        lifecycle=DeviceLifecycleService(store),
        configuration=configuration,
        commands=commands,
        reconciliation=ReconciliationService(store, metric_sink),
        dispatcher=OutboxDispatcher(store, gateway, configuration, commands),
        summary_projector=None if summary_store is None else SummaryProjector(summary_store),
        microsoft_iot=MicrosoftIoTPlatform(
            namespace=settings.device_registry_namespace,
            dps_id_scope=settings.dps_id_scope,
            iothub_hostname=settings.iothub_host_name,
        ),
    )
