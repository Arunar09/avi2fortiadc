# Bastion Evidence Collection Template

Run the commands appropriate to your environment and paste the sanitized output into the migration review. Do not paste credentials, tokens, cookies, private keys, or raw secret-bearing configuration.

## A. Avi / NSX Advanced Load Balancer

Collect read-only metadata:

- controller version
- tenant list and tenant UUIDs/IDs if safe
- object counts by tenant
- virtual services
- pools/members
- health monitors
- profiles
- SSL metadata
- HTTP policies
- DataScript names/references
- SE groups
- GSLB
- IPAM/DNS providers
- networks/VRFs
- dependency/reference relationships

For each collection command, capture:
- command
- timestamp
- exit code
- sanitized output

## B. OpenStack

Collect read-only:
- release/version
- projects/tenants relevant to migration
- networks/subnets
- VIP/network dependencies
- security-group references relevant to the load balancers
- provider/network relationships

Do not collect passwords, tokens, application credentials, or full secret-bearing config.

## C. Contrail

Collect read-only:
- version
- virtual networks
- routing/VRF relationships
- relevant load-balancer/network dependencies
- tenant/project relationships

## D. Infoblox

Collect only the information required to understand DNS/IPAM dependency:
- platform/version
- relevant DNS zones
- records involved in migration
- TTLs
- authoritative/forwarding relationships

Do not export credentials or unrelated DNS data.

## E. FortiADC

If available, collect:
- exact firmware/version
- VDOM model
- API availability/version
- existing target object inventory
- relevant network/route/VIP context

Do not export administrator credentials, API tokens, private keys, or unrelated configuration.

## F. Evidence packaging

Organize the resulting sanitized evidence as:

```text
evidence/
  00-environment/
  10-avi/
  20-openstack/
  30-contrail/
  40-infoblox/
  50-fortiadc/
  90-derived/
```

The evidence should be sufficient to build:
- tenant isolation matrix
- dependency graph
- feature compatibility matrix
- migration blockers
- manual migration list
- target mapping matrix
- qualification test plan

Do not treat missing output as proof that a feature does not exist. Mark it UNKNOWN / NOT-COLLECTED.
