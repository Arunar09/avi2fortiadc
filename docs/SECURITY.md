# AVI → FortiADC Migration Tool — Security

## Security objective

Provide a controlled, auditable migration path without requiring cloud services or LLMs for the core migration pipeline.

## 1. Data protection

Configuration, discovery snapshots, reports, state and logs are stored locally unless an operator explicitly enables an external integration.

config.yaml must not be committed and should use restrictive filesystem permissions.

The core pipeline does not require automatic cloud upload. If an optional external LLM gateway/proxy is configured, sanitized advisory material may leave the local environment. That is a separate data path and must be reviewed.

## 2. Sanitization

core/events.py sanitizes recognized:

- passwords and secret-like fields;
- API keys/tokens;
- Authorization and Proxy-Authorization headers;
- cookies;
- credential-bearing URLs;
- JWT-like values;
- private-key/certificate material;
- IPv4 and IPv6 addresses.

The security regression suite checks representative adversarial forms and sanitizer idempotency.

Sanitization is pattern-based. Operators must review a sanitized pack before external sharing.

## 3. Authentication

### Avi

Use a read-only service account scoped to the migration tenant. The application does not need Avi write privileges for discovery.

### FortiADC

Use an account authorized for the intended target VDOM. Exact permission requirements must be validated against the target release.

### Operator console

The Flask application requires an explicit production secret. First-run bootstrap requires an explicit bootstrap password. Sensitive routes are role-protected.

## 4. Authorization

The console protects high-impact operations including import commit, AI indexing/experience save, VDOM/network strategy changes, existing target configuration access and validation pre-flight.

Authorization is enforced at the route layer and should be supplemented by target-system RBAC.

## 5. Target failure behavior

FortiADC existence checks fail closed for non-404 HTTP errors. Only a confirmed 404 is treated as object absence.

Deployment errors avoid logging complete request payloads and record payload-key information instead.

## 6. Governance

The deployment workflow uses dry-run and explicit execution modes. Approval/change-control state is represented in the decision/governance model.

A local audit hash chain detects tampering of chained log records. It does not replace external immutable storage or SIEM controls.

## 7. Air-gap boundary

Air-gapped execution is supported for the core pipeline when dependencies are bundled and external integrations are disabled.

The optional LLM gateway can intentionally create an external path. Disable it when the environment requires strict air-gap operation.

## 8. Qualification boundary

Repository security tests demonstrate code-level controls. They do not prove:

- production RBAC correctness;
- tenant A/B isolation on live appliances;
- network segmentation;
- external SIEM retention;
- target appliance hardening;
- absence of every possible secret pattern.

Those require environment-specific security qualification.
