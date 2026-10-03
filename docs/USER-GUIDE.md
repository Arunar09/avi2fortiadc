# AVI → FortiADC Migration Tool — User Guide

This is the canonical operator guide. It describes the repository implementation and clearly separates implemented behavior from target-environment qualification.

## 1. Prerequisites

### Host

- Python 3.8+; Python 3.11+ is recommended.
- Linux, macOS or Windows/WSL2.
- Network access to Avi and FortiADC for live phases.
- Air-gapped operation is supported when dependencies are bundled.

### Avi

Use a read-only service account scoped to the tenant being migrated. The repository targets Avi API version 22.1.5; confirm the actual controller version before qualification.

### FortiADC

Use an account authorized for the target VDOM. REST API availability, API paths and object semantics must be validated against the exact FortiADC release.

### Infoblox

Only required for the DNS workflow when Infoblox is the authoritative change system. Validate WAPI version, DNS views, record permissions and rollback behavior.

## 2. Installation

    bash install.sh
    bash install.sh --check

For an internet-connected packaging host:

    bash install.sh --bundle

Transfer the resulting package to the air-gapped host and run the normal installer.

Do not place config.yaml into source control.

## 3. Interfaces

### Web console

    python3 run_ui.py

Default binding is 127.0.0.1:5000. The launcher supports host/port/debug options and corresponding environment variables.

The UI is an operator surface. A UI state or label is not itself production qualification.

### CLI

    python3 migrate.py --help

The checked-in migrate.py parser is the authoritative source for exact CLI options.

### Wizard

    python3 wizard.py

The wizard provides guided phase execution and resumable state.

## 4. Configuration

The minimum configuration contains:

- Avi controller, username and API version.
- FortiADC host, username and VDOM.
- One or more environment mappings.
- Optional LLM gateway settings.

Prefer the documented environment-variable password overrides where available. Protect config.yaml with restrictive filesystem permissions.

## 5. Migration lifecycle

### Phase 1 — Discover

Read Avi configuration through the source API.

Typical command:

    python3 migrate.py discover --env Tenant-Dev-B

Discovery is read-only with respect to Avi. The result is stored under discovery/.

Collected families include virtual services, VIPs, pools, health monitors, SSL objects, application/network/persistence profiles, policies, DataScripts, SE groups, GSLB data, cloud/tenant/network context and external connections.

### Phase 2 — Import when using offline source data

The importer accepts normalized discovery JSON and rejects raw Avi exports when the strict import contract is being used.

    python3 migrate.py import-avi-json --input normalized-discovery.json --env Tenant-Dev-B

The import classifier preserves pipeline, GSLB-related, context and noise categories. GSLB keys are deliberately classified before the generic context fallback.

### Phase 3 — Analyze

Analysis is local to the discovery snapshot and produces compatibility/risk information.

    python3 migrate.py analyse --input discovery/Tenant-Dev-B.json

Review:

- AUTO/WARN/MANUAL/BLOCKED results;
- dependency/impact information;
- risk patterns;
- complexity;
- manual resolution requirements.

### Phase 4 — Transform

Transformation uses explicit repository mappings.

    python3 migrate.py transform --input discovery/Tenant-Dev-B.json

The generated target configuration is stored under fortiadc/. Items that cannot be safely transformed remain visible as manual/unsupported decisions.

A deterministic transform should be reproducible for the same input and implementation/configuration; this does not mean the resulting target behavior is automatically equivalent.

### Phase 5 — Dry-run

Run target validation before any mutation.

    python3 migrate.py deploy --fortiadc-config fortiadc/Tenant-Dev-B-config.json --env Tenant-Dev-B --dry-run

Review target reachability, VDOM, conflicts, payload scope and approval evidence before execution.

### Phase 6 — Deploy

    python3 migrate.py deploy --fortiadc-config fortiadc/Tenant-Dev-B-config.json --env Tenant-Dev-B --execute

The deployer uses existence/update handling and a dependency-aware order. Repository tests demonstrate create-then-update behavior for a representative object, but complete idempotency across every target object and pre-existing configuration must be qualified on the exact FortiADC release.

### Phase 7 — Verify and parallel run

Use the repository's verification and operational checks after deployment.

    python3 migrate.py ops-check --env Tenant-Dev-B
    bash scripts/parallel-run-check.sh Tenant-Dev-B

Parallel-run duration is a change-management decision. Any stated minimum in the runbook is a planning recommendation, not repository qualification evidence.

### Phase 8 — DNS cutover

    bash scripts/dns-cutover.sh --env Tenant-Dev-B --dry-run
    bash scripts/dns-cutover.sh --env Tenant-Dev-B --execute

The script contains Infoblox-oriented workflow logic. Validate the exact WAPI version, DNS view, records and permissions before live execution.

### Phase 9 — Rollback

    bash scripts/rollback.sh --env Tenant-Dev-B --dry-run
    bash scripts/rollback.sh --env Tenant-Dev-B --execute

Rollback can disable FortiADC virtual servers, restore DNS from a backup when available and check Avi health. It is not a transactionally atomic reversal. Test the complete procedure before production.

## 6. Manual and blocked features

The tool must not silently discard unsupported configuration.

Common manual classes:

- DataScripts.
- HSM/non-exportable certificates.
- Product-specific WAF/policy/authentication behavior.
- Health monitors whose semantics differ.
- GSLB semantics without a verified target mapping.
- Infrastructure dependencies requiring OpenStack/Contrail/DNS changes.

See UNSUPPORTED-FEATURES.md, DATASCRIPT-MIGRATION.md and GSLB-MIGRATION.md.

## 7. Audit and evidence

Typical audit operations include:

    python3 migrate.py audit chain --log logs/Tenant-Dev-B-deploy.jsonl
    python3 migrate.py audit verify --log logs/Tenant-Dev-B-deploy.jsonl
    python3 migrate.py audit cef --log logs/Tenant-Dev-B-deploy.jsonl --output siem.cef

The audit chain is tamper-evident; it is not a replacement for external SIEM controls.

Retain discovery/analysis/decision/target/deployment/verification/cutover evidence according to the qualification plan.

## 8. LLM advisory

LLM features are optional.

The supported model is advisory:

sanitized source context → advisory analysis → human review → implementation/test

LLM output must not be treated as an automatic production deployment decision.

If an external gateway is configured, sanitized advisory material may leave the local environment. Review the sanitized pack and gateway policy before enabling it.

## 9. Offline testing

Run:

    python3 -m pytest tests/ -v

The repository also provides mock-service test material under tests/.

Unit/mock evidence demonstrates repository behavior. It does not establish live Avi/FortiADC/Infoblox/OpenStack/Contrail compatibility.

## 10. State and artifacts

Important locations:

- discovery/ — source discovery.
- reports/ — analysis and sanitized reports.
- fortiadc/ — generated target payloads.
- state/ — decision manifests, ledgers and governance state.
- logs/ — event and audit logs.
- qualification/ — qualification matrix and evidence.

## 11. Production gate

Do not treat a migration as production-qualified until the qualification matrix contains the required evidence for:

- exact source/target versions;
- tenant isolation;
- target API behavior;
- repeated deployment;
- unrelated-target preservation;
- failure recovery;
- rollback;
- DNS cutover/rollback;
- parallel run;
- audit integrity;
- security permissions.

The absence of live evidence is a qualification boundary, not a PASS.

## 12. Related documents

- 00-PRODUCT-OVERVIEW.md
- OPERATING-MODEL.md
- ARCHITECTURE.md
- OBJECT-MAPPING-MATRIX.md
- UNSUPPORTED-FEATURES.md
- DATASCRIPT-MIGRATION.md
- GSLB-MIGRATION.md
- ENVIRONMENT-DEPENDENCIES.md
- SECURITY.md
- CLI-REFERENCE.md
- WEB-CONSOLE.md
- RUNBOOK.md
- TROUBLESHOOTING.md
- QUALIFICATION.md
