# Repository Qualification Ledger — 2026-09-21

## Operating rule

Each work item follows: WORK → COMMIT → EXECUTE AVAILABLE TESTS → CAPTURE ACTUAL RESULT → REVIEW → RATIFY OR FIX.
A work item is not ratified when executable evidence is absent or when any required test fails.

## Q0-FIX-001 — import/test contract alignment

### Commits
- 3078e04a20b8bc92eea4da10ba31f625a0b9e43c — aligned tests with the current strict import, state-ledger path, and paginated activity contracts.
- 7c0e33d9285ec5d8767ea107e82e2e229c463dde — partial GSLB classification correction.
- 3bb666db0e8af104f353ea32279d3bf208cb6943 — completed GSLB classifier group definition and classification.
- e2fe107652b7c0ea38ba7d9d4006d1ecbc730ece — corrected classifier ordering so all raw keys beginning with `Gslb` are classified as `gslb_related` before context fallback.

### Executed evidence

GitHub Actions workflow: Offline Qualification, run #9, evaluated the PR merge ref containing 3078e04a.

Result:
- Python compile: PASS
- Test collection: 80
- Tests: 79 PASS / 1 FAIL
- Failure: GROUP_GSLB_RELATED was still absent from core.import_classifier in that merge ref.

A subsequent workflow run #11 evaluated the next PR merge ref containing the partial GSLB correction.

Result:
- Python compile: PASS
- Test collection: 80
- Tests: 76 PASS / 4 FAIL
- Root cause: the correction referenced GROUP_GSLB_RELATED but did not yet define the constant/group in the classifier.
- The four failures were consequences of that missing definition.

The completed group-definition correction was commit 3bb666db0e8af104f353ea32279d3bf208cb6943.

The final classifier-order correction is commit e2fe107652b7c0ea38ba7d9d4006d1ecbc730ece.

### Final executed evidence for the correction

GitHub Actions workflow: Offline Qualification, run #19, evaluated commit e2fe107652b7c0ea38ba7d9d4006d1ecbc730ece.

Result:
- Workflow status: completed
- Workflow conclusion: success
- Python compile: PASS
- Full test-suite step: PASS
- GitHub Actions job `test`: PASS

The completed run provides executable evidence that the corrected classifier ordering passes the repository's available qualification test suite.

### Qualification state

RATIFIED — Q0-FIX-001.

Scope of ratification:
- The import/classifier correction represented by e2fe107 is supported by completed CI evidence.
- This ratification does not extend to live Avi/FortiADC interoperability or production qualification.

## Q0 security baseline

The security-hardening test file contains seven security-focused tests covering adversarial event sanitization, sanitizer idempotency, explicit first-run bootstrap password handling, fail-closed FortiADC existence checks, and repeated deployment create/update behavior.
The available run #9 executed all seven successfully.

This is evidence for those test cases only; it does not qualify real Avi/FortiADC interoperability.

## Qualification boundary

The repository and mock suite can provide code-level and simulated evidence. They do not establish compatibility with every Avi release, the exact FortiADC target release/API, live Infoblox/OpenStack/Contrail behavior, production tenant isolation, production DNS cutover/rollback, production failure recovery, or preservation of unrelated target configuration.
Those remain unverified until executed against the relevant systems and captured as qualification evidence.


## Q0-DOC-AUDIT-001 — target-dependent operational claims

### Work performed
Reviewed the repository's operational and technical documentation for claims that could be mistaken for qualification evidence. Corrected three areas:
- Runbook target-specific API/CLI/UI details, timing values, and operational differences are explicitly marked as examples/planning values pending exact-release validation.
- Security documentation distinguishes the air-gapped core from optional LLM gateway/proxy behavior; sanitized advisory material may leave the environment when an external gateway is configured.
- Technical architecture describes RAG retrieval as lexical TF-IDF/BM25 rather than semantic search and aligns deployment ordering language with the repository-defined deployer order.

Commit: cca683a6359389e671389b3894b018bd83887d0b.

### Executed evidence
GitHub Actions workflow: Offline Qualification, run #22, evaluated commit cca683a6359389e671389b3894b018bd83887d0b.

Result:
- Workflow status: completed
- Workflow conclusion: success
- Python compile: PASS
- Full test-suite step: PASS
- GitHub Actions job: PASS

### Qualification state
RATIFIED — Q0-DOC-AUDIT-001.

Scope: documentation claim qualification only. This does not establish live platform compatibility or production behavior.

### Ledger commit verification
The evidence-recording ledger commit is d4e8e697c44108e82d55351df44742fbf870c9fe. GitHub Actions Offline Qualification run #24 evaluated that commit and completed successfully:
- Python compile: PASS
- Full test-suite step: PASS
- Workflow conclusion: success

This closes the documentation-audit evidence chain.


## Q0-DOC-CONSISTENCY-001 — canonical documentation and claim-boundary audit

### Work performed

The documentation set was reorganized into a canonical docs/ hierarchy and expanded to cover:

- product scope and lifecycle;
- operating model and tenant sequencing;
- architecture and data flow;
- object mapping boundaries;
- unsupported/manual/blocked features;
- DataScript migration;
- GSLB migration;
- OpenStack/Contrail/Infoblox/DNS dependencies;
- security and data handling;
- CLI, wizard and web-console entry points;
- qualification method;
- runbook/troubleshooting references.

Root USER-GUIDE.md, ARCHITECTURE.md and SECURITY.md were reduced to compatibility pointers to prevent duplicate documentation drift.

Operational claims were qualified where repository evidence does not establish live behavior, including universal idempotency, rollback safety/atomicity, DNS propagation timing and production qualification.

A repository documentation-consistency test was added at tests/test_documentation_consistency.py. It checks the canonical documentation set, documented entrypoints, known unqualified operational overclaims, root compatibility pointers, and preservation of non-PASS live qualification statuses.

### Commits

- 1e7a1de7ce0ef7e00429cfc2bc40eca945bbc589 through b955f9b2b90fad7bc864a94666a009de3934da42 — canonical documentation additions.
- 6d5b34530c153ea4fa19e0f4129effb64fc27325 — canonical user guide rewrite.
- 49af293f0062ca226502a913b5ee7c900c318732 — architecture alignment.
- dffaafed19a7c1dd627b70cd8e14d938ed80e1ee — security alignment.
- 80da10929da255a11d4b733b5f456069f6185c82 — troubleshooting claim qualification.
- e152250f3bc1dca8fc66abab81e153502099e576 — root user-guide compatibility pointer.
- 4eba38b6ac0825c28193f8b7065f67a747e56e75 — root architecture compatibility pointer.
- 8001839afa8f3d68b27c388afaa19841221c6ec6 — root security compatibility pointer.
- 0c7c26fe98b11465207dd8aa979df2c37040ece7 — README documentation/qualification entry points.
- b15a3549acd569a2861149fa4c659735fa117e85 — documentation consistency tests.

### Executed evidence

The repository workflow is configured to compile Python and run the full pytest suite on pushes to hardening branches and pull requests to main.

Initial execution of the documentation-consistency work exposed one test defect: run #65 for commit 8625a8255b9cd8164e2cd16b01165de3c7e1167b completed with 1 failure and 84 passes because the test expected GSLB-MIGRATION.md under docs/ while the repository file is intentionally at the repository root. The failure was reviewed and corrected by commit fdb80eec24ed166ccbf79830593585cc87a6c054.

Final executed evidence for the correction:
- GitHub Actions workflow: Offline Qualification
- Run: #67
- Run ID: 35578541731
- Evaluated commit: fdb80eec24ed166ccbf79830593585cc87a6c054
- Workflow status: completed
- Workflow conclusion: success
- Job test: PASS
- Python compile: PASS
- Full pytest step: PASS

The failed run is retained as evidence of the defect discovery and repair; it is not treated as a passing result.

### Final ledger verification

The evidence-recording ledger commit is `f706a83c761b6ce84315a085de8202dc09f41bea`. GitHub Actions Offline Qualification run #69 (run ID `35579762330`) evaluated that commit and completed successfully:
- Workflow status: completed
- Workflow conclusion: success
- Job `test`: PASS
- Python compile: PASS
- Full pytest step: PASS

This provides completed executable evidence for the evidence-recording commit itself.

### Qualification state

RATIFIED — Q0-DOC-CONSISTENCY-001.

Scope of ratification:
- Canonical documentation organization and documentation-consistency checks are supported by completed CI evidence.
- The known documentation test defect was detected in run #65, corrected in `fdb80eec`, and the correction passed run #67.
- The evidence-recording ledger commit then passed run #69.
- This ratification does not establish live Avi/FortiADC/OpenStack/Contrail/Infoblox interoperability or production qualification.


## Q0-002 — security artifact redaction

### Work performed

Persisted migration-event JSONL artifacts and CEF audit exports were changed to sanitize secret-bearing fields before serialization. Adversarial tests cover credential-bearing authority values, including username/password forms without an explicit URL scheme.

Commits:
- efa97472c8822016bf0d012b6f6d0a2b40710429 — sanitize persisted migration-event artifacts.
- 215dc66c908e1e3b54602d34028afa05e2f7e003 — sanitize CEF audit export fields.
- d5e37e65ba6ae34fde7195715ab7e41039847d45 — add JSONL/CEF leakage regression tests.
- 003ad437315d0a1bce93c59c5b4e5389969f2799 — redact credential-bearing authority values and close the failing CEF case.

### Executed evidence

GitHub Actions workflow: Offline Qualification, run #83, run ID 35595063729, evaluated commit 003ad437315d0a1bce93c59c5b4e5389969f2799.

Result:
- Workflow status: completed
- Workflow conclusion: success
- Python compile: PASS
- Full pytest step: PASS
- GitHub Actions job `test`: PASS
- Test suite: completed successfully after the previously observed CEF secret-leakage failure.

### Qualification state

RATIFIED — Q0-002.

Scope: repository-level artifact sanitization covered by the executed regression tests. This does not establish absence of secret leakage in every untested code path or in live external integrations.

## Live-system qualification status

The repository intentionally records real-system requirements separately from offline repository evidence. They remain NOT-TESTED until evidence is captured from the relevant environment:

- Q1 Avi 22.1.5: controller/version, tenant isolation, inventory, dependency, GSLB and DataScript evidence — NOT-TESTED.
- Q2 OpenStack/Contrail/Infoblox: tenant/project, network/VRF, DNS/IPAM and integration evidence — NOT-TESTED.
- Q3 FortiADC: exact firmware/API/VDOM model and target behavior — NOT-TESTED.
- Q4 Migration: representative tenant dry-run/deployment/repeat/preservation/failure recovery/rollback/parallel-run/DNS cutover and rollback — NOT-TESTED.
- Q5 Production: approval/SoD, audit integrity, cross-tenant denial, permission fail-closed behavior and production secret-leakage evidence — NOT-TESTED.

These statuses are deliberate qualification boundaries, not inferred PASS results.
