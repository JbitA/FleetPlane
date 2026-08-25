# Key Architecture Decisions

This document records the main design choices and the alternatives intentionally rejected or deferred.

## D1 — Supervisory cloud, locally authoritative edge

**Decision:** FleetPlane never becomes the hard real-time/safety controller.

**Reason:** network/cloud availability cannot be a precondition for safe local operation. The device evaluates local command/configuration policy.

**Consequence:** a cloud request may remain durable and still be rejected by the device.

## D2 — Microsoft IoT platform is the production substrate

**Decision:** FleetPlane is built around Microsoft's IoT platform: Device Registry, IoT Hub, DPS, Azure IoT Operations, Functions, Cosmos, Entra, Monitor, and optional Fabric integration.

**Reason:** the project should demonstrate deep use of a managed hyperscale IoT platform rather than rebuild broker/provisioning/edge-data primitives. FleetPlane's differentiation is the operational semantics above those services.

**Constraint:** domain state transitions remain isolated from Azure SDK calls so that correctness can be tested deterministically and service upgrades do not invade the domain model.

## D3 — Per-device control aggregate

**Decision:** Cosmos control documents for one device use `/device_id` as the logical partition.

**Reason:** configuration, command, audit, outbox, receipt, and state transitions frequently require one atomic per-device boundary. Cosmos transactional batches are ACID within a logical partition.

**Trade-off:** fleet-wide/global views cannot be maintained transactionally in the same partition and therefore require projections.

## D4 — Transactional outbox instead of synchronous network coupling

**Decision:** state transition + audit + outbound intent commit before external dispatch.

**Reason:** database and network transport do not share an atomic transaction. The outbox makes post-commit retry explicit.

**Trade-off:** control actions become eventually delivered rather than synchronous with API acceptance.

## D5 — Optimistic concurrency instead of process locks

**Decision:** projections/configuration transitions use versions/ETags/CAS semantics.

**Reason:** process-local locks do not coordinate horizontally scaled workers.

**Trade-off:** contention requires retry/backoff/deadline handling.

## D6 — Desired properties for state; direct methods for interaction

**Decision:** Azure persistent desired configuration maps to IoT Hub twin desired properties; immediate interactive commands map to direct methods.

**Reason:** this matches the service semantics documented by Azure: desired properties persist across disconnects, while direct methods are synchronous online interactions.

## D7 — Device generation is distinct from boot ID

**Decision:** telemetry identifies logical device, physical generation, boot session, and sequence separately.

**Reason:** rebooting current hardware is different from replacing/factory-resetting hardware under the same business identity.

## D8 — Persistent bounded edge spool

**Decision:** device telemetry is first persisted locally and the spool has a maximum row count.

**Reason:** offline behavior is a normal operating mode, but “durable” must not mean “unbounded disk growth.”

**Trade-off:** when the bound is reached, retention policy becomes a business/operational decision; the simulator currently drops the oldest queued record.

## D9 — Command journal at the device

**Decision:** processed command IDs and results are persisted.

**Reason:** at-least-once/retry behavior can otherwise execute a restart-class action multiple times.

## D10 — Materialized summaries rather than dashboard reconciliation

**Decision:** local state maintains a summary projection; Azure state emits transactional summary deltas and projects them through Cosmos change feed.

**Reason:** a read endpoint should not trigger O(N) reconciliation and writes across the entire fleet.

**Trade-off:** summary state becomes eventually consistent with the per-device aggregate; an exact query is kept for validation/backfill.

## D11 — SQLite as the local semantic reference

**Decision:** local reproduction uses SQLite/WAL rather than a mandatory broker/database stack.

**Reason:** the reference scenario should run from one Python installation while still exercising transactions, persistence, migrations, and concurrency.

**Trade-off:** SQLite performance does not represent Cosmos/Azure performance; provider-contract tests exist for semantic parity.

## D12 — No Kubernetes in the baseline

**Decision:** do not introduce AKS merely to demonstrate Kubernetes.

**Reason:** FleetPlane's cloud workload is currently event-driven and state-oriented. Kubernetes would increase deployment surface without solving a demonstrated scheduling requirement.

## D13 — Raw telemetry history is separate from control state

**Decision:** the control store keeps idempotency receipts/current projection, not an indefinite high-volume sensor history.

**Reason:** operational control and analytical history have different volume, retention, cost, and query patterns.

## D14 — Provider fakes are semantic evidence only

**Decision:** fake Cosmos/IoT Hub clients are used for deterministic adapter contract tests.

**Reason:** they make cloud semantics testable without external dependencies.

**Constraint:** they are never treated as evidence of real Azure performance/availability.

## D15 — Azure Device Registry is the Microsoft management-plane projection

**Decision:** FleetPlane maps device lifecycle/site/fleet/generation/health posture into Azure Device Registry attributes and tags.

**Reason:** Device Registry is Microsoft's common device/asset management plane across Azure IoT Operations and IoT Hub. It gives FleetPlane an ARM/RBAC/Policy/Resource Graph representation without making Cosmos serve as Azure's resource registry.

**Maturity boundary:** Device Registry is GA with Azure IoT Operations; its IoT Hub integration is currently preview and is not required for FleetPlane control correctness.

## D16 — DPS owns direct-cloud bootstrap

**Decision:** zero-touch IoT Hub assignment belongs to Device Provisioning Service, not a FleetPlane custom provisioning server.

**Reason:** provisioning identity/allocation at scale is horizontal infrastructure. FleetPlane expresses provisioning intent and business metadata; DPS performs the managed assignment.

## D17 — Azure IoT Operations owns industrial edge transport

**Decision:** FleetPlane does not build a second industrial MQTT/OPC UA edge platform.

**Reason:** Azure IoT Operations already provides an Arc-enabled edge data plane with MQTT, OPC UA connectivity, discovery, and data flows. FleetPlane consumes its device/asset representation and adds operational governance.
