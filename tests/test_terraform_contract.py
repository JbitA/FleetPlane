from __future__ import annotations

import re
from pathlib import Path


def test_terraform_references_are_declared_and_deployment_defaults_off():
    root = Path(__file__).parents[1] / "infra" / "terraform"
    text = "\n".join(path.read_text() for path in sorted(root.glob("*.tf")))
    declared_vars = set(re.findall(r'variable\s+"([^"]+)"', text))
    referenced_vars = set(re.findall(r"var\.([A-Za-z0-9_]+)", text))
    assert referenced_vars <= declared_vars

    declared_locals = set(re.findall(r"^\s{2}([A-Za-z0-9_]+)\s*=", (root / "locals.tf").read_text(), re.M))
    referenced_locals = set(re.findall(r"local\.([A-Za-z0-9_]+)", text))
    assert referenced_locals <= declared_locals

    variables = (root / "variables.tf").read_text()
    deployment = re.search(
        r'variable\s+"deployment_enabled"\s*\{(?P<body>.*?)\n\}',
        variables,
        re.S,
    )
    assert deployment is not None
    assert re.search(r"default\s*=\s*false", deployment.group("body"))
    assert 'partition_key_paths   = ["/device_id"]' in text
    assert 'name                   = "fleetplane-control"' in text
    assert 'resource "azurerm_iothub_dps" "main"' in text
    assert 'Microsoft.DeviceRegistry/namespaces@2026-04-01' in text
    assert 'FLEETPLANE_DEVICE_REGISTRY_NAMESPACE' in text
    assert 'FLEETPLANE_DPS_ID_SCOPE' in text


def test_project_version_is_consistent():
    root = Path(__file__).parents[1]
    pyproject = (root / "pyproject.toml").read_text()
    init = (root / "src" / "fleetplane" / "__init__.py").read_text()
    project_version = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
    package_version = re.search(r'__version__\s*=\s*"([^"]+)"', init)
    assert project_version is not None and package_version is not None
    assert project_version.group(1) == package_version.group(1)
