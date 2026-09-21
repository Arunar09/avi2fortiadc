# Unsupported, Manual and Blocked Features

## Decision semantics

| Decision | Meaning | Deployment implication |
|---|---|---|
| AUTO | Automatic mapping path exists | May proceed after normal gates |
| WARN | Mapping exists with material caveats | Operator review required |
| MANUAL | Tool cannot safely perform the migration | Human action required |
| BLOCKED | Migration cannot safely proceed until resolved | Deployment gate must stop |

The rule is visibility over silent loss.

## DataScripts

Avi DataScripts contain application-specific logic. The repository captures and analyzes them but does not claim universal deterministic translation into FortiADC.

Action: identify trigger and business behavior, determine the target-native replacement, optionally generate a sanitized advisory pack, manually implement and test, then record evidence.

## HSM/non-exportable certificates

The migration can identify certificate metadata and exportability constraints. Non-exportable private keys cannot be reconstructed by the tool.

Action: use the approved certificate reissue/import process, validate chain/SAN/expiry/TLS behavior, and record the manual action.

## Custom health monitors

A syntactically similar monitor may behave differently. Validate request/response semantics, timing, network reachability and failure behavior from the FortiADC path.

## WAF, policy and authentication behavior

Product-specific engines and references can make semantic equivalence unsafe to assume. Treat unsupported semantics as manual/target-specific until documented and tested.

## GSLB

GSLB has global, service, GeoDB and external DNS implications. Treat it as a separate workstream. Cross-site and cross-tenant dependencies must be resolved before DNS cutover.

## Manual-item record

For every manual item capture:

- source object/type/name;
- reason automatic mapping is unsafe or unavailable;
- proposed replacement;
- owner;
- target change reference;
- test result;
- approval;
- residual risk.

## Never do this

Do not silently omit an Avi object, convert BLOCKED to AUTO because a payload is accepted, assume syntactic validity means semantic equivalence, use unreviewed LLM output as production configuration, or declare a manual item complete without evidence.
