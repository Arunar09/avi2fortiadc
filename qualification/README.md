# Migration Qualification Pack

This directory defines the evidence-driven qualification process for Avi -> FortiADC migration.

## Rules

1. Qualification is evidence-based. A unit or mock test is not real-device qualification.
2. Collection from production systems is read-only unless a test explicitly states otherwise.
3. Never collect passwords, API tokens, cookies, private keys, certificate private material, session headers, or full secret-bearing configuration.
4. Tenant identity must be preserved in evidence, but sensitive tenant data should be minimized or pseudonymized where practical.
5. Every result records source/target versions, environment, timestamp, test ID, action, expected result, observed result, and evidence reference.
6. A failed, skipped, unavailable, or not-tested case is not a PASS.
7. Unsupported/manual features must remain visible; they must never be silently omitted.

## Qualification stages

### Q0 — Repository correctness

- Existing unit tests pass.
- Security regression tests pass.
- Import/transform/deploy paths remain functional.
- No secrets appear in generated logs or test artifacts.

### Q1 — Avi 22.1.5 discovery

Read-only evidence collection covering:

- controller/API version
- tenants
- virtual services
- pools and members
- health monitors
- SSL certificates/profiles (metadata only; never private keys)
- application/network/persistence profiles
- HTTP policies
- DataScripts
- SE groups
- GSLB objects
- DNS/IPAM providers
- networks/VRFs
- dependencies and cross-tenant references

### Q2 — Environment dependency qualification

Document actual dependencies on:

- OpenStack
- Contrail
- Infoblox
- external DNS
- routing/security controls

### Q3 — FortiADC target qualification

Record the exact FortiADC release/API/VDOM model and validate supported target operations against that appliance.

### Q4 — Migration behavior

For a representative tenant:

1. discover
2. analyze
3. transform
4. review manual/blocked items
5. local validation
6. target API validation
7. dry-run
8. deploy
9. verify
10. repeat deployment
11. parallel-run validation
12. DNS cutover/rollback test

### Q5 — Production qualification

Production qualification requires explicit evidence for tenant isolation, idempotency, rollback, failure recovery, auditability, and preservation of unrelated target configuration.

## Evidence record

Use this structure for each test:

```text
TEST-ID:
DATE:
SOURCE:
SOURCE-VERSION:
TARGET:
TARGET-VERSION:
ENVIRONMENT:
TENANT/VDOM:
ACTION:
EXPECTED:
OBSERVED:
RESULT: PASS | FAIL | BLOCKED | NOT-TESTED
EVIDENCE:
OPERATOR:
NOTES:
```

## Current qualification boundary

The repository can establish code-level and mock-test evidence. It cannot establish real Avi/FortiADC/Infoblox/OpenStack/Contrail qualification without access to representative systems and evidence from those systems.
