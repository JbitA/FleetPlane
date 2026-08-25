# Security Policy

FleetPlane is a public engineering reference implementation. Security-sensitive behavior is documented in [docs/SECURITY.md](docs/SECURITY.md), including implemented trust boundaries and controls that would still be required for a production deployment.

## Reporting a security issue

Do not publish active credentials, private infrastructure details, or exploit material in a public issue.

If GitHub Private Vulnerability Reporting is enabled for the repository, use that channel for a vulnerability that would benefit from coordinated disclosure. Otherwise, contact the repository owner through the account's available private contact channel before publishing sensitive details.

Ordinary hardening suggestions that do not disclose sensitive exploit information can be raised through the normal issue process.

## Scope of the public reference implementation

The repository includes examples of:

- device lifecycle and generation enforcement;
- authenticated IoT Hub identity binding at ingress;
- local policy rejection of cloud intent;
- idempotent command handling;
- correlation/audit records;
- managed-identity-oriented Azure composition;
- CI security scanning through CodeQL and Dependabot.

The repository does **not** claim production qualification for:

- certificate issuance/rotation/revocation;
- Entra operator RBAC;
- private endpoints/network isolation;
- immutable audit/SIEM export;
- secret-management operations;
- penetration testing;
- SIL/ASIL or other functional-safety certification.

See [docs/SECURITY.md](docs/SECURITY.md) for the complete boundary.
