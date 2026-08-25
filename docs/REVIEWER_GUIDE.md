# Reviewer Guide

This guide is for a reviewer encountering FleetPlane without prior context.

## What to decide first

FleetPlane should be evaluated as a **Microsoft IoT–native distributed-systems reference implementation**, not as a production SaaS claim.

The core question is whether the repository demonstrates credible reasoning about what happens when intelligent edge systems become a fleet:

```text
identity
lifecycle
message ordering
concurrency
desired/reported state
offline operation
durable intent
idempotent commands
local autonomy
cloud-provider mapping
```

## Five-minute path

Run:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
fleetplane showcase --devices 100 --restricted-devices 5
```

Then inspect:

- `evidence/reference-scenario-v0.6.0.json`
- `tests/test_wire_and_scenario.py`
- `tests/test_concurrency.py`

The experiment is deterministic with respect to the injected failure conditions. It is a behavioral experiment, not a throughput benchmark.

## Fifteen-minute architecture path

Read these files in order:

1. `src/fleetplane/domain/models.py`
2. `src/fleetplane/core/ingestion.py`
3. `src/fleetplane/core/configuration.py`
4. `src/fleetplane/core/commands.py`
5. `src/fleetplane/services/outbox.py`
6. `src/fleetplane/adapters/sqlite_store.py`
7. `src/fleetplane/adapters/cosmos_store.py`
8. `src/fleetplane/microsoft_iot.py`
9. `src/fleetplane/runtime.py`

Questions to ask while reading:

- What is the authoritative identity of one telemetry event?
- What prevents an old event from rewinding current state?
- What happens between committing desired state and talking to a device?
- What does `202 Accepted` actually guarantee?
- What does an IoT Hub twin update prove, and what does it not prove?
- Can duplicate command delivery execute twice at the edge?
- Where does local autonomy override cloud intent?

## Microsoft IoT path

Read:

- `docs/MICROSOFT_IOT_PLATFORM.md`
- `src/fleetplane/microsoft_iot.py`
- `src/fleetplane/adapters/azure_iothub.py`
- `src/fleetplane/azure_ingress.py`
- `azure/functions/function_app.py`
- `infra/terraform/`

The intended production split is:

```text
Direct device
  DPS → IoT Hub → Functions → FleetPlane → Cosmos

Industrial site
  OPC UA/MQTT → Azure IoT Operations → Device Registry → FleetPlane
```

Provider-contract tests verify FleetPlane's mapping and application semantics. They do not claim real Azure throughput, availability, or latency.

## Scientific/evidence path

Read:

1. `docs/METHODOLOGY.md`
2. `docs/VERIFICATION.md`
3. `evidence/verification-v0.6.0.md`

The important evidence distinction is:

```text
Class A: deterministic local behavior
Class B: provider-contract behavior
Class C: live-provider qualification
```

FleetPlane v0.6.0 makes Class A and Class B claims. It does not use them as a substitute for Class C performance evidence.

## Security path

Read:

- `docs/SECURITY.md`
- `tests/test_lifecycle.py`
- `tests/test_device_semantics.py`
- `src/fleetplane/azure_ingress.py`

The most important implemented trust boundaries are:

- telemetry cannot create unknown device identities;
- device generation must match provisioning state;
- authenticated IoT Hub identity is checked against payload identity;
- quarantined/disabled/decommissioned lifecycle states affect allowed operations;
- edge policy can reject cloud intent;
- audit/correlation exists for durable operations.

Important production gaps remain documented: Entra operator RBAC, private networking, production PKI lifecycle, immutable/SIEM audit, and live cloud security qualification.

## What would falsify the project's claims?

Examples:

- a concurrent telemetry test leaves `last_sequence` below the maximum accepted sequence;
- the same command ID causes a restart handler to execute twice;
- replaying an older desired revision replaces a newer applied revision;
- a decommissioned device can re-enter service merely by sending telemetry;
- a duplicate summary event changes the materialized fleet counters twice;
- an IoT Hub transport success is represented as a device-applied configuration ACK.

Those conditions are intentionally represented as automated tests/invariants rather than prose-only claims.

## Bottom line

A strong review should ask whether FleetPlane demonstrates **correct systems thinking at the edge/cloud boundary**. It should not grade the local simulator as though it were a published hyperscale benchmark or safety-certified industrial controller.
