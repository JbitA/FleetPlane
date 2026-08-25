from __future__ import annotations

import hmac

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from fleetplane import __version__

from fleetplane.api.middleware import CorrelationIdMiddleware, RequestBodyLimitMiddleware
from fleetplane.api.schemas import CommandRequest
from fleetplane.core.configuration import ConfigurationConflictError
from fleetplane.core.lifecycle import (
    DeviceAlreadyExistsError,
    DeviceLifecycleError,
    DeviceNotFoundError,
)
from fleetplane.domain.enums import CommandStatus, DeviceLifecycle, HealthState
from fleetplane.domain.models import (
    ConfigurationPatch,
    DeviceLifecycleChange,
    DeviceProvisionRequest,
    TelemetryEnvelope,
)
from fleetplane.observability import configure_operation_logging, current_correlation_id
from fleetplane.runtime import Runtime, build_runtime


def create_app(runtime: Runtime | None = None) -> FastAPI:
    runtime = runtime or build_runtime()
    configure_operation_logging(runtime.settings.log_level)
    app = FastAPI(title="FleetPlane", version=__version__)
    app.state.runtime = runtime
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=runtime.settings.max_request_bytes)
    app.add_middleware(CorrelationIdMiddleware)
    if runtime.settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(runtime.settings.cors_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH"],
            allow_headers=[
                "Content-Type",
                "X-FleetPlane-Admin-Key",
                "X-Actor",
                "Idempotency-Key",
                "X-Correlation-ID",
            ],
        )

    def require_admin(
        x_fleetplane_admin_key: str | None = Header(default=None),
        x_actor: str | None = Header(default=None),
    ) -> str:
        expected = runtime.settings.admin_key
        if expected is not None:
            if x_fleetplane_admin_key is None or not hmac.compare_digest(
                x_fleetplane_admin_key, expected
            ):
                raise HTTPException(status_code=401, detail="invalid admin credential")
        return (x_actor or "local-admin")[:128]

    @app.get("/health/live")
    def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def readiness() -> dict[str, str]:
        runtime.store.get_fleet_summary()
        return {"status": "ready", "mode": runtime.settings.mode}

    @app.get("/v1/fleet/summary")
    def fleet_summary():
        return runtime.reconciliation.fleet_summary()

    @app.get("/v1/platform/microsoft")
    def microsoft_platform():
        if runtime.microsoft_iot is None:
            raise HTTPException(status_code=503, detail="Microsoft IoT platform mapping unavailable")
        return {
            "topology": runtime.microsoft_iot.topology(),
            "device_registry_namespace": runtime.microsoft_iot.namespace,
            "dps_id_scope": runtime.microsoft_iot.dps_id_scope,
            "iothub_hostname": runtime.microsoft_iot.iothub_hostname,
            "connectivity_patterns": ["iot_hub_direct", "iot_operations_edge"],
        }

    @app.get("/v1/platform/microsoft/devices/{device_id}")
    def microsoft_device_projection(device_id: str):
        state = runtime.store.get_device_state(device_id)
        if state is None:
            raise HTTPException(status_code=404, detail="device not found")
        if runtime.microsoft_iot is None:
            raise HTTPException(status_code=503, detail="Microsoft IoT platform mapping unavailable")
        return {
            "device_registry": runtime.microsoft_iot.registry_projection(state),
            "dps": runtime.microsoft_iot.dps_intent(state),
            "iot_operations": runtime.microsoft_iot.iot_operations_intent(state),
        }

    @app.post("/v1/devices", status_code=201)
    def provision_device(
        request: DeviceProvisionRequest,
        actor: str = Depends(require_admin),
    ):
        try:
            return runtime.lifecycle.provision(request, actor, current_correlation_id())
        except DeviceAlreadyExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/devices")
    def devices(
        limit: int = Query(default=100, ge=1, le=500),
        cursor: str | None = None,
        health: HealthState | None = None,
        lifecycle: DeviceLifecycle | None = None,
        site_id: str | None = None,
        fleet_id: str | None = None,
    ):
        try:
            return runtime.store.list_device_states_page(
                limit=limit,
                cursor=cursor,
                health_state=health,
                lifecycle=lifecycle,
                site_id=site_id,
                fleet_id=fleet_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/v1/devices/{device_id}")
    def device(device_id: str):
        state = runtime.store.get_device_state(device_id)
        if state is None:
            raise HTTPException(status_code=404, detail="device not found")
        return {
            "state": state,
            "desired": runtime.store.get_desired_config(device_id),
            "latest_config_ack": runtime.store.get_latest_config_ack(device_id),
            "commands": runtime.store.list_commands_page(limit=50, device_id=device_id),
        }

    @app.patch("/v1/devices/{device_id}/lifecycle")
    def transition_lifecycle(
        device_id: str,
        change: DeviceLifecycleChange,
        actor: str = Depends(require_admin),
    ):
        try:
            return runtime.lifecycle.transition(
                device_id,
                change.lifecycle,
                actor=actor,
                reason=change.reason,
                request_correlation_id=current_correlation_id(),
            )
        except DeviceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except DeviceLifecycleError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    def _set_desired(device_id: str, patch: ConfigurationPatch, actor: str):
        try:
            return runtime.configuration.set_desired(
                device_id, patch, actor, request_correlation_id=current_correlation_id()
            )
        except DeviceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ConfigurationConflictError, DeviceLifecycleError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.patch("/v1/devices/{device_id}/desired-config")
    def patch_desired(
        device_id: str,
        patch: ConfigurationPatch,
        actor: str = Depends(require_admin),
    ):
        return _set_desired(device_id, patch, actor)

    @app.put("/v1/devices/{device_id}/desired-config", deprecated=True)
    def put_desired_compatibility(
        device_id: str,
        patch: ConfigurationPatch,
        actor: str = Depends(require_admin),
    ):
        return _set_desired(device_id, patch, actor)

    @app.post("/v1/devices/{device_id}/commands", status_code=202)
    def command(
        device_id: str,
        request: CommandRequest,
        actor: str = Depends(require_admin),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        try:
            return runtime.commands.issue(
                device_id,
                request.kind,
                actor=actor,
                payload=request.payload,
                ttl_seconds=request.ttl_seconds,
                idempotency_key=idempotency_key,
                request_correlation_id=current_correlation_id(),
            )
        except DeviceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except DeviceLifecycleError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/commands")
    def commands(
        limit: int = Query(default=100, ge=1, le=500),
        cursor: str | None = None,
        device_id: str | None = None,
        status: CommandStatus | None = None,
    ):
        try:
            return runtime.store.list_commands_page(
                limit=limit,
                cursor=cursor,
                device_id=device_id,
                status=status,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/v1/audit")
    def audit(
        limit: int = Query(default=100, ge=1, le=500),
        cursor: str | None = None,
        correlation_id: str | None = None,
        request_correlation_id: str | None = None,
        target: str | None = None,
        actor: str = Depends(require_admin),
    ):
        del actor
        try:
            return runtime.store.list_audit_page(
                limit=limit,
                cursor=cursor,
                correlation_id=correlation_id,
                request_correlation_id=request_correlation_id,
                target=target,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/v1/ingest")
    def ingest(event: TelemetryEnvelope):
        if runtime.settings.mode != "local":
            raise HTTPException(status_code=404, detail="not available in cloud mode")
        return runtime.ingestion.ingest(event)

    @app.post("/v1/reconcile")
    def reconcile(
        cursor: str | None = None,
        limit: int = Query(default=200, ge=1, le=500),
        actor: str = Depends(require_admin),
    ):
        del actor
        runtime.commands.expire_pending(limit=limit)
        return runtime.reconciliation.reconcile_batch(cursor=cursor, limit=limit)

    @app.post("/v1/outbox/dispatch")
    def dispatch(
        limit: int = Query(default=50, ge=1, le=100),
        actor: str = Depends(require_admin),
    ):
        del actor
        return runtime.dispatcher.dispatch_once(limit=limit)

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(runtime.metrics.registry), media_type=CONTENT_TYPE_LATEST)

    return app
