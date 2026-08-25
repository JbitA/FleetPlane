# Use Cases and System Fit

FleetPlane is most useful around **software-defined physical assets**: devices that have meaningful local compute, persistent identity, software/configuration lifecycle, remote connectivity, and enough operational importance that fleet governance matters.

The examples below describe architectural fit. They are not claims of real-world FleetPlane deployments.

## 1. Predictive-maintenance edge gateways

### Edge responsibility

- high-rate vibration/acoustic/current acquisition;
- local feature extraction or ML inference;
- local alarm/safety behavior;
- local buffering during connectivity loss.

### FleetPlane responsibility

- authoritative gateway identity and hardware generation;
- software/model/configuration posture;
- connectivity/spool/sensor health;
- supervisory threshold/configuration changes;
- diagnostics commands;
- quarantine/disable/decommission lifecycle;
- fleet/site operational views.

### Separate data plane

Raw high-frequency waveform history belongs in a time-series/object/analytics system rather than the control aggregate.

## 2. Railway / road / bridge inspection systems

A mobile inspection node may combine vision, vibration, GNSS, environmental sensing, and edge inference.

FleetPlane can supervise:

- which inspection units are commissioned;
- which model/configuration each unit is using;
- whether telemetry is fresh;
- whether a device accumulated an offline backlog;
- which units rejected a configuration;
- whether replacement hardware is authorized;
- diagnostic and maintenance-state actions.

The inspection/control algorithm remains local.

## 3. Warehouse AMRs / AGVs

FleetPlane is a good fit **above** the robot-specific fleet manager.

Robot/OEM software should retain:

- localization;
- path planning;
- collision avoidance;
- traffic/mission scheduling;
- safety controller behavior.

FleetPlane can add cross-fleet concepts such as:

- enterprise device inventory;
- lifecycle/quarantine;
- software/model posture;
- desired configuration;
- operator/audit policy;
- cross-site operational state.

It should not attempt to become a universal robot traffic controller.

## 4. Industrial AI appliances

An OEM may ship Jetson, ARM Linux, x86, or industrial-PC appliances that perform vision/inspection/anomaly detection at customer sites.

FleetPlane can provide the otherwise-undifferentiated operational backend:

```text
provision → commission → observe → configure → diagnose → quarantine → retire
```

This is a particularly strong fit because the OEM's product differentiation may lie in the physical/AI function rather than in building a custom fleet backend.

## 5. Smart-camera / machine-vision fleets

Useful control-plane state includes:

- camera/edge-compute identity;
- software/model version;
- model channel;
- applied configuration revision;
- inference latency;
- sensor/camera health;
- connectivity/backlog;
- site/fleet assignment.

Raw video should generally remain outside the control-plane database.

## 6. Distributed energy / building assets

Examples include heat-pump controllers, ventilation systems, distributed batteries, solar inverters with local intelligence, or facility edge gateways.

FleetPlane can normalize the **software-defined operational layer** while traditional BMS/SCADA/PLC systems retain real-time process control.

## 7. Remote / intermittent-connectivity assets

Examples:

- mines;
- maritime systems;
- mobile inspection platforms;
- agriculture;
- remote infrastructure;
- environmental monitoring.

The key FleetPlane property is not “always connected.” It is the opposite: connectivity loss is expected, the edge continues locally, and telemetry/configuration reconcile later.

## 8. OEM installed-base operations

A manufacturer may have thousands of intelligent machines at customer sites.

FleetPlane can become the operational system of record for:

- physical generation;
- software/model/configuration posture;
- current operational health;
- diagnostics availability;
- lifecycle state;
- fleet/site segmentation;
- audit history.

This can support service organizations without putting ERP/EAM directly in the high-rate device telemetry path.

## 9. ERP / EAM enrichment

A higher-level integration could translate FleetPlane machine state into business-grade events such as:

```text
AssetConditionChanged
SoftwarePostureChanged
ConfigurationDriftDetected
DeviceQuarantined
MaintenanceEvidenceAvailable
```

An ERP/EAM system could then manage:

- maintenance notifications/work orders;
- technician assignment;
- spare parts;
- procurement;
- contracts/warranty;
- financial consequences.

FleetPlane would remain the machine-operational layer. The ERP/EAM system remains the business system of record.

This integration is a plausible use case, not part of the current repository implementation.

## 10. When FleetPlane is the wrong abstraction

### A few simple sensors

If the requirement is only:

```text
temperature → MQTT → dashboard
```

FleetPlane is excessive.

### Fixed hard real-time industrial control

Use PLC/DCS/SCADA/safety systems for deterministic process control. FleetPlane can supervise software-defined assets around them but should not replace that layer.

### OTA-only requirement

If the sole problem is safe embedded/Linux firmware updates, mature OTA products are likely a better fit. FleetPlane's value is broader desired posture, identity, lifecycle, policy, and operational state.

### Robot mission orchestration

If the primary requirement is task allocation, maps, traffic management, or route planning, use a robotics fleet-management/orchestration platform.

### Long-term analytics warehouse

Use a telemetry lake/time-series/analytics platform. FleetPlane retains only control-plane state and receipts required for its invariants.

## 11. Selection heuristic

FleetPlane becomes more appropriate as the answer to these questions becomes “yes”:

- Do devices continue evolving after shipment?
- Is physical access expensive?
- Is local autonomy required during cloud loss?
- Do software/model/configuration versions matter operationally?
- Are there multiple hardware generations?
- Do operators need quarantine/disable/decommission semantics?
- Can retries/duplicates cause harmful actions?
- Does the organization need an audit trail for remote control intent?
- Are there enough devices that manual SSH/spreadsheets no longer scale?

The asset count alone is not the deciding variable; **device value, operational criticality, software complexity, and service cost** matter just as much.
