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

**Result:** PASS — EXECUTED CI

The repository contains `.github/workflows/qualification.yml` configured to:

1. install Python 3.11;
2. install `requirements.txt`;
3. compile Python;
4. run `pytest -q`.

The later repository-hardening commits were executed by GitHub Actions. Run #71 evaluated the final evidence-recording commit `da8a585fb80e15682ceb6ea0738682ea3027cfd5` and completed successfully with Python compilation and the full pytest step passing.

This is executable evidence for the current repository state, rather than evidence for the historical baseline commit.

### 3. Architecture contains an over-broad idempotency statement

**Result:** CORRECTED — VERIFIED

The canonical architecture documentation was corrected to describe implemented existence/update handling and to require exact target-release validation rather than asserting universal safety. The documentation-consistency test covers this claim boundary and passed in run #71.

### 4. Local test guide contains an over-broad rollback/demo claim

**Result:** CORRECTED — VERIFIED

The local test documentation was qualified so rollback/demo behavior is not presented as production rollback evidence. The documentation-consistency test checks for the unqualified claim and passed in run #71.

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
| GitHub Actions current-state qualification | Executed result | Run #71 completed successfully | PASS — CI |
| Evidence-recording commit | Completed CI verification | `da8a585fb80e15682ceb6ea0738682ea3027cfd5`, run #71 | PASS — CI |
| Repository pytest suite | Actual test result | Full pytest step passed in run #71 | PASS — CI |
| Real Avi/FortiADC qualification | Representative-system evidence | No representative target evidence in repository | UNVERIFIED |
| Documentation consistency | Claims bounded by evidence | Consistency test passed in run #71 | PASS — CI |

## Ratification state

**Q0-AUDIT-001: RATIFIED.**

The original findings were corrected and the current repository state passed the full automated qualification workflow. The evidence-recording commit `879902e98e724ea0c03ff26d1af9d990a97b2251` was then evaluated by Offline Qualification run #73 and completed successfully, closing the evidence chain.

### Final evidence reviewed

- Offline Qualification run #73
- Run ID `35580956286`
- Commit evaluated: `879902e98e724ea0c03ff26d1af9d990a97b2251`
- Python compile: PASS
- Full pytest: PASS
- Job `test`: PASS

