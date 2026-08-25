# FleetPlane v0.6.0 Local Verification Record

## Release identity

```text
FleetPlane: 0.6.0
Architecture: Microsoft Azure IoT-native
```

## Executed local verification

The GitHub-presentation-ready v0.6.0 release was checked with:

```bash
PYTHONPATH=src pytest -q
PYTHONPATH=src pytest --cov=fleetplane --cov-branch --cov-report=term-missing -q
PYTHONPATH=src python -m fleetplane showcase \
  --devices 100 \
  --restricted-devices 5 \
  --evidence evidence/reference-scenario-v0.6.0.json
python -m pip wheel . --no-deps --no-build-isolation -w /mnt/data
```

Observed result:

```text
47 tests passed
82.90% branch-aware coverage
100-device reference scenario: 9/9 assertions true
wheel: fleetplane-0.6.0-py3-none-any.whl
wheel SHA-256: a46efd9545856c32983548dc028b9204b21c0ec7f2851e8a5df48320d2a82373
```

## Reference scenario observations

```text
registered devices                       100
offline devices during disturbance       20
persistently spooled telemetry events    60
revision-1 applied                       95
revision-1 policy rejected                5
revision-2 converged                    100
stale revision replays rejected         100
active-device restart              rejected
diagnostic ping                    accepted
```

The raw self-describing evidence record is [reference-scenario-v0.6.0.json](reference-scenario-v0.6.0.json).

## Microsoft IoT platform contract evidence

The v0.6 suite additionally verifies, without Azure network access:

```text
DeviceState → Azure Device Registry resource projection
DeviceState → DPS provisioning intent
DeviceState → Azure IoT Operations asset intent
Azure Device Registry create/replace payload construction
Microsoft platform inspection API
Terraform structural presence of DPS + Device Registry namespace
```

This is semantic/contract evidence only. No live Microsoft service performance claim is derived from it.

## Environment limitations

The execution environment used for this record does not include the `ruff`, `mypy`, or `terraform` executables. Their GitHub workflows/configuration remain in the repository, but this local record does not claim those external tools were executed here.

The ordinary isolated wheel build path attempted dependency resolution and could not reach the package index. The wheel was therefore built with the already-installed build backend using `--no-build-isolation`; the wheel build itself succeeded.


## Repository presentation checks

The final public-repository pass additionally verified:

```text
README/package/__init__ version agreement: 0.6.0
internal Markdown links: 0 broken
Azure/Cosmos adapters: included in coverage
reference experiment: refreshed after presentation changes
package wheel: rebuilt successfully
```

`ruff` and `mypy` were not installed in this execution environment; their configuration and GitHub CI gates are committed but are not represented as locally executed evidence in this record.
