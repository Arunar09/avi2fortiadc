# Operating Model

## Migration unit

The recommended unit of work is an environment/tenant mapping:

Avi tenant → migration environment → FortiADC VDOM

The mapping must be reviewed before deployment. Do not infer that all Avi tenants can be migrated simultaneously.

## Tenant sequence

1. Select one environment.
2. Confirm the Avi tenant.
3. Confirm the target VDOM.
4. Discover read-only.
5. Review all object families and cross-references.
6. Resolve MANUAL/BLOCKED items.
7. Transform.
8. Dry-run against the intended target.
9. Obtain change/approval evidence.
10. Deploy.
11. Verify.
12. Run in parallel.
13. Execute DNS cutover only after the cutover gate is satisfied.
14. Keep the Avi path available until the approved rollback hold expires.

The tool does not grant tenant authorization; organizational approval remains external to the software.

## Separation of duties

The operator executing a live deployment should be distinct from the approval decision where organizational governance requires it. The console contains role/approval gates, but the actual organizational authorization must be validated in the deployment environment.

## Evidence package

Retain:

- source Avi version and tenant;
- target FortiADC version/API/VDOM;
- OpenStack/Contrail and DNS dependencies;
- discovery snapshot or approved sanitized equivalent;
- analysis report;
- resolved decision manifest;
- generated target configuration;
- dry-run result;
- deployment/audit logs;
- post-deployment verification;
- repeated-run evidence where required;
- parallel-run evidence;
- DNS cutover and rollback evidence;
- manual-action records.

Never place passwords, private keys, cookies, session headers, or API tokens into the evidence package.

## Stop conditions

Stop before target mutation when import scope is unresolved, a required dependency is missing, a BLOCKED item remains unresolved, target API assumptions are unverified, dry-run reports a blocking conflict, approval evidence is missing, or required evidence cannot be captured.

## Rollback philosophy

Rollback is a controlled procedure, not a claim of transactional atomicity. The current scripts can disable FortiADC virtual servers, restore DNS from a backup when available, and perform health checks. Exercise them in the target environment before the first production cutover.

## LLM boundary

LLM use is advisory only. It may help interpret manual items or draft DataScript translations. It must not authorize deployment or replace deterministic mapping logic.
