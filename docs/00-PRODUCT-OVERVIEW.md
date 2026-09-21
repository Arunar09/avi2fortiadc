# Product Overview

## Purpose

The AVI → FortiADC Migration Tool converts Avi/NSX Advanced Load Balancer configuration into a structured FortiADC target representation and provides controlled deployment, validation, audit, and operator workflows.

The design is translation-first:

Avi state → normalized discovery → analysis/decision → deterministic transform → target payload → controlled deployment → verification

The tool is not a generic configuration copier. Features that cannot be represented safely are retained as MANUAL, WARN, or BLOCKED decisions rather than silently discarded.

## Lifecycle

1. Discover — read Avi configuration and runtime information.
2. Import — accept strict normalized discovery JSON when live Avi access is unavailable.
3. Analyze — build dependency/impact information and classify migration risk.
4. Review — resolve import scope, mapping, analysis and manual decisions.
5. Transform — generate deterministic FortiADC payloads from explicit mappings.
6. Dry-run — validate target reachability/conflicts without applying changes.
7. Deploy — create/update supported target objects in dependency order.
8. Verify — compare expected and observed target state and health.
9. Parallel run — validate target behavior while Avi remains available.
10. DNS cutover — change traffic direction through the approved DNS workflow.
11. Rollback — disable target virtual servers and restore source DNS when required.
12. Audit/learn — preserve evidence and optionally index sanitized experience.

## Deterministic surface

- Discovery collection order and normalized schema.
- Raw-key classification and import-scope decisions.
- Object-to-transformer registry.
- Mapping tables and transformer logic.
- Sanitization rules.
- Audit-chain generation and verification.

## Not automatically guaranteed

- Compatibility with an arbitrary FortiADC release.
- Semantic equivalence between Avi and FortiADC features with different models.
- Exportability of HSM/non-exportable certificate material.
- Correct replacement of arbitrary Avi DataScripts.
- DNS propagation time.
- OpenStack/Contrail network equivalence.
- Production tenant isolation.
- Full rollback atomicity.
- Universal end-to-end idempotency.

Those items require explicit qualification evidence.

## Safety model

Avi is treated as read-only. The decision layer requires operator review. FortiADC mutations require explicit execution mode. Unsupported objects remain visible. Events are sanitized before persistence and logs use a SHA-256 chain.

## Production boundary

Repository tests establish code-level behavior only. Live Avi/FortiADC/Infoblox/OpenStack/Contrail interoperability, production cutover and rollback must be qualified separately.
