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

The latest documentation-consistency commit has been pushed, but a completed CI result for b15a3549 has not been observed through the available GitHub workflow/status interface in this session.

### Qualification state

NOT-RATIFIED — Q0-DOC-CONSISTENCY-001.

No PASS is recorded until the completed workflow result for the current work is observed and reviewed.
