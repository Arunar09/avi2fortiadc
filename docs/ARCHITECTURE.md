# AVI → FortiADC Migration Tool — Architecture

## 1. System model

The framework treats migration as controlled translation of configuration state.

Avi REST API or normalized offline JSON
→ discovery/import
→ classifier and decision manifest
→ analysis/pattern engine
→ deterministic transformers
→ FortiADC target payload
→ dry-run/deployer
→ verification
→ parallel run/DNS/rollback
→ audit evidence

The web console reads and updates the same service/state model; it is not a second migration engine.

## 2. Source and import layer

### Avi client

core/avi_client.py provides the source API client. The current code is aligned to Avi API version 22.1.5. Actual controller compatibility must still be checked.

### Collectors

collectors/ contains typed collectors registered in registry.py.

The registry includes:

- virtual services and VIPs;
- pools and health monitors;
- SSL certificates/profiles;
- application/network/persistence profiles;
- HTTP/WAF/auth policies;
- DataScripts;
- SE groups;
- GSLB/global/GeoDB data;
- alert/cloud/tenant/VRF/network context;
- IP address groups;
- IPAM/DNS providers;
- external connections.

### Strict import

core/avi_import.py defines a normalized discovery contract. Raw Avi exports are not silently interpreted as normalized discovery. The import classifier separates pipeline, GSLB-related, context and noise data.

## 3. Decision layer

core/decision_manifest.py and the pipeline services provide phase gates and operator decisions.

Important decision classes include:

- import scope;
- mapping approvals;
- analysis triage;
- transform approvals;
- deploy approval;
- post-validation decision.

This is a governance layer, not merely UI state.

## 4. Analysis

The analyzer/resolver/intelligence services identify:

- dependencies;
- cross-tenant/global references;
- compatibility patterns;
- manual/blocked features;
- migration impact.

The system must preserve unsupported items rather than silently dropping them.

## 5. Transformation

transformers/ contains explicit source-to-target mappings.

registry.py is the registration authority for collectors and transformers.

The target payload is an intermediate representation of the intended FortiADC configuration. Exact API compatibility is target-release dependent.

## 6. Deployment

deployers/fortiadc_deployer.py performs controlled target mutation.

The repository defines dependency ordering and existence/update handling. A security regression test proves a representative create-then-update sequence.

This is deliberately weaker than claiming universal idempotency. Production qualification must test repeated deployment against the exact target release and an environment containing unrelated pre-existing configuration.

## 7. Event and audit model

core/events.py provides structured events and sanitization.

Sanitization covers recognized credential, token, authorization-header, cookie, certificate/private-key and IP forms. The tests include adversarial secret forms and sanitizer idempotency.

Logs are JSONL and can be chained/verified with the audit tooling.

## 8. State

State is filesystem-backed.

Representative artifacts:

- discovery/<env>.json
- fortiadc/<env>-config.json
- state/<env>-ledger.json
- state/<env>-governance.json
- state/rag_index.db
- logs/<env>-<phase>.jsonl

Decision manifests are used to preserve operator decisions.

## 9. RAG

core/rag/ provides local retrieval over the knowledge base. The repository documentation describes TF-IDF/BM25 lexical retrieval and associated ranking/augmentation logic.

RAG is advisory. It is not part of the deterministic transformation contract.

## 10. LLM gateway

core/llm_gateway.py and related advisory services are optional. Modes can route to local or approved gateway infrastructure.

External LLM use creates a separate data path. Sanitized context should be reviewed before transmission.

## 11. Web console

ui/app.py creates the Flask application. ui/routes.py contains protected routes. run_ui.py launches the console.

Security controls include CSRF protection, explicit production secret requirements, session controls, bootstrap-admin requirements and role-protected mutation routes.

## 12. Failure model

The architecture is not transactionally atomic across multiple target API calls.

Failures must be represented through events/state and handled by:

- phase gates;
- dry-run;
- retry/re-execution where appropriate;
- target verification;
- rollback procedures;
- audit evidence.

## 13. Qualification boundary

Repository architecture describes implementation. It does not establish:

- exact FortiADC firmware/API compatibility;
- Infoblox WAPI behavior in the production DNS topology;
- OpenStack/Contrail route/security equivalence;
- production tenant isolation;
- production rollback success;
- end-to-end migration correctness.

Those require Q1–Q5 evidence.
