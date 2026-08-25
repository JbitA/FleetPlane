# Roadmap

FleetPlane v0.6 makes Microsoft's IoT platform the production substrate. The remaining roadmap is therefore about **deepening the Microsoft integration and production evidence**, not adding alternative clouds.

## Next — operator identity and authorization

- Microsoft Entra authentication for FastAPI/Functions endpoints.
- domain roles for operator, maintainer, auditor, and administrator.
- derive audit actor from validated identity rather than `X-Actor`.

## Next — live Microsoft IoT qualification environment

- DPS X.509 enrollment proof.
- real IoT Hub telemetry/twin/direct-method integration tests.
- real Cosmos RU/latency/concurrency measurements.
- real Azure Device Registry projection test; explicitly track the IoT Hub integration preview boundary.

## Next — Azure IoT Operations industrial-site reference

- Arc-enabled test cluster deployment guide.
- OPC UA reference asset.
- MQTT/data-flow route into Azure/Fabric analytics.
- Device Registry asset-to-FleetPlane identity mapping.

## Next — observability and SLOs

- OpenTelemetry traces into Application Insights.
- ingestion/config-convergence/command-dispatch SLOs.
- failure-budget evidence generated in CI/live qualification.

## Later — enterprise integration

- SAP/EAM/CMMS event adapter for business-grade asset condition events.
- ServiceNow/maintenance workflow integration.
- software/model deployment posture and staged rollout policy.
- Microsoft Fabric analytical export.

## Later — production infrastructure hardening

- remote Terraform state and protected promotion environments.
- private endpoints/private DNS.
- Key Vault integration for unavoidable secret material.
- multi-region recovery design and drills.
- supply-chain artifact signing/attestation.
