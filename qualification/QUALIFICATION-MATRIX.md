# Qualification Matrix

| ID | Area | Test | Evidence required | Current status |
|---|---|---|---|---|
| Q0-001 | Repository | Unit/security tests pass | CI or local test output | NOT-VERIFIED |
| Q0-002 | Secrets | No credentials in generated artifacts | sanitized artifact scan | NOT-VERIFIED |
| Q1-001 | Avi | Controller/API version | read-only version output | NOT-TESTED |
| Q1-002 | Avi | Tenant discovery isolation | tenant-scoped evidence | NOT-TESTED |
| Q1-003 | Avi | Complete object inventory | inventory counts/list | NOT-TESTED |
| Q1-004 | Avi | Cross-object dependencies | dependency evidence | NOT-TESTED |
| Q1-005 | Avi | GSLB inventory | GSLB object evidence | NOT-TESTED |
| Q1-006 | Avi | DataScripts | script metadata/reference evidence | NOT-TESTED |
| Q2-001 | OpenStack | Tenant/project mapping | project/network evidence | NOT-TESTED |
| Q2-002 | Contrail | Network/VRF dependency | routing/VN evidence | NOT-TESTED |
| Q2-003 | Infoblox | DNS/IPAM dependency | relevant zone/record/TTL evidence | NOT-TESTED |
| Q3-001 | FortiADC | Exact target version/API | appliance metadata | NOT-TESTED |
| Q3-002 | FortiADC | VDOM mapping | VDOM evidence | NOT-TESTED |
| Q3-003 | FortiADC | API pre-flight | target API response | NOT-TESTED |
| Q4-001 | Migration | Representative tenant dry-run | dry-run artifact | NOT-TESTED |
| Q4-002 | Migration | First deployment | deployment/audit evidence | NOT-TESTED |
| Q4-003 | Migration | Repeated deployment | two-run comparison | NOT-TESTED |
| Q4-004 | Migration | Unrelated target preservation | before/after inventory diff | NOT-TESTED |
| Q4-005 | Migration | Failure recovery | injected/observed failure evidence | NOT-TESTED |
| Q4-006 | Migration | Rollback | FortiADC rollback evidence | NOT-TESTED |
| Q4-007 | DNS | Cutover | Infoblox change evidence | NOT-TESTED |
| Q4-008 | DNS | DNS rollback | restored-record evidence | NOT-TESTED |
| Q4-009 | Migration | Parallel run | source/target behavior evidence | NOT-TESTED |
| Q5-001 | Governance | Approval / SoD | audit record | NOT-TESTED |
| Q5-002 | Governance | Tamper/audit integrity | audit verification | NOT-TESTED |
| Q5-003 | Security | Tenant A cannot operate on B | negative test evidence | NOT-TESTED |
| Q5-004 | Security | Permission denial fails closed | 401/403 test evidence | NOT-TESTED |
| Q5-005 | Security | No secret leakage | artifact/log scan | NOT-TESTED |

## Status semantics

- **PASS** — evidence demonstrates the requirement for the tested version/environment.
- **FAIL** — evidence demonstrates the requirement is not satisfied.
- **BLOCKED** — test could not safely be executed because a prerequisite is missing.
- **NOT-TESTED** — no evidence has been collected.
- **MANUAL** — supported only through a documented operator procedure.
- **UNKNOWN** — evidence is insufficient to determine behavior.

Do not convert NOT-TESTED or UNKNOWN into PASS by inference.
