# Security Model

FleetPlane organizes security around explicit trust boundaries and state authority. It does not claim that the current repository is a complete production security program.

## 1. Trust boundaries

```text
operator/API caller
        ↓
HTTP authentication/authorization boundary
        ↓
FleetPlane domain services
        ↓
control store + durable outbox
        ↓
IoT Hub service boundary
        ↓
authenticated device connection
        ↓
edge local policy + persistent state
```

The current strongest security property is architectural: **cloud intent is not equivalent to local permission**.

## 2. Implemented controls

### Input validation

- strict Pydantic contracts use forbidden extras where appropriate;
- request body size is bounded;
- telemetry timestamps have future/backlog validation;
- cloud mode does not expose a parallel public HTTP telemetry-ingest endpoint;
- malformed/permanent ingress failures are distinguished from unexpected infrastructure failures.

### Device identity

- devices must be provisioned before operation;
- identity includes `device_generation`;
- lifecycle state authorizes/denies operations;
- decommissioning is terminal in the application model;
- Azure telemetry requires IoT Hub authenticated connection identity to match payload `device_id`.

### Configuration/commands

- desired revisions are monotonic;
- stale desired state is rejected at the device;
- device policy can reject cloud intent;
- API command creation supports idempotency keys;
- device command execution uses a durable command journal;
- command TTL/timeout exists;
- late or wrong-device ACKs do not blindly transition state;
- restart-class actions can be blocked by local operating mode/lifecycle.

### Durable delivery

- command/configuration intent uses transactional outbox;
- workers use storage-backed leases rather than process-only locks;
- retryable/permanent provider failures are separated.

### Correlation/audit

- HTTP request correlation is validated/generated server-side;
- request correlation is persisted separately from business operation IDs;
- lifecycle/configuration/command transitions create audit records;
- audit can be queried by request/business correlation and target.

### Cloud identities

- Cosmos local-key authentication is disabled in Terraform;
- Function managed identity receives Cosmos data access;
- Function managed identity receives IoT Hub service data access;
- GitHub Azure planning uses OIDC federation instead of a stored Azure client secret.

### Container runtime

- non-root application user;
- read-only application filesystem in Compose;
- dropped Linux capabilities;
- `no-new-privileges`;
- loopback-only published ports for the local composition.

### Supply-chain/process controls

- CodeQL;
- Dependabot for Python/GitHub Actions/Terraform;
- Ruff, strict mypy and tests in CI;
- runtime container installs a built wheel rather than an editable package.

## 3. Current limitations

### Operator authentication/authorization

The API still has development-level admin/actor mechanics rather than authoritative enterprise identity.

Next target:

```text
validated Entra token
  ↓
principal + tenant
  ↓
role/site/fleet authorization
  ↓
audit actor derived server-side
```

### Built-in IoT events credential

The current minimal telemetry topology uses the IoT Hub built-in Event Hubs-compatible endpoint with a scoped SAS connection. A custom Event Hubs route can enable an identity-based ingestion path.

### Network isolation

The showcase Terraform currently allows public network access to Cosmos. Private endpoints/VNet integration/private DNS are not implemented.

### Audit immutability

Audit records are normal application data. They are not WORM/tamper-evident and are not exported to a SIEM.

### Function host storage

The Function App model uses a storage-account access key for host storage. Further production hardening would evaluate supported identity-based host-storage configuration and Key Vault for unavoidable secret material.

### Device trust depth

The repository does not include:

- production DPS/certificate enrollment;
- hardware-backed keys/TPM attestation;
- certificate rotation;
- hostile-root resistance;
- secure boot attestation.

## 4. Threats explicitly considered

| Threat | Current response |
|---|---|
| spoof payload device ID | bind payload identity to authenticated IoT Hub connection identity |
| unknown device creates itself | explicit provisioning/lifecycle gate |
| replacement hardware impersonates current unit | provisioned `device_generation` |
| replayed telemetry | generation/boot/sequence identity |
| old event rewinds current state | ordering classification + projection version/CAS |
| stale configuration replay | monotonic revision + device rejection |
| duplicated operator request | `Idempotency-Key` |
| duplicated device command | persistent `command_id` journal |
| database commit followed by network failure | transactional outbox |
| two workers dispatch the same item | durable lease ownership |
| unsafe cloud restart request | local device policy/lifecycle |
| disabled/decommissioned activity | lifecycle authorization |
| oversized HTTP request | maximum body middleware |
| audit actor/request loses traceability | request/business correlation persisted separately |
| duplicate/late summary event corrupts fleet counters | per-device summary cursor + projection version |

## 5. Non-goals / not claimed

The repository does not claim:

- functional-safety certification;
- penetration-test completion;
- DDoS/WAF/reverse-proxy completeness;
- hardware root of trust;
- fleet-scale certificate provisioning;
- confidential computing;
- tamper-proof audit storage;
- multi-region DR/security qualification;
- zero-trust network completion.

Security roadmap items are prioritized in [ROADMAP.md](ROADMAP.md).

## Microsoft IoT security ownership

FleetPlane v0.6 deliberately delegates horizontal identity infrastructure to Microsoft services:

- direct-cloud device bootstrap and assignment are DPS responsibilities;
- IoT Hub authenticates the connected device transport identity;
- FleetPlane binds that authenticated identity to the application payload before state mutation;
- Azure Device Registry/ARM provide the management-plane target for RBAC, policy, tags, and resource governance;
- Microsoft Entra is the planned operator identity authority;
- Azure IoT Operations/Arc own the industrial edge runtime boundary.

FleetPlane does not claim that its local simulator or provider fakes implement Microsoft PKI, Entra authorization, Arc security, or Azure network isolation. Those controls require a declared live Microsoft environment.
