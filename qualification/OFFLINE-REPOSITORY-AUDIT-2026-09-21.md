# Offline Repository Qualification Audit — 2026-09-21

## Work item

**ID:** Q0-AUDIT-001  
**Scope:** Repository correctness and qualification-boundary audit  
**Branch:** `hardening/audit-fixes-2026-09-21`  
**Baseline evidence commit:** `e0e5287ed87b44c0489564b1ed4d875abb518d58`  
**Rule:** This document records evidence; it does not convert unexecuted tests into PASS.

## Evidence reviewed

- `README.md`
- `ARCHITECTURE.md`
- `qualification/README.md`
- `tests/LOCAL-TEST-SETUP.md`
- `.github/workflows/qualification.yml`
- GitHub commit metadata and Actions/status state for `e0e5287ed87b44c0489564b1ed4d875abb518d58`

## Findings

### 1. Qualification boundary is explicitly documented

**Result:** PASS — DOCUMENTATION

The README states that unit/mock tests do not establish compatibility with every Avi, FortiADC, Infoblox, OpenStack, or Contrail release and calls for representative real-system validation covering tenant isolation, target API behavior, repeated-run/idempotency, DNS cutover/rollback, and failure recovery.

The qualification pack independently states that real-device qualification requires evidence from representative systems and that unavailable/not-tested cases are not PASS.

### 2. Offline qualification workflow exists

**Result:** IMPLEMENTED — EXECUTION PENDING

The repository contains `.github/workflows/qualification.yml` configured to:

1. install Python 3.11;
2. install `requirements.txt`;
3. compile Python;
4. run `pytest -q`.

No GitHub Actions workflow run or commit status was available for baseline commit `e0e5287ed87b44c0489564b1ed4d875abb518d58` at audit time.

Therefore the workflow is **not yet evidence of a passing test suite**.

### 3. Architecture contains an over-broad idempotency statement

**Result:** FINDING — DOCUMENTATION NEEDS CORRECTION

`ARCHITECTURE.md` currently says:

> "Idempotent deployment | Deploy checks object existence before create. Re-running is always safe."

The README correctly qualifies this by stating that complete idempotency must be validated against the target release/configuration.

The architecture wording is stronger than the current qualification evidence supports. It should be changed to describe the implemented exists/update/create behavior while explicitly requiring target-version validation.

### 4. Local test guide contains an over-broad rollback/demo claim

**Result:** FINDING — DOCUMENTATION NEEDS CORRECTION

`tests/LOCAL-TEST-SETUP.md` contains a demo narrative stating that one command "returns everything to AVI."

The repository qualification model requires rollback to be tested and evidenced; the mock/offline material cannot establish production rollback semantics. The statement should be rewritten as a rollback-script/demo description with an explicit qualification boundary.

### 5. Mock environment is useful but not target qualification

**Result:** PASS — DOCUMENTED BOUNDARY

The local test guide provides fixture and mock-server procedures. These can establish code-level and mock behavior when actually executed. They do not establish FortiADC appliance API semantics, Avi controller behavior across releases, or production DNS/network rollback behavior.

### 6. Real environment qualification remains open

**Result:** NOT-TESTED / UNVERIFIED

The following require representative-system evidence:

- Avi 22.1.5 read-only discovery against the actual deployment;
- tenant isolation and cross-tenant reference behavior;
- OpenStack/Contrail dependency behavior;
- Infoblox and external DNS integration;
- exact FortiADC target release/API/VDOM behavior;
- deploy/update behavior against the real target;
- repeated-run/idempotency behavior on the target;
- failure recovery;
- parallel-run validation;
- DNS cutover and rollback;
- preservation of unrelated target configuration.

## Test evidence ledger

| Test/evidence | Expected | Observed | Status |
|---|---|---|---|
| Workflow file present | Qualification workflow exists | Present | PASS — CODE |
| GitHub Actions run for baseline commit | Executed result | No run returned | NOT-TESTED |
| Commit status for baseline commit | Test status | No status returned | NOT-TESTED |
| Local pytest execution | Actual test result | Not executed in this qualification step | NOT-TESTED |
| Real Avi/FortiADC qualification | Representative-system evidence | No representative target evidence in repository | UNVERIFIED |
| Documentation consistency | Claims bounded by evidence | Two over-broad claims identified | FINDING |

## Ratification state

**Q0-AUDIT-001: NOT RATIFIED.**

Reason: the audit itself is committed evidence, but executable test results are not available yet. Documentation findings also remain open.

## Required next work

1. Correct the architecture idempotency wording.
2. Correct the local rollback/demo wording.
3. Commit each correction separately.
4. After each commit, obtain actual automated test evidence.
5. Ratify only when the corresponding evidence is available and reviewed.
6. Continue with repository-level Q0 tests before claiming Q0 qualification.

