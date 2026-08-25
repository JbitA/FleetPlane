# Azure Runtime and Infrastructure Model

This document is the deployment-level companion to [MICROSOFT_IOT_PLATFORM.md](MICROSOFT_IOT_PLATFORM.md).

FleetPlane's production architecture is Microsoft IoT-native. The current executable Azure path is the **direct-cloud IoT Hub pattern**; the **Azure IoT Operations pattern** is represented as the industrial-edge extension.

## Executable cloud path

```text
DPS
 ↓
IoT Hub
 ├── device-to-cloud telemetry
 ├── twin desired/reported state
 └── direct methods
      ↓
Azure Functions
 ├── authenticated telemetry ingress
 ├── FleetPlane API
 ├── reconciliation/expiry/outbox worker
 └── Cosmos change-feed summary projector
      ↓
Cosmos DB
 ├── control /device_id
 ├── summaries /scope_id
 └── leases /id
```

Azure Device Registry provides the management-plane representation. FleetPlane's deterministic Device Registry resource projection is implemented in `src/fleetplane/microsoft_iot.py`.

## Application mode

The Azure runtime selects:

```text
FLEETPLANE_MODE=azure
```

Core environment settings include:

```text
FLEETPLANE_COSMOS_ENDPOINT
FLEETPLANE_COSMOS_DATABASE
FLEETPLANE_COSMOS_CONTAINER
FLEETPLANE_COSMOS_SUMMARY_CONTAINER
FLEETPLANE_IOTHUB_HOST_NAME
FLEETPLANE_DEVICE_REGISTRY_NAMESPACE
FLEETPLANE_DPS_ID_SCOPE
```

`DefaultAzureCredential` is used by service-side SDK adapters where supported.

## Device Provisioning Service

DPS is the direct-cloud bootstrap service. FleetPlane does not invent a proprietary assignment protocol.

The domain-to-Microsoft mapping exposes a `DPSProvisioningIntent` containing:

```text
registration_id
id_scope
attestation = x509
target IoT Hub
initial twin tags
initial desired metadata
```

The Terraform model includes `azurerm_iothub_dps` and links it to the IoT Hub. Actual device enrollment/certificate issuance is intentionally outside the local reference scenario.

## Azure Device Registry

Terraform declares an Azure Device Registry namespace through AzAPI. Device resources are projected from FleetPlane state through `AzureDeviceRegistryGateway`.

The management projection includes site, fleet, lifecycle, hardware generation, health, desired/reported revisions, convergence, and model version.

Important: Microsoft currently documents IoT Hub integration with Device Registry as preview. The FleetPlane control aggregate therefore does not depend on Device Registry availability for correctness. For Azure IoT Operations, Device Registry is the GA management-plane path.

## IoT Hub identity boundary

Device authentication is an IoT Hub concern. FleetPlane additionally binds the authenticated IoT Hub connection device ID to the `device_id` inside the application payload before the event can affect device state.

A device authenticated as `edge-007` cannot mutate the projection for payload identity `edge-042`.

## Desired state and direct methods

FleetPlane maps:

```text
persistent desired posture → IoT Hub twin desired properties
interactive operation      → IoT Hub direct method
```

A successful twin update is transport acceptance, not proof of device application. The device must separately report/acknowledge the applied revision.

## Cosmos data model

One control container uses:

```text
partition key: /device_id
```

A device partition co-locates state that needs an atomic application boundary. Fleet-wide summaries are projected separately through change feed into a `/scope_id` summary container.

## Azure IoT Operations extension

Industrial sites use:

```text
OPC UA / MQTT assets
        ↓
Azure IoT Operations on Azure Arc
        ↓
Azure Device Registry
        ↓
FleetPlane governance
```

The baseline Terraform does not deploy an Arc Kubernetes cluster or Azure IoT Operations instance. Those require site-specific deployment planning such as cluster topology, broker cardinality, storage, network integration, and OT protocol configuration. The repository models the integration boundary rather than disguising those decisions behind a generic template.

## Analytics plane

Raw/high-volume historical telemetry is separate from FleetPlane's control database. Azure IoT Operations data flows or IoT Hub routing can feed Microsoft Fabric or another customer-selected analytical destination.

FleetPlane persists the control facts needed for device governance, not every historical sensor sample.

## Terraform safety model

```bash
cd infra/terraform
terraform init
terraform fmt -check -recursive
terraform validate
terraform plan -var='deployment_enabled=true'
```

`deployment_enabled` defaults to `false`. There is no committed automatic apply workflow.

## GitHub OIDC

`.github/workflows/azure-plan.yml` uses GitHub OIDC for Azure login and requests only the permissions needed for plan execution. No long-lived Azure client secret is required by the workflow design.

## Production-hardening work still open

- Entra-authenticated FleetPlane operator API;
- remote Terraform state and environment promotion;
- private endpoints/private DNS;
- identity-only IoT Hub ingestion route rather than built-in Event Hub-compatible SAS path;
- managed device certificate/enrollment lifecycle;
- live Device Registry + IoT Hub preview qualification;
- live Azure IoT Operations/Arc qualification;
- Application Insights/OpenTelemetry SLO evidence;
- multi-region recovery qualification.
