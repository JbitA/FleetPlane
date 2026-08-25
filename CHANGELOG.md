# Changelog

## 0.6.0 — Microsoft IoT platform refocus

- Completed a GitHub presentation/readiness pass: reviewer-first README, claim/evidence/limitation matrix, reviewer guide, security-reporting guidance, and local verification helper.
- Aligned public package metadata with the v0.6.0 reference-implementation status and verified Azure/Cosmos adapters remain inside the coverage surface.
- Reframed FleetPlane as a Microsoft Azure IoT-native supervisory application rather than a generic Azure-capable fleet service.
- Added explicit Microsoft platform topology for IoT Hub, DPS, Azure Device Registry, Azure IoT Operations, Functions, Cosmos, Entra, Monitor, and Fabric/analytics integration.
- Added deterministic Azure Device Registry resource projection and contract-tested management adapter.
- Added DPS provisioning-intent and Azure IoT Operations asset-intent models.
- Added API endpoints for inspecting Microsoft platform topology and per-device projections.
- Added Terraform models for Device Provisioning Service and an Azure Device Registry namespace.
- Documented two production connectivity patterns: direct-cloud IoT Hub devices and industrial edge assets through Azure IoT Operations.
- Preserved FleetPlane's local-authority, transactional-outbox, concurrency, idempotency, and failure-engineering invariants.

## v0.5.0

### Added

- replay-safe Cosmos change-feed materialized summaries for global, fleet, and site scopes;
- same-device transactional `summary_delta` emission;
- per-device summary cursors using highest reflected projection version;
- Azure Functions Cosmos DB change-feed trigger and Terraform `summaries`/`leases` containers;
- request-correlation propagation into durable audit/outbox processing and structured operation logs;
- self-describing `fleetplane showcase --evidence <path>` reproduction artifacts;
- clean showcase stdout with optional `--verbose-operations` dispatcher logging;
- CI-uploaded JUnit XML, coverage XML, reference evidence JSON, and wheel artifact;
- experimental methodology with research questions, hypotheses, acceptance criteria, evidence classes, and threats to validity;
- detailed use-case/system-fit guide;
- architecture decision record-style documentation;
- contributing guide focused on invariants/evidence.

### Changed

- README reorganized for immediate reviewer comprehension and claim-to-evidence traceability;
- verification, Azure, architecture, engineering-review, security, and roadmap documents updated to reflect the materialized-summary architecture;
- project version advanced to `0.5.0`.

## v0.4.0

### Added

- explicit device provisioning;
- site and fleet identity scope;
- lifecycle states: provisioned, active, quarantined, disabled, decommissioned;
- lifecycle-aware telemetry/configuration/command authorization;
- provisioned hardware-generation enforcement;
- lifecycle transitions with audit records and optimistic concurrency;
- site/fleet/lifecycle device query filters;
- SQLite lifecycle/scope schema fields and additive upgrade handling;
- Cosmos lifecycle/scope metadata and transactional lifecycle operations;
- local + Cosmos contract tests for lifecycle behavior;
- comprehensive GitHub architecture, verification, security and roadmap documentation.

### Fixed

- Terraform `prefix` variable block syntax.

## v0.3.0

- Azure IoT Hub and Cosmos DB adapters;
- Azure Functions composition;
- Terraform Azure showcase architecture;
- GitHub CI, CodeQL, Terraform validation and OIDC plan workflow;
- Azure provider-contract test suite.
