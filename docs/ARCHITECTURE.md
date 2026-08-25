# Architecture

FleetPlane v0.6 is a **Microsoft IoT-native application architecture**. Microsoft services provide the managed device/asset substrate; FleetPlane provides the intelligent-machine operational semantics. See [MICROSOFT_IOT_PLATFORM.md](MICROSOFT_IOT_PLATFORM.md) for the service-by-service mapping.

The two production connectivity patterns are:

```text
Direct cloud:      device → DPS → IoT Hub → Functions → FleetPlane
Industrial edge:   OPC UA/MQTT → Azure IoT Operations → Device Registry → FleetPlane
```

Azure Device Registry is the common management-plane representation. FleetPlane's correctness-critical application state remains in a per-device Cosmos aggregate so preview management-plane features are not part of the transaction boundary.

# FleetPlane Architecture

## 1. Architectural objective

FleetPlane separates the **local operational authority** of an autonomous/industrial edge system from the **supervisory fleet-control authority** of the cloud.

The cloud owns:

- device identity and lifecycle;
- current operational state projection;
- desired configuration;
- supervisory commands;
- durable delivery intent;
- fleet/site summaries;
- audit/correlation state.

The edge owns:

- sensing and local inference;
- real-time control/safety behavior;
- the ability to continue when cloud connectivity disappears;
- local telemetry persistence;
- local policy for configuration/commands;
- command replay protection.

The cloud is deliberately not the hard real-time loop.

## 2. Dependency direction

```text
HTTP / Azure Functions / simulator / CLI
                 ↓
             core services
                 ↓
               ports
          ↙               ↘
    local adapters     Azure adapters
```

`domain/` and `core/` do not import Azure SDK or SQLite implementation types. Provider-specific construction is isolated in `runtime.py` and adapter modules.

This is not done merely for “clean architecture.” It allows the same state-transition semantics to be exercised locally and against Azure-oriented provider contracts.

## 3. Device aggregate

The logical control aggregate is keyed by `device_id` and includes authoritative identity/scope:

```text
site_id
fleet_id
lifecycle
device_generation
projection_version
```

plus current operational projection:

```text
boot_id
last_sequence
telemetry timestamps
health inputs
health state
desired configuration revision
reported/applied configuration revision
software/model information
```

Azure maps the aggregate to one Cosmos logical partition per `/device_id` so same-device business transitions can use one transactional batch.

## 4. Identity hierarchy

FleetPlane currently models:

```text
site
  └── fleet
       └── logical device_id
             └── authorized device_generation
                   └── boot_id
                         └── sequence
```

These levels intentionally answer different questions:

- `site_id`: where/organizationally which site the asset belongs to;
- `fleet_id`: which operational group owns it;
- `device_id`: stable logical identity;
- `device_generation`: authorized physical generation under that identity;
- `boot_id`: one boot session of the same physical generation;
- `sequence`: event ordering inside that boot session.

## 5. Lifecycle state machine

`DeviceLifecycleService` owns provisioning and lifecycle transitions. Decommissioning is terminal.

The lifecycle state is an authorization input, not display-only metadata.

A lifecycle update uses optimistic concurrency and is written with its audit event.

```text
PROVISIONED → ACTIVE / QUARANTINED / DISABLED / DECOMMISSIONED
ACTIVE      → QUARANTINED / DISABLED / DECOMMISSIONED
QUARANTINED → ACTIVE / DISABLED / DECOMMISSIONED
DISABLED    → ACTIVE / QUARANTINED / DECOMMISSIONED
DECOMMISSIONED → terminal
```

## 6. Telemetry process

```text
device sample
   ↓
local transaction:
  allocate sequence
  + persist event to bounded spool
   ↓
transport
   ↓
Azure: authenticated connection device identity extraction
   ↓
strict wire/domain validation
   ↓
provisioned identity + generation + lifecycle gate
   ↓
read current device projection/version
   ↓
classify duplicate/gap/out-of-order/current boot
   ↓
transaction:
  telemetry receipt
  + device projection version N+1
  + summary delta (Cosmos path)
   ↓
accepted / duplicate / conflict retry / rejected
```

### Telemetry identity invariant

The durable ordering identity is:

```text
(device_id, device_generation, boot_id, sequence)
```

`event_id` remains useful for correlation but is not the only durability identity.

### Projection invariant

A later-finishing worker must not be able to overwrite a newer projection with a stale snapshot. `projection_version`/CAS semantics force conflicting workers to re-read/re-project.

## 7. Desired configuration process

```text
operator PATCH
     ↓
request correlation context
     ↓
lifecycle authorization
     ↓
read desired revision + device projection
     ↓
build revision N+1
     ↓
transaction:
  desired config
  + device desired_revision
  + audit
  + outbox
  + summary delta if projection contribution changed
     ↓
return durable result
     ↓
outbox dispatcher
     ↓
IoT Hub twin desired properties / in-memory device
     ↓
device local policy
     ↓
atomic local config + revision persistence
     ↓
ConfigAck
     ↓
transaction:
  ack
  + reported/applied state
  + audit
  + summary delta
```

A transport-level twin update is **not** a device-level configuration ACK.

## 8. Command process

```text
operator POST + optional Idempotency-Key
     ↓
request correlation context
     ↓
lifecycle authorization
     ↓
transaction:
  command(QUEUED)
  + audit
  + outbox
  + idempotency marker
     ↓
202 Accepted
     ↓
outbox worker lease
     ↓
transport direct method
     ↓
device persistent command-journal lookup
     ↓
local policy
     ↓
persist device result
     ↓
CommandAck
     ↓
conditional command transition
```

A late ACK cannot resurrect a terminal timed-out command. A wrong-device ACK is not allowed to transition the command.

## 9. Transactional outbox

The outbox exists because database state and a network call cannot be one atomic transaction.

```text
pending → leased → done
             │
             ├── retryable failure → pending at later available_at
             └── permanent failure → failed
```

Leases use:

```text
lease_owner
lease_until
attempts
```

A completion/retry transition must be performed by the current owner. This coordinates horizontally scaled workers through persistence rather than process-local locks.

## 10. Correlation and audit

FleetPlane distinguishes:

```text
request_correlation_id
```

from durable business identifiers such as:

```text
command_id
config:<device>:<revision>
outbox_id
```

An HTTP request can therefore be traced into durable audit/outbox records even after the original process/request ends. Outbox dispatch re-enters the originating correlation context for structured operation logs.

This is not yet a complete OpenTelemetry trace, but it establishes the propagation contract.

## 11. Local persistence

The local control store uses SQLite with:

- WAL mode;
- `BEGIN IMMEDIATE` write transactions;
- optimistic `projection_version` checks;
- additive schema migration metadata;
- relational indexes for keyset pagination and outbox claims;
- a transactionally maintained global fleet summary.

Each simulated edge device uses a separate SQLite database containing:

- boot/generation/sequence metadata;
- applied configuration;
- bounded telemetry spool;
- persistent command journal.

The local runtime is a semantic reference and reproducibility tool. It is not a proxy for Cosmos performance.

## 12. Azure persistence

The Cosmos control container uses:

```text
partition key: /device_id
```

and heterogeneous document types such as:

```text
device_state
desired_config
telemetry_receipt
config_ack
command
command_ack
command_idempotency
audit
outbox
summary_delta
```

State-changing operations use `execute_item_batch()` and ETag conditions where concurrency matters.

This maps the domain's same-device atomicity requirement onto Cosmos's same-logical-partition transactional boundary.

## 13. Materialized summary architecture

Global/site/fleet summaries do not belong in the per-device transaction partition, so FleetPlane uses an eventually consistent projection.

### Write side

Every relevant device-state transition creates a `summary_delta` inside the same `/device_id` transaction as the state mutation.

This avoids:

```text
device state committed
but
projection event lost
```

### Projection side

```text
control container change feed
           ↓
Azure Function / SummaryProjector
           ↓
summary container (/scope_id)
   ├── summary
   └── cursor:<device_id>
```

Each cursor stores:

```text
highest reflected projection_version
current contribution of that device to this scope
```

When a new delta arrives, the projector subtracts the previous stored contribution and adds the new contribution in the scope partition transaction.

This makes duplicate/late delivery safe:

```text
same version replay → no-op
older version        → no-op
newer version        → replaces previous device contribution
```

Scopes currently include:

```text
global
fleet:<fleet_id>
site:<site_id>
```

An exact cross-partition calculation remains as a backfill/validation fallback.

## 14. Read model

- device/command/audit lists use keyset cursors rather than offsets;
- the local global summary is materialized transactionally;
- Azure reads the materialized global summary when available;
- site/fleet materialized summaries are produced by the same projection store;
- reconciliation operates in bounded pages rather than being triggered implicitly by dashboard reads.

## 15. Runtime composition

### Local

```text
FastAPI / CLI
  ├── SQLiteFleetStore
  ├── InMemoryDeviceGateway
  └── FleetSimulator
```

### Azure

```text
Azure Functions / FastAPI
  ├── CosmosFleetStore
  │     └── CosmosSummaryStore
  └── AzureIoTHubGateway
```

The composition root selects adapters using `FLEETPLANE_MODE`.

## 16. Failure assumptions

FleetPlane explicitly assumes:

- messages duplicate;
- telemetry reorders;
- sequence gaps happen;
- devices disappear for long intervals;
- API callers retry;
- independent workers race;
- processes fail after committing state;
- provider calls fail transiently/permanently;
- outbox workers overlap;
- stale commands/configurations reappear;
- hardware identity changes independently of logical asset identity;
- change-feed/event projection delivery can replay or arrive late.

The test suite is organized around these assumptions.

## 17. Architecture evidence boundary

The architecture is considered implemented when its **state-transition semantics** are exercised locally/provider-contract tests.

It is not considered performance-qualified until the live-provider measurements listed in [METHODOLOGY.md](METHODOLOGY.md) are published.
