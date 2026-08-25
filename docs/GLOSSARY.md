# Glossary

## Control plane

The supervisory software layer that manages identity, desired state, commands, health, and audit for a fleet. It does not execute the hard real-time physical control loop.

## Edge device

A deployed compute system attached to or embedded in a physical asset. In FleetPlane it retains local operational authority and can continue when the cloud is unavailable.

## Desired state

The configuration the cloud wants the device to run.

## Reported/applied state

The configuration the device confirms it has actually applied. Desired and applied state may temporarily differ.

## Convergence

The condition in which the device's reported/applied configuration revision matches the cloud's desired revision.

## Device generation

The currently authorized physical hardware generation under a stable logical `device_id`. It separates hardware replacement/factory reset from an ordinary reboot.

## Boot ID

Identifier for one boot session of a particular device generation.

## Sequence

Monotonic event order inside a boot session.

## Projection

A current-state representation derived from events/observations. FleetPlane's device state is a projection of telemetry and control transitions rather than an append-only raw telemetry history.

## Projection version

Optimistic concurrency version attached to current device state. It prevents a stale concurrent worker from overwriting a newer projection.

## Transactional outbox

A pattern where business state and an outbound work record are committed atomically. Network delivery happens afterward from the durable work item.

## Lease

A time-bounded ownership claim on an outbox item. It coordinates independent workers through persistence rather than process-local locking.

## Idempotency

The property that repeating the same logical operation does not create an unintended additional effect.

FleetPlane uses distinct idempotency boundaries for telemetry identity, API command creation, device command execution, and summary projection.

## Materialized summary

A stored fleet/site aggregate maintained as device state changes. Reads consume the aggregate rather than scanning/reconciling every device.

## Summary delta

A document emitted alongside a per-device state transition that describes the previous and new summary contribution for that device.

## Local autonomy

The architectural rule that safety/real-time operation remains on the device even if FleetPlane or Azure is unavailable.

## Provider-contract test

A deterministic test against an SDK/service-shaped fake that verifies FleetPlane's adapter semantics. It is not a benchmark or availability test of the real provider.

## Live qualification

A declared experiment against actual cloud services with documented region, SKUs, workload, versions, measurement window, and results.
