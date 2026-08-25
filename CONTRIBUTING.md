# Contributing to FleetPlane

FleetPlane is intentionally small enough to remain understandable. Contributions should strengthen an existing system invariant or add a clearly justified capability rather than increase service count for its own sake.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e '.[dev]'
```

## Before submitting a change

Run:

```bash
ruff check .
mypy src/fleetplane
pytest --cov=fleetplane --cov-branch --cov-report=term-missing
fleetplane showcase --devices 20 --restricted-devices 3
python -m build --wheel
```

Terraform changes should also pass:

```bash
cd infra/terraform
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
```

## Change discipline

For reliability/control-plane changes:

1. write down the invariant;
2. define the failure/concurrency condition that could violate it;
3. add a failing test or evidence criterion;
4. change the core/domain model;
5. make local/provider adapters conform;
6. document new limitations as well as capabilities.

## Provider adapters

Do not let a cloud adapter silently change the meaning of a port operation.

Examples:

- “IoT Hub accepted a twin update” is not “the device applied configuration.”
- “broker transport acknowledged publish” is not automatically “application state was persisted.”
- provider-contract fakes do not establish provider latency/availability.

## Domain boundaries

Avoid provider SDK types in `core/` and `domain/`.

Prefer:

```text
provider SDK → adapter → FleetPlane model/service
```

not:

```text
provider SDK → business logic everywhere
```

## Tests

Concurrency, duplicate delivery, ordering, lifecycle, and retry behavior are not optional edge cases in this repository. If a change touches those semantics, add an adversarial test.

The branch-coverage floor is 80%, but coverage percentage is not a substitute for meaningful invariant tests.

## Documentation claims

Before adding “safe,” “reliable,” “idempotent,” “scalable,” or “production-ready” to public documentation, identify the evidence class described in [docs/METHODOLOGY.md](docs/METHODOLOGY.md).

If it is not measured, say “designed for,” “implemented,” or “requires live qualification” rather than implying a proven result.
