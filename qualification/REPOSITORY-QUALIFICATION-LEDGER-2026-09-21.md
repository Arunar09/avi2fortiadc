# Repository Qualification Ledger — 2026-09-21

## Operating rule

Each work item follows: WORK → COMMIT → EXECUTE AVAILABLE TESTS → CAPTURE ACTUAL RESULT → REVIEW → RATIFY OR FIX.
A work item is not ratified when executable evidence is absent or when any required test fails.

## Q0-FIX-001 — import/test contract alignment

### Commits
- 3078e04a20b8bc92eea4da10ba31f625a0b9e43c — aligned tests with the current strict import, state-ledger path, and paginated activity contracts.
- 7c0e33d9285ec5d8767ea107e82e2e229c463dde — partial GSLB classification correction.
- 3bb666db0e8af104f353ea32279d3bf208cb6943 — completed GSLB classifier group definition and classification.

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

The completed correction is commit 3bb666db0e8af104f353ea32279d3bf208cb6943.

### Current qualification state

NOT RATIFIED.

Reason: the completed correction has not yet been observed in a completed CI test result. The latest completed evidence predates the complete correction.

## Q0 security baseline

The security-hardening test file contains seven security-focused tests covering adversarial event sanitization, sanitizer idempotency, explicit first-run bootstrap password handling, fail-closed FortiADC existence checks, and repeated deployment create/update behavior.
The available run #9 executed all seven successfully.

This is evidence for those test cases only; it does not qualify real Avi/FortiADC interoperability.

## Qualification boundary

The repository and mock suite can provide code-level and simulated evidence. They do not establish compatibility with every Avi release, the exact FortiADC target release/API, live Infoblox/OpenStack/Contrail behavior, production tenant isolation, production DNS cutover/rollback, production failure recovery, or preservation of unrelated target configuration.
Those remain unverified until executed against the relevant systems and captured as qualification evidence.