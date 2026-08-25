# Verification and Evidence

FleetPlane separates deterministic behavioral evidence, provider-contract evidence, and real-provider qualification. This prevents the repository from turning a local simulator or fake SDK client into a claim about cloud-scale performance.

## 1. Current repository gates

The GitHub CI workflow runs:

```text
Ruff
strict mypy
pytest
branch-aware coverage >= 80%
reference fleet scenario
wheel build
```

It uploads:

```text
JUnit XML
coverage XML
self-describing scenario evidence JSON
built wheel
```

Additional workflows run CodeQL and Terraform formatting/validation. The manually triggered Azure workflow performs an OIDC-authenticated Terraform **plan**, not an apply.

## 2. Current locally executed state

At release preparation for v0.6.0, 47 tests pass and branch-aware coverage is 82.90%, above the repository's 80% gate.

The README intentionally reports the gate as the durable contract and the observed test count as release evidence. Exact coverage percentage can move slightly as branches/environment-specific adapters evolve.

## 3. Reference scenario

The default 100-device run is one coherent failure/recovery experiment.

Expected controlled observations:

```text
100 registered/active devices
20% intentionally offline
60 telemetry events retained in local spools
95 devices accept revision 1
5 devices reject revision 1 by local policy
100 devices converge on revision 2
100 stale revision-1 replays rejected
restart rejected while the selected device is active
ping accepted
```

The scenario separately injects duplicate, sequence-gap, and out-of-order telemetry.

Run:

```bash
fleetplane showcase --devices 100 --restricted-devices 5
```

Write reproduction metadata:

```bash
fleetplane showcase \
  --devices 100 \
  --restricted-devices 5 \
  --evidence evidence/reference-scenario.json
```

The controlled-method interpretation is defined in [METHODOLOGY.md](METHODOLOGY.md).

## 4. Traceability matrix

| Behavior/invariant | Main automated evidence |
|---|---|
| clean CLI result + evidence envelope | `tests/test_cli.py` |
| wire identity binding / malformed payload rejection | `tests/test_wire_and_scenario.py` |
| full deterministic fleet scenario | `tests/test_wire_and_scenario.py` |
| concurrent telemetry projection safety | `tests/test_concurrency.py` |
| concurrent desired-revision allocation | `tests/test_concurrency.py` |
| device configuration/command semantics | `tests/test_device_semantics.py` |
| transactional command/configuration + outbox | `tests/test_outbox_commands.py` |
| exclusive outbox worker leases | `tests/test_outbox_commands.py` |
| command API idempotency and HTTP contracts | `tests/test_api.py` |
| provisioning/lifecycle/generation authorization | `tests/test_lifecycle.py` |
| keyset pagination/local materialized summary | `tests/test_pagination_projection.py` |
| request/business correlation + audit filtering | `tests/test_observability.py` |
| Cosmos/IoT Hub provider contracts | `tests/test_azure_cloud.py` |
| replay-safe Cosmos summary projection | `tests/test_azure_cloud.py` |
| Microsoft IoT topology / Device Registry / DPS / IoT Operations mapping | `tests/test_microsoft_iot_platform.py` |
| Terraform structural contract including DPS + Device Registry namespace | `tests/test_terraform_contract.py` |

## 5. Concurrency evidence

### Telemetry projection

Independent threads submit telemetry for one logical device. The test specifically guards against the lost-update pattern:

```text
worker A reads version N
worker B reads version N
worker B writes newer sequence
worker A writes older sequence last
```

Acceptance requires the current projection to retain the newest sequence rather than the last thread's stale snapshot.

### Desired configuration

Multiple workers allocate configuration revisions concurrently. Revisions must remain unique/monotonic rather than silently overwriting the desired state.

### Outbox leases

Two worker identities attempt to claim the same pending work. The storage transition must permit exactly one active owner.

## 6. Edge durability evidence

Tests verify:

- sequence allocation and spool insertion share one local transaction;
- configuration body/revision persist atomically;
- local spool is bounded;
- already processed command IDs return journaled results;
- stale desired revisions are rejected;
- restart is rejected while the simulated device is active.

## 7. Identity/lifecycle evidence

Tests prove that:

- telemetry cannot create an unknown device;
- a hardware-generation mismatch cannot silently replace current hardware;
- a provisioned device must be activated before normal telemetry;
- quarantine preserves observation/diagnostic behavior while blocking restart-class commands;
- disabled devices reject ordinary control-plane interaction;
- decommissioning is terminal;
- site/fleet/lifecycle filters work in local and Cosmos-style stores.

## 8. Microsoft IoT platform contract evidence

`tests/test_microsoft_iot_platform.py` verifies the FleetPlane-to-Microsoft mapping without making network calls:

- DeviceState → Azure Device Registry attributes/tags;
- disabled/decommissioned lifecycle → disabled Device Registry projection;
- DeviceState → DPS X.509 provisioning intent;
- DeviceState → Azure IoT Operations asset intent;
- Device Registry management API create/replace payload construction;
- platform-inspection API endpoints.

This evidence proves deterministic mapping and adapter contract construction. It does not prove the availability, throughput, latency, or current production status of a Microsoft service.

## 9. Provider-contract evidence

`tests/test_azure_cloud.py` uses provider-like fake clients/containers to verify FleetPlane's interaction semantics without network/provider dependence.

The suite covers:

- same-partition Cosmos batch construction;
- optimistic ETag/CAS behavior;
- command idempotency documents;
- outbox lease behavior;
- IoT Hub desired-state transport semantics;
- direct-method structured ACK parsing;
- retryable/permanent provider failure classification;
- Event Hub/IoT Hub authenticated-device metadata extraction;
- Cosmos-style lifecycle/filter behavior;
- materialized global/fleet/site summary correctness;
- replay/late-event summary safety.

This evidence supports **semantic compatibility of the adapter implementation**. It does not support cloud throughput or availability claims.

## 10. Summary projection evidence

The Azure summary test compares:

```text
materialized summary
versus
exact cross-partition state calculation
```

for the same simulated state.

Then it replays all summary deltas and requires zero further effective summary updates. A separate test sends a newer device projection followed by an older one and requires the newer contribution to remain authoritative.

This proves the projector's replay/version semantics under the fake Cosmos transaction surface. It does not measure real change-feed lag.

## 11. What local/provider tests do not prove

The current repository makes no measured claim for:

- Azure regional availability;
- IoT Hub connection/message throughput;
- real Cosmos RU consumption;
- Cosmos change-feed latency;
- Azure Functions cold starts or horizontal scale-out;
- direct-method p50/p95/p99 latency;
- private networking behavior;
- real certificate/provisioning lifecycle;
- disaster recovery/RTO/RPO;
- safety certification;
- multi-million-device performance.

Those are Class C live-provider questions in [METHODOLOGY.md](METHODOLOGY.md).

## 12. How to publish a future live result

A future benchmark/qualification result should declare at minimum:

```text
commit/release
measurement date
Azure region
resource SKUs
Terraform/provider/SDK versions
fleet/device count
telemetry frequency/payload size
command/config workload
Cosmos throughput mode
Function concurrency settings
network topology
measurement duration
warm/cold treatment
raw output / aggregation method
```

Negative results should be retained rather than changing thresholds after measurement and publishing only the successful rerun.
