# AVI → FortiADC Migration Tool — Documentation Index

This directory is the canonical documentation set. Root-level USER-GUIDE.md, ARCHITECTURE.md, and SECURITY.md are compatibility entry points and are not independent copies.

## Evidence labels

- IMPLEMENTED — directly supported by repository code/tests.
- PROCEDURAL — an operator workflow implemented by scripts/UI but requiring environment-specific validation.
- UNVERIFIED — depends on the actual Avi, FortiADC, Infoblox, OpenStack, Contrail, network, or DNS environment.

A successful repository test does not convert an UNVERIFIED integration claim into PASS.

## Start here

| Document | Purpose |
|---|---|
| 00-PRODUCT-OVERVIEW.md | Scope, goals, lifecycle, capabilities |
| USER-GUIDE.md | Installation and operator workflow |
| OPERATING-MODEL.md | Change control, tenant sequencing, approvals, evidence |
| ARCHITECTURE.md | Components, data flow, state and control gates |
| OBJECT-MAPPING-MATRIX.md | Avi object families and target handling |
| UNSUPPORTED-FEATURES.md | Manual/blocked feature handling |
| DATASCRIPT-MIGRATION.md | DataScript treatment and LLM advisory boundary |
| GSLB-MIGRATION.md | GSLB inventory and migration boundary |
| ENVIRONMENT-DEPENDENCIES.md | OpenStack, Contrail, Infoblox and DNS dependencies |
| SECURITY.md | Security controls and data handling |
| CLI-REFERENCE.md | CLI, wizard, UI and script entry points |
| WEB-CONSOLE.md | Operator-console behavior and security |
| RUNBOOK.md | Maintenance-window procedures |
| TROUBLESHOOTING.md | Failure diagnosis |
| QUALIFICATION.md | Evidence model and release gates |
| TECHNICAL-ARCHITECTURE.md | Operation-by-operation implementation notes |

## Qualification authority

qualification/README.md defines the rules. qualification/QUALIFICATION-MATRIX.md defines current requirement status. qualification/REPOSITORY-QUALIFICATION-LEDGER-2026-09-21.md records dated evidence.

## Required reading before production

1. Product overview.
2. Operating model.
3. User guide.
4. Object mapping and unsupported-feature matrix.
5. Environment dependencies.
6. Security.
7. Qualification matrix.
8. Runbook and rollback procedure.

## Target-release boundary

The repository contains implementation and mock/unit evidence, but the exact FortiADC release/API/VDOM model, Infoblox behavior, OpenStack/Contrail topology, and production tenant isolation are not established by repository tests alone.
