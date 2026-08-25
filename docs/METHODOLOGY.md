# Experimental Methodology

FleetPlane is software engineering rather than a scientific instrument, but the repository deliberately applies a falsifiable experimental structure to its distributed-system claims.

The objective is to prevent statements such as “reliable,” “idempotent,” “offline capable,” or “cloud ready” from existing only as architecture prose.

## 1. Research questions

The reference implementation is organized around the following research questions.

| ID | Research question |
|---|---|
| RQ1 | Can duplicate telemetry be detected without applying the same observation twice? |
| RQ2 | Can the control plane distinguish missing, late, duplicate, and current telemetry while preventing projection rewind? |
| RQ3 | Can an edge node continue locally while disconnected and later reconcile stored telemetry? |
| RQ4 | Can desired configuration converge while preserving device-side policy and stale-revision rejection? |
| RQ5 | Can cloud control intent remain durable if the process fails between database commit and network delivery? |
| RQ6 | Can concurrent workers modify one logical device without silently losing the newest state? |
| RQ7 | Can repeated command submission/delivery remain safe at both API and device boundaries? |
| RQ8 | Can unauthorized/retired hardware be prevented from silently assuming a logical device identity? |
| RQ9 | Can fleet/site summaries be maintained under replay/late change events without scanning the entire fleet on every read? |
| RQ10 | Can Azure adapters preserve the same domain semantics without turning provider fakes into performance claims? |

## 2. Hypotheses and acceptance criteria

### H1 — duplicate telemetry is idempotent

**Intervention:** submit the identical telemetry envelope twice.

**Acceptance:**

- first event is accepted;
- second is classified `duplicate`;
- the second submission does not advance the projection as a new observation.

### H2 — out-of-order delivery cannot rewind current state

**Intervention:** construct two increasing sequence numbers, deliver the newer one first, then the older one.

**Acceptance:**

- newer event is accepted with a gap if a sequence was skipped;
- older event is classified out-of-order;
- persisted current sequence remains the newer sequence.

A separate multithreaded test increases the adversarial strength by allowing independent workers to race.

### H3 — disconnection does not remove local autonomy

**Intervention:** mark 20% of the simulated fleet offline for three ticks.

**Acceptance:**

- the local telemetry sequence continues;
- events are persisted in device-local SQLite spools;
- the reference 100-device scenario records 60 spooled events;
- reconnection flushes acceptable events without requiring the device to recreate them.

### H4 — desired configuration respects device policy and converges

**Intervention:** five devices enforce a minimum telemetry interval of 60 seconds while the first desired revision requests 30 seconds.

**Acceptance:**

- those devices reject revision 1 by policy;
- unrestricted devices apply revision 1;
- a second compliant revision converges across the complete fleet;
- a later replay of revision 1 is rejected as stale by every device.

### H5 — durable control intent is not coupled to a network call

**Intervention:** inspect/test command and configuration creation around the storage boundary.

**Acceptance:** business state, audit, and outbox work are committed together before dispatch. A worker can later claim and retry the outbox item independently.

### H6 — one outbox item has one active owner

**Intervention:** two independent worker identities attempt to claim the same pending work.

**Acceptance:** exactly one lease succeeds for the active lease interval.

### H7 — retries do not duplicate direct actions

**Intervention:** create/replay commands using API idempotency keys and repeat an already processed `command_id` at the device.

**Acceptance:**

- repeated client creation returns the same logical command;
- repeated device delivery returns the journaled result rather than executing a second action.

### H8 — fleet membership is authoritative

**Intervention:** submit telemetry from unknown, mismatched-generation, disabled, and decommissioned identities.

**Acceptance:** invalid identities/lifecycle states are rejected before operational projection.

### H9 — summary projection is replay safe

**Intervention:** apply Cosmos-style `summary_delta` documents, replay the same documents, then deliver an older projection after a newer device projection.

**Acceptance:**

- first application updates global/fleet/site scopes;
- exact replay performs zero further scope updates;
- an older projection cannot replace a newer device contribution;
- materialized counts match the exact control-store calculation in the contract test.

## 3. Controlled reference scenario

The simulator intentionally fixes the following parameters for the README reference run:

```text
devices = 100
restricted_devices = 5
offline_fraction = 0.20
offline_ticks = 3
restricted_min_interval_s = 60
revision_1_interval_s = 30
revision_2_interval_s = 120
revision_2_anomaly_threshold = 0.84
```

Each device uses a deterministic pseudo-random generator seeded as `1000 + device_index`. Randomized telemetry values therefore vary predictably for the same device/order while failure injection itself is deterministic.

Timestamps and UUIDs are not fixed because the behavioral assertions do not depend on their literal values.

## 4. Independent variables

The principal manipulated conditions are:

- message repetition;
- message sequence order;
- network availability;
- local device configuration policy;
- desired configuration revision;
- device operating mode;
- worker concurrency;
- lifecycle state;
- device generation;
- projection version ordering.

## 5. Dependent variables / observations

The system observes:

- ingestion disposition;
- persisted current sequence;
- sequence-gap/out-of-order classification;
- spool depth;
- configuration ACK code;
- desired/reported revision convergence;
- command status/ACK code;
- outbox lease ownership/status;
- lifecycle authorization result;
- fleet summary counters;
- summary projection version cursor.

## 6. Experimental isolation

The default scenario uses temporary directories for cloud/device SQLite files. A run starts from a new local state unless a developer explicitly starts the API with a persistent path.

This removes cross-run state contamination from the deterministic showcase.

The simulator runs local adapters so the reference behavior is independent of external network availability and provider quotas.

## 7. Evidence classes

FleetPlane uses three evidence classes.

### Class A — deterministic local evidence

Examples:

- unit tests;
- concurrency tests;
- reference scenario;
- SQLite transactions;
- edge spool/journal behavior.

These can support statements about application semantics under the tested conditions.

### Class B — provider-contract evidence

Provider-like fakes emulate the interaction surface used by FleetPlane for Cosmos DB and IoT Hub.

These tests can support statements such as:

- a per-device transition is assembled as one transactional batch;
- an ETag conflict causes the adapter's expected behavior;
- a twin write is not treated as a device ACK;
- authenticated device metadata is required;
- change-feed projection logic is replay safe.

They **cannot** support claims about Azure performance, availability, exact service-side retry behavior, or quotas.

### Class C — live provider qualification

A live qualification run would require publishing at minimum:

```text
date/time
Azure region
service SKUs
provider/SDK/Terraform versions
fleet size
telemetry rate and payload size
command/configuration workload
RU settings
Function concurrency/scaling settings
network topology
measurement window
raw/aggregated results
```

No Class C performance claim is currently made.

## 8. Reproducibility artifact

The CLI can write a self-describing evidence envelope:

```bash
fleetplane showcase --devices 100 --restricted-devices 5 --evidence evidence/reference-scenario.json
```

The envelope contains:

- evidence schema version;
- FleetPlane release version;
- Python version;
- host platform;
- scenario parameters;
- raw scenario result;
- acceptance assertions.

This is intentionally a small artifact. It records **what was run and what was observed**, not an invented benchmark report.

## 9. Statistical interpretation

The reference scenario is deterministic and assertion-driven. It does not estimate a population parameter and therefore does not use p-values or confidence intervals.

The correct interpretation is:

> The specified implementation satisfied the specified invariants for the executed controlled conditions.

It is not:

> The implementation is proven reliable for every deployment/environment.

When latency, RU cost, failure probability, or throughput are evaluated against a live provider, repeated sampling, distribution summaries, confidence intervals, warm/cold separation, and workload declaration would become appropriate.

## 10. Threats to validity

### Internal validity

Provider fakes can diverge from real service behavior. This is mitigated by keeping provider-specific claims narrow and by using the fakes to test FleetPlane semantics rather than Azure performance.

### External validity

The simulator does not model every device/network failure mode. Real hardware may have clock drift, storage corruption, partial power failure, TLS/certificate lifecycle problems, broker throttling, and much longer offline periods.

### Construct validity

“Healthy” is a project-defined operational projection based on telemetry freshness, sensor state, battery, backlog, convergence, and lifecycle. It is not a universal measure of physical asset health.

### Performance validity

The 100-device scenario measures behavior, not capacity. Running 100 simulated objects on one process says nothing about hyperscale IoT throughput.

### Safety validity

Demonstrating that a simulated edge policy can reject a restart proves an authority boundary in software. It does not constitute safety analysis, hazard analysis, SIL/ASIL compliance, or certification.

## 11. Falsifiability rule for new features

A new reliability claim should enter the README only when at least one of the following exists:

1. an automated invariant test;
2. a reproducible reference scenario assertion;
3. a provider-contract test;
4. a clearly labeled live qualification result.

If none exists, describe it as a **design target** or **roadmap item**, not an implemented property.
