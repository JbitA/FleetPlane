from __future__ import annotations

from fastapi.testclient import TestClient

from fleetplane.api.app import create_app
from fleetplane.domain.enums import CommandKind
from tests.conftest import provision_active, telemetry


def test_device_api_is_paginated(runtime):
    for index in range(5):
        provision_active(runtime, f"edge-{index}")
        runtime.ingestion.ingest(telemetry(f"edge-{index}", 0))
    client = TestClient(create_app(runtime))
    first = client.get("/v1/devices?limit=2")
    assert first.status_code == 200
    body = first.json()
    assert len(body["items"]) == 2
    assert body["next_cursor"]
    second = client.get("/v1/devices", params={"limit": 2, "cursor": body["next_cursor"]})
    assert second.status_code == 200
    assert {d["device_id"] for d in body["items"]}.isdisjoint(
        {d["device_id"] for d in second.json()["items"]}
    )


def test_invalid_cursor_is_400(runtime):
    client = TestClient(create_app(runtime))
    response = client.get("/v1/devices?cursor=not-a-cursor")
    assert response.status_code == 400


def test_command_endpoint_is_202_and_idempotent(runtime):
    provision_active(runtime, "edge-1")
    client = TestClient(create_app(runtime))
    body = {"kind": CommandKind.PING.value, "payload": {}, "ttl_seconds": 30}
    first = client.post(
        "/v1/devices/edge-1/commands",
        json=body,
        headers={"Idempotency-Key": "api-request-1"},
    )
    second = client.post(
        "/v1/devices/edge-1/commands",
        json=body,
        headers={"Idempotency-Key": "api-request-1"},
    )
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["command_id"] == second.json()["command_id"]


def test_cloud_mode_hides_http_ingest(tmp_path):
    from fleetplane.adapters.inmemory_gateway import InMemoryDeviceGateway
    from fleetplane.adapters.sqlite_store import SQLiteFleetStore
    from fleetplane.runtime import Settings, build_runtime

    runtime = build_runtime(
        Settings(mode="azure", sqlite_path=str(tmp_path / "cloud.db")),
        store=SQLiteFleetStore(tmp_path / "cloud.db"),
        gateway=InMemoryDeviceGateway(),
    )
    try:
        client = TestClient(create_app(runtime))
        response = client.post("/v1/ingest", json=telemetry("edge-1", 0).model_dump(mode="json"))
        assert response.status_code == 404
    finally:
        runtime.close()


def test_device_lifecycle_api_and_scope_filters(runtime):
    from fleetplane.domain.enums import DeviceLifecycle

    client = TestClient(create_app(runtime))
    created = client.post(
        "/v1/devices",
        json={"device_id": "edge-api", "site_id": "site-a", "fleet_id": "fleet-a"},
    )
    assert created.status_code == 201
    assert created.json()["lifecycle"] == DeviceLifecycle.PROVISIONED.value

    activated = client.patch(
        "/v1/devices/edge-api/lifecycle",
        json={"lifecycle": "active", "reason": "commissioned"},
    )
    assert activated.status_code == 200
    assert activated.json()["lifecycle"] == DeviceLifecycle.ACTIVE.value

    filtered = client.get("/v1/devices", params={"site_id": "site-a", "fleet_id": "fleet-a"})
    assert filtered.status_code == 200
    assert [item["device_id"] for item in filtered.json()["items"]] == ["edge-api"]

    decommissioned = client.patch(
        "/v1/devices/edge-api/lifecycle",
        json={"lifecycle": "decommissioned", "reason": "asset retired"},
    )
    assert decommissioned.status_code == 200
    resurrect = client.patch(
        "/v1/devices/edge-api/lifecycle",
        json={"lifecycle": "active", "reason": "should fail"},
    )
    assert resurrect.status_code == 409
