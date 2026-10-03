# Environment Dependencies

The migration tool sits between multiple infrastructure control planes. Configuration translation alone is not enough to establish a successful migration.

## Avi / NSX Advanced Load Balancer

Record exact controller version, tenant, source API role, virtual services, pools, health monitors, certificates/exportability, GSLB, DataScripts, IPAM/DNS providers, networks/VRFs and runtime health.

The repository's Avi client is aligned to API version 22.1.5. The actual controller must still be checked during qualification.

## OpenStack

Record release, project/tenant mapping, networks/subnets, ports, floating/fixed IP behavior, availability zones, load-balancer/network ownership and port-security/allowed-address-pairs requirements.

Do not assume that Avi service-engine placement can be reproduced by a FortiADC VDOM mapping alone.

## Contrail

Record release, virtual networks, routing instances/VRFs, service-chain dependencies, route advertisements, security policy and VIP/member reachability from the FortiADC network.

The repository can collect network/VRF context but cannot prove route equivalence without live evidence.

## Infoblox / DNS

Record release/WAPI version, DNS views, authoritative zones, A/AAAA/CNAME dependencies, TTL, RBAC scope, record ownership and rollback backup.

The DNS scripts contain WAPI examples. Exact endpoints, permissions and behavior must be validated against the deployed Infoblox release.

## External security controls

Validate firewall rules, security groups, ACLs, routing, NAT, TLS inspection, monitoring, logging/SIEM and certificate trust.

## Evidence format

For each dependency capture:

system → object/reference → expected behavior → observed behavior → evidence

Documentation is not a substitute for observed evidence.
