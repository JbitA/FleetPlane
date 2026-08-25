from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from fleetplane.domain.enums import DeviceLifecycle, HealthState
from fleetplane.domain.models import DeviceState
from fleetplane.microsoft_iot import AzureDeviceRegistryGateway, MicrosoftIoTPlatform
from fleetplane.runtime import Settings, build_runtime
from tests.conftest import provision_active


class FakeNamespaceDevices:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, dict]] = []

    def create_or_replace(self, resource_group: str, namespace: str, device: str, resource: dict):
        self.calls.append((resource_group, namespace, device, resource))
        return {"id": f"/namespaces/{namespace}/devices/{device}", **resource}


class FakeDeviceRegistryClient:
    def __init__(self) -> None:
        self.namespace_devices = FakeNamespaceDevices()


def test_microsoft_platform_maps_fleetplane_state_to_device_registry_and_dps():
    platform = MicrosoftIoTPlatform(
        namespace="adr-fleetplane-prod",
        dps_id_scope="0neABC12345",
        iothub_hostname="iot-fleetplane.azure-devices.net",
    )
    state = DeviceState(
        device_id="edge-42",
        site_id="oulu",
        fleet_id="inspection",
        lifecycle=DeviceLifecycle.ACTIVE,
        device_generation=4,
        health_state=HealthState.HEALTHY,
        desired_revision=12,
        reported_revision=12,
        model_version="rail-v8",
    )

    registry = platform.registry_projection(state)
    assert registry.namespace == "adr-fleetplane-prod"
    assert registry.enabled
    assert registry.attributes["fleetplane.deviceGeneration"] == 4
    assert registry.attributes["fleetplane.configConverged"] is True
    assert registry.tags["fleetplane-site"] == "oulu"

    dps = platform.dps_intent(state)
    assert dps.registration_id == "edge-42"
    assert dps.id_scope == "0neABC12345"
    assert dps.target_iot_hub == "iot-fleetplane.azure-devices.net"
    assert dps.attestation == "x509"

    edge = platform.iot_operations_intent(state)
    assert edge.site_id == "oulu"
    assert "Azure IoT Operations" in edge.connection_path


def test_disabled_or_decommissioned_device_is_disabled_in_device_registry_projection():
    platform = MicrosoftIoTPlatform(namespace="adr-prod")
    for lifecycle in (DeviceLifecycle.DISABLED, DeviceLifecycle.DECOMMISSIONED):
        projection = platform.registry_projection(
            DeviceState(device_id=f"edge-{lifecycle.value}", lifecycle=lifecycle)
        )
        assert projection.enabled is False


def test_device_registry_gateway_builds_arm_resource_contract():
    client = FakeDeviceRegistryClient()
    gateway = AzureDeviceRegistryGateway(
        client,
        resource_group="rg-fleetplane",
        namespace="adr-fleetplane",
        location="northeurope",
    )
    platform = MicrosoftIoTPlatform(namespace="adr-fleetplane")
    projection = platform.registry_projection(
        DeviceState(device_id="edge-1", site_id="site-a", fleet_id="fleet-a")
    )
    result = gateway.upsert(projection)
    assert result["properties"]["externalDeviceId"] == "edge-1"
    [call] = client.namespace_devices.calls
    assert call[0:3] == ("rg-fleetplane", "adr-fleetplane", "edge-1")
    assert call[3]["properties"]["attributes"]["fleetplane.siteId"] == "site-a"


def test_api_exposes_microsoft_platform_projection(tmp_path):
    runtime = build_runtime(
        Settings(
            mode="local",
            sqlite_path=str(tmp_path / "fleet.db"),
            device_registry_namespace="adr-demo",
            dps_id_scope="scope-demo",
            iothub_host_name="hub-demo.azure-devices.net",
        )
    )
    try:
        provision_active(runtime, "edge-api", site_id="site-1", fleet_id="fleet-1")
        from fleetplane.api.app import create_app

        client = TestClient(create_app(runtime))
        topology = client.get("/v1/platform/microsoft")
        assert topology.status_code == 200
        assert topology.json()["topology"]["management_plane"] == "Azure Device Registry"

        device = client.get("/v1/platform/microsoft/devices/edge-api")
        assert device.status_code == 200
        body = device.json()
        assert body["device_registry"]["namespace"] == "adr-demo"
        assert body["dps"]["id_scope"] == "scope-demo"
        assert body["iot_operations"]["site_id"] == "site-1"
    finally:
        runtime.close()
