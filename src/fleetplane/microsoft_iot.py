from __future__ import annotations

import os
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from fleetplane.domain.enums import DeviceLifecycle
from fleetplane.domain.models import DeviceState


class MicrosoftConnectivityPattern(StrEnum):
    """Microsoft IoT connectivity patterns used by FleetPlane."""

    IOT_HUB_DIRECT = "iot_hub_direct"
    IOT_OPERATIONS_EDGE = "iot_operations_edge"


class MicrosoftPlatformModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: str = "Microsoft Azure IoT"
    management_plane: str = "Azure Device Registry"
    cloud_connectivity: str = "Azure IoT Hub"
    zero_touch_provisioning: str = "Azure IoT Hub Device Provisioning Service"
    industrial_edge: str = "Azure IoT Operations enabled by Azure Arc"
    control_execution: str = "Azure Functions"
    control_state: str = "Azure Cosmos DB"
    operator_identity: str = "Microsoft Entra ID"
    operations_observability: str = "Azure Monitor / Application Insights"
    analytics_plane: str = "Microsoft Fabric or customer-selected analytics destination"


class DeviceRegistryProjection(BaseModel):
    """Provider-neutral representation of the Azure Device Registry resource FleetPlane wants."""

    model_config = ConfigDict(extra="forbid")

    namespace: str
    device_name: str
    external_device_id: str
    enabled: bool
    attributes: dict[str, str | int | float | bool]
    tags: dict[str, str]


class DPSProvisioningIntent(BaseModel):
    """The provisioning intent FleetPlane expects DPS to enforce for a direct-cloud device."""

    model_config = ConfigDict(extra="forbid")

    registration_id: str
    id_scope: str | None = None
    attestation: str = "x509"
    allocation_policy: str = "hashed"
    target_iot_hub: str | None = None
    initial_twin_tags: dict[str, str]
    initial_twin_desired: dict[str, str | int | float | bool]


class IoTOperationsAssetIntent(BaseModel):
    """FleetPlane's management intent for an asset connected through Azure IoT Operations."""

    model_config = ConfigDict(extra="forbid")

    namespace: str
    site_id: str
    fleet_id: str
    external_asset_id: str
    connection_path: str = "OPC UA/MQTT -> Azure IoT Operations -> Azure Device Registry"
    data_plane: str = "Azure IoT Operations MQTT broker and data flows"
    cloud_projection: str = "Azure Device Registry asset/device resource"


class MicrosoftIoTPlatform:
    """Maps FleetPlane domain state onto Microsoft's IoT platform concepts.

    This class deliberately contains no Azure network calls. It is the deterministic mapping layer
    used by API/docs/tests and by concrete Azure adapters.
    """

    def __init__(
        self,
        *,
        namespace: str = "fleetplane-prod",
        dps_id_scope: str | None = None,
        iothub_hostname: str | None = None,
    ) -> None:
        self.namespace = namespace
        self.dps_id_scope = dps_id_scope
        self.iothub_hostname = iothub_hostname

    @staticmethod
    def topology() -> MicrosoftPlatformModel:
        return MicrosoftPlatformModel()

    def registry_projection(self, state: DeviceState) -> DeviceRegistryProjection:
        enabled = state.lifecycle not in {
            DeviceLifecycle.DISABLED,
            DeviceLifecycle.DECOMMISSIONED,
        }
        attributes: dict[str, str | int | float | bool] = {
            "fleetplane.lifecycle": state.lifecycle.value,
            "fleetplane.siteId": state.site_id,
            "fleetplane.fleetId": state.fleet_id,
            "fleetplane.deviceGeneration": state.device_generation,
            "fleetplane.health": state.health_state.value,
            "fleetplane.desiredRevision": state.desired_revision,
            "fleetplane.reportedRevision": state.reported_revision,
            "fleetplane.configConverged": state.config_converged,
        }
        if state.model_version is not None:
            attributes["fleetplane.modelVersion"] = state.model_version
        return DeviceRegistryProjection(
            namespace=self.namespace,
            device_name=state.device_id,
            external_device_id=state.device_id,
            enabled=enabled,
            attributes=attributes,
            tags={
                "fleetplane-site": state.site_id,
                "fleetplane-fleet": state.fleet_id,
                "fleetplane-lifecycle": state.lifecycle.value,
            },
        )

    def dps_intent(self, state: DeviceState) -> DPSProvisioningIntent:
        return DPSProvisioningIntent(
            registration_id=state.device_id,
            id_scope=self.dps_id_scope,
            target_iot_hub=self.iothub_hostname,
            initial_twin_tags={
                "siteId": state.site_id,
                "fleetId": state.fleet_id,
                "deviceGeneration": str(state.device_generation),
            },
            initial_twin_desired={
                "fleetplaneLifecycle": state.lifecycle.value,
                "deviceGeneration": state.device_generation,
            },
        )

    def iot_operations_intent(self, state: DeviceState) -> IoTOperationsAssetIntent:
        return IoTOperationsAssetIntent(
            namespace=self.namespace,
            site_id=state.site_id,
            fleet_id=state.fleet_id,
            external_asset_id=state.device_id,
        )


class NamespaceDevicesOperations(Protocol):
    def create_or_replace(
        self,
        resource_group_name: str,
        namespace_name: str,
        device_name: str,
        resource: Any,
    ) -> Any: ...


class DeviceRegistryManagementClient(Protocol):
    namespace_devices: NamespaceDevicesOperations


class AzureDeviceRegistryGateway:
    """Azure Device Registry management-plane adapter.

    The IoT Hub + Device Registry integration remains a Microsoft preview feature. The adapter is
    therefore contract-tested and opt-in; Azure IoT Operations uses Device Registry as a GA
    management-plane service.
    """

    def __init__(
        self,
        client: DeviceRegistryManagementClient,
        *,
        resource_group: str,
        namespace: str,
        location: str,
    ) -> None:
        self.client = client
        self.resource_group = resource_group
        self.namespace = namespace
        self.location = location

    @classmethod
    def from_environment(cls) -> "AzureDeviceRegistryGateway":
        try:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.deviceregistry import DeviceRegistryMgmtClient
        except ImportError as exc:  # pragma: no cover - exercised only with azure extra installed
            raise RuntimeError("install FleetPlane with the 'azure' extra") from exc
        subscription_id = os.getenv("AZURE_SUBSCRIPTION_ID")
        resource_group = os.getenv("FLEETPLANE_AZURE_RESOURCE_GROUP")
        namespace = os.getenv("FLEETPLANE_DEVICE_REGISTRY_NAMESPACE")
        location = os.getenv("FLEETPLANE_AZURE_LOCATION", "northeurope")
        if not subscription_id or not resource_group or not namespace:
            raise RuntimeError(
                "AZURE_SUBSCRIPTION_ID, FLEETPLANE_AZURE_RESOURCE_GROUP and "
                "FLEETPLANE_DEVICE_REGISTRY_NAMESPACE are required"
            )
        client = DeviceRegistryMgmtClient(
            credential=DefaultAzureCredential(), subscription_id=subscription_id
        )
        return cls(
            client,
            resource_group=resource_group,
            namespace=namespace,
            location=location,
        )

    def upsert(self, projection: DeviceRegistryProjection) -> Any:
        resource = {
            "location": self.location,
            "tags": projection.tags,
            "properties": {
                "externalDeviceId": projection.external_device_id,
                "enabled": projection.enabled,
                "attributes": projection.attributes,
            },
        }
        operation = self.client.namespace_devices
        create = getattr(operation, "create_or_replace", None)
        if create is None:
            create = getattr(operation, "begin_create_or_replace", None)
        if create is None:
            raise RuntimeError("Device Registry SDK does not expose create-or-replace")
        result = create(
            self.resource_group,
            projection.namespace,
            projection.device_name,
            resource,
        )
        wait = getattr(result, "result", None)
        return wait() if callable(wait) else result
