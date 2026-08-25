from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import azure.functions as func

from fleetplane.api.app import create_app
from fleetplane.azure_ingress import PermanentIngressError, decode_iothub_event
from fleetplane.runtime import build_runtime
from fleetplane.wire import process_wire_event

LOGGER = logging.getLogger("fleetplane.azure.functions")

runtime = build_runtime()
fastapi_app = create_app(runtime)
app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


@app.route(route="{*route}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def http_api(req: func.HttpRequest, context: func.Context) -> func.HttpResponse:
    return await func.AsgiMiddleware(fastapi_app).handle_async(req, context)


@app.function_name(name="telemetry_ingest")
@app.event_hub_message_trigger(
    arg_name="event",
    event_hub_name="%FLEETPLANE_IOTHUB_EVENTHUB_NAME%",
    connection="FLEETPLANE_IOTHUB_EVENTHUB",
    consumer_group="%FLEETPLANE_IOTHUB_CONSUMER_GROUP%",
)
def telemetry_ingest(event: func.EventHubEvent) -> None:
    try:
        decoded = decode_iothub_event(event)
    except PermanentIngressError as exc:
        LOGGER.warning("permanent_ingress_rejection reason=%s", exc)
        return

    result = process_wire_event(
        runtime,
        decoded.payload,
        authenticated_device_id=decoded.authenticated_device_id,
    )
    if not result.accepted:
        LOGGER.warning(
            "wire_event_rejected device_id=%s kind=%s reason=%s",
            decoded.authenticated_device_id,
            result.kind,
            result.reason,
        )


def _document_dict(document: object) -> dict[str, object]:
    if isinstance(document, dict):
        return dict(document)
    to_json = getattr(document, "to_json", None)
    if callable(to_json):
        import json

        value = json.loads(to_json())
        if isinstance(value, dict):
            return value
    try:
        return dict(document)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise TypeError("unsupported Cosmos change-feed document") from exc


@app.function_name(name="fleet_summary_projection")
@app.cosmos_db_trigger(
    arg_name="documents",
    database_name="%FLEETPLANE_COSMOS_DATABASE%",
    container_name="%FLEETPLANE_COSMOS_CONTAINER%",
    connection="FLEETPLANE_COSMOS_CONNECTION",
    lease_container_name="%FLEETPLANE_COSMOS_LEASE_CONTAINER%",
    create_lease_container_if_not_exists=False,
)
def fleet_summary_projection(documents: func.DocumentList) -> None:
    if not documents:
        return
    if runtime.summary_projector is None:
        raise RuntimeError("Cosmos summary projector is not configured")
    stats = runtime.summary_projector.process_documents([_document_dict(doc) for doc in documents])
    LOGGER.info(
        "summary_projection seen=%s deltas=%s scope_updates=%s ignored=%s",
        stats["seen"],
        stats["deltas"],
        stats["scope_updates"],
        stats["ignored"],
    )


@app.function_name(name="reconcile_timer")
@app.timer_trigger(
    schedule="0 */1 * * * *",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def reconcile_timer(timer: func.TimerRequest) -> None:
    del timer
    runtime.commands.expire_pending(limit=200)
    runtime.reconciliation.reconcile_batch(limit=200)
    runtime.dispatcher.dispatch_once(limit=100)
