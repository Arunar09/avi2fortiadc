# Object Mapping Matrix

This matrix describes the repository's current source families and target handling. It does not invent FortiADC field/API equivalence where the repository does not establish it.

| Avi family | Repository handling | Target handling | Boundary |
|---|---|---|---|
| VirtualService | Collected/normalized | VirtualServerTransformer | Target-release validation required |
| VsVip | Collected | Used with virtual-service transformation | Target validation |
| Pool | Collected | PoolTransformer → real-server pools | Target validation |
| HealthMonitor | Collected | HealthCheckTransformer | Monitor semantics require validation |
| SSLKeyAndCertificate | Collected with sensitive-material controls | SSLCertTransformer | HSM/non-exportable material is manual |
| SSLProfile | Collected | Profile transformation | Cipher/TLS equivalence requires validation |
| ApplicationProfile | Collected | ApplicationProfileTransformer | Target validation |
| NetworkProfile | Collected | NetworkProfileTransformer | Target validation |
| PersistenceProfile | Collected | PersistenceProfileTransformer | Target validation |
| HTTPPolicySet | Collected/analyzed | No universal semantic mapping claimed | Manual/feature-specific |
| WafPolicy | Collected/analyzed | No universal semantic mapping claimed | Manual/target-specific |
| AuthProfile | Collected | No universal semantic mapping claimed | Manual/target-specific |
| VSDataScriptSet | Collected | No direct deterministic translation | Manual |
| ServiceEngineGroup | Collected | Informational/context | No direct target mapping claimed |
| GSLB | Global/service/GeoDB data collected | Separate migration workflow | Manual/Unverified |
| AlertConfig | Collected | Context unless explicitly mapped | Unverified |
| Cloud/Tenant/VRF/Network | Collected | Environment/dependency context | Unverified |
| IP address groups | Collected | Context/dependency | Unverified |
| IPAM/DNS providers | Collected | External dependency | Unverified |
| Connections | Collected | Dependency evidence | Unverified |

## Interpretation

IMPLEMENTED means repository code exists for collection/transform handling; it does not mean a live appliance has been qualified.

MANUAL means human work is required and must remain visible.

UNVERIFIED means evidence is insufficient to assert target behavior.

Never delete an unsupported source object merely because no target mapping exists.

## Deployment order

The deployer defines a dependency-aware order. Read deployers/fortiadc_deployer.py as the implementation authority for the current sequence. Exact target API paths remain release-dependent.

The security regression suite exercises repeated create/update behavior for a representative real-server object. That is evidence for that test case, not universal target idempotency.
