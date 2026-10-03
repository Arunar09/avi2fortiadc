# Qualification

## Authority

Qualification status is governed by:

1. qualification/QUALIFICATION-MATRIX.md
2. qualification/REPOSITORY-QUALIFICATION-LEDGER-2026-09-21.md
3. completed CI/test evidence
4. real-system evidence for platform-specific claims

This document explains the method; it does not change matrix status.

## Stages

### Q0 — Repository correctness

Compile, unit, security regression, mock and documentation-consistency checks.

### Q1 — Avi 22.1.5

Read-only collection from a representative controller/tenant.

### Q2 — Environment dependencies

OpenStack, Contrail, Infoblox, routing and security integration.

### Q3 — FortiADC

Exact firmware/API/VDOM model and supported target operations.

### Q4 — Migration behavior

Representative tenant:

discover → analyze → transform → review → dry-run → deploy → verify → repeat → parallel-run → DNS cutover → rollback

### Q5 — Production

Tenant isolation, idempotency, failure recovery, auditability, unrelated-target preservation and change governance.

## Evidence rule

PASS means evidence demonstrates the requirement for the tested version/environment.

Unit test, mock API, vendor documentation, inference and live qualification are different evidence classes. The matrix must retain NOT-TESTED, UNKNOWN or BLOCKED where evidence is missing.

## Minimum evidence record

Test ID, source version, target version, environment/tenant/VDOM, date/time, action, expected result, observed result, evidence reference, operator and notes.

## Current boundary

The current repository evidence establishes code-level behavior and mock/unit results. It does not establish live Avi/FortiADC/Infoblox/OpenStack/Contrail interoperability or production migration safety.
