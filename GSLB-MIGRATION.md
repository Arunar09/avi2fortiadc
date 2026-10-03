# GSLB Migration

## Scope

The repository models GSLB-related Avi data including global configuration, GSLB services and GeoDB-related data. Raw GSLB keys are deliberately grouped by the import classifier so they are not mistaken for unrelated context.

## Why GSLB is separate

GSLB can cross normal SLB boundaries through multiple sites, global/service objects, DNS behavior, health and site-selection rules, GeoDB data, tenant/global scope and external DNS providers.

## Required inventory

Capture:

- global GSLB configuration;
- GSLB services;
- sites and members;
- health monitors;
- domain/FQDN mappings;
- TTL and DNS update behavior;
- GeoDB dependencies;
- cross-tenant references;
- external DNS/IPAM provider;
- ownership and cutover authority.

## Migration decision

The repository does not assert that every Avi GSLB feature has a deterministic FortiADC equivalent. Treat unsupported semantics as MANUAL/UNVERIFIED until the exact target release is tested.

## Cutover controls

Before changing DNS:

1. Confirm dependent SLB environments are ready.
2. Confirm the target GSLB/DNS design with network and DNS owners.
3. Capture pre-cutover DNS state.
4. Execute a dry-run.
5. Record approval.
6. Change only authorized records.
7. Verify authoritative DNS and application behavior.
8. Preserve rollback state.

The qualification matrix contains separate DNS/GSLB-related tests. Unit tests cannot establish live DNS/GSLB qualification.
