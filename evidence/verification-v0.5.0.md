# FleetPlane v0.5.0 Local Verification Record

This record captures the local release-preparation evidence used for the repository documentation. It is not a live Azure qualification report.

## Environment

```text
FleetPlane: 0.5.0
Python:     3.13.5
Platform:   Linux 6.18.35 x86_64 / glibc 2.41
Date:       2026-08-24
```

The package declares Python `>=3.12`. GitHub CI is configured for Python 3.12; this local record was produced on the execution environment shown above.

## Automated Python verification

Command:

```bash
python -m pytest --cov=fleetplane --cov-branch --cov-report=term-missing -q
```

Observed result:

```text
43 tests passed
82.93% total branch-aware coverage
80% repository coverage gate satisfied
```

The reported aggregate includes Azure/Cosmos adapter code in the coverage source surface.

## Reference scenario

Command:

```bash
PYTHONPATH=src python -m fleetplane showcase \
  --devices 100 \
  --restricted-devices 5 \
  --evidence evidence/reference-scenario-v0.5.0.json
```

Observed controlled results:

| Observation | Result |
|---|---:|
| devices | 100 |
| duplicate second delivery | `duplicate` |
| induced sequence jump | `accepted_with_gap` |
| late older event | `accepted_out_of_order` |
| events spooled during offline interval | 60 |
| revision 1 applied | 95 |
| revision 1 rejected by local policy | 5 |
| stale revision replays rejected | 100 |
| restart while active | `rejected` |
| ping | `accepted` |
| final config convergence | 100/100 |
| acceptance assertions | 9/9 true |

Raw machine-readable record: [reference-scenario-v0.5.0.json](reference-scenario-v0.5.0.json).

## Package build

Command:

```bash
python -m pip wheel . --no-deps --no-build-isolation -w /tmp/fleetplane-wheel
```

Observed artifact:

```text
fleetplane-0.5.0-py3-none-any.whl
SHA-256: cea02c165b7faa0e35403e13070c1a273a398220af33dd23e9f6f492b2b9f292
```

## Documentation integrity

A local relative-link checker verified that all Markdown links between repository documents resolve to existing paths.

Version consistency was checked across:

```text
pyproject.toml
src/fleetplane/__init__.py
README.md
CHANGELOG.md
```

All report version `0.5.0`.

## Checks not executed in this local environment

The current runtime did not contain the `ruff`, `mypy`, `terraform`, or `docker` executables. The repository's GitHub workflows are configured to execute Ruff, strict mypy, Terraform formatting/validation, CodeQL, and the reference scenario in CI after publication.

Accordingly, this local record does not claim those external-tool checks were executed here.

## Evidence interpretation

This record supports statements about the behavior of the checked-out implementation under the local deterministic/provider-contract tests described in [../docs/METHODOLOGY.md](../docs/METHODOLOGY.md).

It does not support claims about:

- live Azure throughput/latency/RU consumption;
- Azure service availability;
- Function cold start/scale-out;
- production network/security qualification;
- functional-safety certification.
