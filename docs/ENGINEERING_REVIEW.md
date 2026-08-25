# Engineering Review

## Review question

FleetPlane exists to demonstrate the managed-cloud side of autonomous/industrial edge systems:

> identity, state convergence, durable control intent, failure recovery, Microsoft IoT platform composition, IaC, CI/CD, observability, and distributed consistency while preserving local autonomy.

This review therefore separates **portfolio completeness** from **production readiness**.

## Current assessment — v0.6.0

| Dimension | Assessment | Reasoning |
|---|---:|---|
| Autonomous-system boundary | 9/10 | local autonomy and cloud supervisory authority are explicit and tested |
| Distributed-system correctness | 8.5/10 | duplicates, ordering, optimistic concurrency, outbox, leases, idempotency, stale state and replay are first-class |
| Device/fleet domain model | 8.5/10 | provisioning, lifecycle, generation, site/fleet scope and applied/desired state are explicit |
| Local reproducibility | 9.5/10 | one-command scenario, evidence envelope, SQLite, simulator, API and Compose path |
| Microsoft IoT platform architecture | 9/10 | IoT Hub/DPS/Device Registry/Azure IoT Operations responsibilities are explicit; IoT Hub/Cosmos/Functions paths are executable/contract-tested |
| Cloud-scale read model | 8/10 | replay-safe materialized global/site/fleet projection exists; exact cross-partition fallback is retained for validation/backfill |
| API design | 7.5/10 | strict contracts, pagination, correlation and idempotent 202 command semantics; enterprise operator auth remains |
| Verification rigor | 9/10 | explicit evidence classes, claim/test traceability and threats-to-validity documentation |
| Observability | 7/10 | structured correlation + Prometheus + audit; full Azure OTel/SLO evidence remains |
| Security architecture | 6.5/10 | strong device/control trust boundaries; operator Entra RBAC/private networking/immutable audit remain |
| IaC / delivery | 7.5/10 | Terraform, validation and OIDC plan; remote state/promotion/immutable deployment not complete |
| GitHub reviewer experience | 9.5/10 | quickstart, methodology, evidence matrix, design decisions, use cases and explicit limitations |

## Portfolio result

**Strong cloud/distributed-systems showcase for an autonomous-systems engineer.**

The repository demonstrates capabilities that a pure ML/embedded project usually does not:

- Microsoft IoT platform mapping across IoT Hub, DPS, Device Registry, IoT Operations, Functions and Cosmos;
- logical-partition/transaction design;
- event-driven Functions composition;
- cloud/device semantics;
- Terraform and cloud identity;
- concurrency/failure engineering;
- device fleet lifecycle;
- operational API/read-model design;
- reproducible evidence rather than screenshots alone.

## Production result

**Credible architecture/reference implementation, not a finished production fleet platform.**

That boundary is intentional. A strong portfolio repo is more credible when it states the missing production controls than when it claims every enterprise property.

## Strongest choices

### 1. Local autonomy is an invariant

The device can reject cloud restart/configuration intent. The cloud is not represented as a safety authority.

### 2. The outbox is part of the business transition

Configuration/command intent is durable before dispatch is attempted.

### 3. Provider semantics remain distinct

A twin update is cloud acceptance, not application. Direct-method transport result and device business ACK are not collapsed into one meaning.

### 4. Identity starts at provisioning, not telemetry

Unknown/replaced/decommissioned devices cannot materialize from a payload alone.

### 5. Hardware generation is separate from boot identity

A reboot and replacement/factory-reset hardware have different semantics.

### 6. Cloud adapters are inside the verification surface

Azure/Cosmos adapter code is tested instead of being excluded as “integration code.”

### 7. Cloud fleet summary is now a projection problem

Per-device state changes emit transactional summary deltas; the change-feed projector maintains replay-safe global/fleet/site summaries.

### 8. Public claims have evidence classes

Local behavior, provider contracts, and live-provider qualification are deliberately not conflated.

## Highest-value remaining weaknesses

### P1. Operator identity and authorization

Current local/admin identity cannot prove that an audit actor corresponds to a validated enterprise principal.

**Next:** Entra JWT validation, role/site/fleet authorization, actor derived server-side.

### P2. End-to-end Azure observability

Request/business correlation exists, but Azure Functions/IoT operations do not yet publish an end-to-end OpenTelemetry trace/SLO report.

**Next:** OpenTelemetry/Application Insights spans for request → durable state/outbox → provider dispatch → ACK, plus a small declared SLO experiment.

### P3. Live Microsoft IoT qualification

The Microsoft mappings are contract-tested, but the release deliberately does not claim measured IoT Hub/DPS/Device Registry/IoT Operations performance.

**Next:** a declared Azure qualification environment covering DPS X.509 provisioning, real IoT Hub messaging/twins/methods, Cosmos RU/latency, Device Registry projection, and an Arc/Azure IoT Operations reference site.

### P4. Public command-contract evolution

Generic command payload dictionaries are easy for a prototype but weak for long-lived compatibility.

**Next:** discriminated/versioned payload and result types.

### P5. Deployment lifecycle

There is no committed remote Terraform state, environment promotion, or immutable release artifact/digest workflow.

### P6. Security productization

Private networking, custom least-privilege roles, WORM/SIEM audit export, SBOM/signing and richer secret management remain.

### P7. Identity-only telemetry ingress

The compact IoT Hub built-in Event Hubs-compatible ingest path still uses a scoped SAS connection string. A hardened deployment should route into a custom Event Hubs endpoint using managed identity.

## Choices intentionally not made

### Kubernetes

No demonstrated scheduling requirement justifies AKS in the baseline. Adding it would increase surface area more than learning value.

### Another ML model

FleetPlane exists specifically to show the systems/cloud layer around intelligent devices.

### Raw telemetry lake

High-rate historical analytics is a separate data-plane problem.

### Multi-cloud parity

FleetPlane v0.6 is deliberately Microsoft-first. Additional cloud implementations are not a portfolio goal; any future provider adapter should be driven by a concrete customer requirement.

## Recommended portfolio stopping point

FleetPlane is already publishable. The highest-return additions before freezing the portfolio version are:

1. Entra-backed operator identity;
2. OpenTelemetry + one small SLO/evidence report;
3. one live Microsoft IoT qualification environment if desired.

After that, multi-region DR, immutable audit infrastructure, sophisticated tenancy, private networking and extensive deployment governance are productization work rather than necessary proof of the original engineering goal.
