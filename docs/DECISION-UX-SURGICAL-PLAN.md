# Decision UX Surgical Plan

Purpose: make the decision pipeline safer, clearer, and resumable without broad rewrites.

## Scope

This plan covers the operator-facing decision flow only:

1. `decisions_overview`
2. `import_scope`
3. `analysis_triage`
4. `transform_approval`
5. `deploy_approval`
6. `post_validation_decision`

Primary goal:
- replace the remaining placeholder gate experiences
- make blocking reasons obvious before submit
- keep all decisions auditable and resumable

Out of scope for this pass:
- major backend pipeline redesign
- new persistence models unless strictly required
- non-decision tabs except where they must reflect gate state

## Current Gaps

1. `deploy_approval` is still a bare form for the highest-risk gate.
2. `post_validation_decision` is still a bare form with weak consequence framing.
3. `decisions_overview` does not visually communicate sequential dependency between gates.
4. gate pages do not consistently explain "what is blocking me" before user submission.
5. overview lacks a compact risk signal for `AUTO / WARN / MANUAL / BLOCKED`.
6. import scope lacks a hard completion meter for `INC / CTX / EXCL / undecided`.
7. triage lacks efficient bulk action helpers for repeated issue groups.
8. decision history exists in state/logs but is not surfaced per gate.

## Recommended Delivery Order

### Wave 1: Safety and operator control

1. Deploy approval structured checklist
2. Post-validation consequence card
3. Persistent "what's blocking me" callout on each gate page

### Wave 2: Orientation and state visibility

4. Sequential gate rail on decisions overview
5. Compatibility risk bar on decisions overview
6. Import scope completion meter

### Wave 3: Speed and audit visibility

7. Triage bulk action row
8. Decision history trail per gate

## File Targets

### Primary templates

- `templates/decisions_overview.html`
- `templates/decisions_gate.html`

### Primary route/service files

- `ui/routes.py`
- `services/pipeline_service.py`
- `services/analytics_service.py` only if overview risk-bar data needs a cleaner source

### Supporting state/log sources already present

- `state/<env>/decision-manifest.json`
- `state/<env>/ledger.json`
- `logs/<env>/*.jsonl`

## Work Items

## 1. Sequential Gate Rail

Goal:
- make gate dependency visible at a glance

Implementation:
- replace equal-weight gate cards in `templates/decisions_overview.html`
- render a left-to-right rail with states:
  - `complete`
  - `active`
  - `blocked`
  - `locked`
- show gate message under each node
- disable or de-emphasize links to locked gates

Backend/data:
- reuse existing `ensure_phase_gate(...)`
- no new persistence needed

Acceptance:
- Gate 4 visually reads as locked if Gate 3 unresolved
- current actionable gate is visually distinct
- operator can tell order without reading all card text

## 2. Deploy Approval Structured Checklist

Goal:
- replace bare deploy form with a true pre-flight gate

Implementation:
- rebuild `deploy_approval` section in `templates/decisions_gate.html`
- checklist items:
  - dry-run reviewed
  - CR ID present
  - second approver captured when SoD applies
  - maintenance window confirmed
  - rollback readiness acknowledged
- show completion meter `x/5`
- disable execute approval action until all required items are checked
- show inline warning for SoD mismatch or missing approver

Backend/data:
- may require extending `deploy_approval` payload fields in `ui/routes.py`
- persist only explicit checklist fields already used by the UI

Acceptance:
- operator cannot approve execute with missing checklist items
- incomplete checklist explains why button is disabled
- saved data reloads correctly on revisit

## 3. Post-Validation Consequence Card

Goal:
- explain `proceed / hold / rollback` before save

Implementation:
- rebuild `post_validation_decision` section in `templates/decisions_gate.html`
- add dynamic consequence panel bound to selected action
- describe:
  - expected Avi state
  - DNS/cutover expectation
  - reversibility
  - operational next step

Backend/data:
- existing fields are enough
- no new persistence required

Acceptance:
- switching action updates consequence text immediately
- saved action reloads with the same consequence panel state

## 4. "What's Blocking Me" Callout

Goal:
- operator sees blocking reason before submit or trial-and-error

Implementation:
- add reusable top callout in `templates/decisions_gate.html`
- content varies by phase:
  - import: undecided keys or unresolved selection state
  - analysis: unresolved triage groups
  - transform: missing generated config or unresolved transform review
  - deploy: missing checklist prerequisites
  - post-validation: missing action or unresolved prior gate

Backend/data:
- may use existing manifest + review objects already passed by `ui/routes.py`
- keep logic minimal and derived

Acceptance:
- each decision page shows one clear plain-English blocker summary when blocked

## 5. Compatibility Risk Bar on Overview

Goal:
- show migration difficulty without opening analytics

Implementation:
- add stacked `AUTO / WARN / MANUAL / BLOCKED` bar to `templates/decisions_overview.html`
- counts should come from current env state, not stale UI assumptions

Backend/data:
- preferred source: current analysis outputs already available per env
- if needed, add a small helper in `ui/routes.py`

Acceptance:
- bar totals match the same env’s analysis/unsupported state
- zero-state handled cleanly for envs not yet analyzed

## 6. Import Scope Completion Meter

Goal:
- make scope completeness explicit and commit-safe

Implementation:
- add stacked meter above import scope shell
- counts:
  - `INC`
  - `CTX`
  - `EXCL`
  - `undecided`
- disable commit until no undecided keys remain

Backend/data:
- only valid if every top-level key has explicit decision state in review data
- if defaults are inferred, meter must still compute from effective review state consistently

Acceptance:
- meter and commit button state always match actual decision completeness

## 7. Triage Bulk Action Row

Goal:
- reduce repetition for repeated issue groups

Implementation:
- for grouped triage sections, add quick actions:
  - accept all in group
  - override all in group
  - defer all in group
- preserve individual edits after bulk apply until save

Backend/data:
- frontend-only if names already follow `decision__<key>` shape

Acceptance:
- bulk action updates visible controls immediately
- individual edits can still override bulk-applied values before submit

## 8. Decision History Trail

Goal:
- expose audit context without opening raw files

Implementation:
- add collapsible history section per gate
- show concise entries:
  - timestamp
  - actor if known
  - action
  - summary/note

Backend/data:
- source from `decision-manifest.json` and relevant `jsonl` logs
- do not dump raw log lines

Acceptance:
- operator can see the last meaningful gate actions without leaving the page

## Resume Rules

Anyone resuming should follow these constraints:

1. keep changes inside `templates/decisions_gate.html`, `templates/decisions_overview.html`, and `ui/routes.py` unless a backend gap is proven
2. avoid changing persistence shape unless a gate cannot work without it
3. prefer derived UI state over new stored state
4. every wave must end with route-level verification using Flask test client
5. do not mix unrelated UI cleanup into these passes

## Verification Checklist

For each completed wave, verify:

1. `/decisions/<env>` returns `200`
2. `/decisions/<env>/import-scope` returns `200`
3. `/decisions/<env>/analysis-triage` returns `200`
4. `/decisions/<env>/transform-approval` returns `200`
5. `/decisions/<env>/deploy-approval` returns `200`
6. `/decisions/<env>/post-validation-decision` returns `200`
7. saved form state reloads after POST
8. blocked states are visible before submit

## Suggested Execution Start

Start with Wave 1:

1. `deploy_approval` redesign
2. `post_validation_decision` redesign
3. shared blocker callout

Reason:
- these are highest-risk operator actions
- they improve safety without requiring broad backend rework
- they create the strongest immediate UX improvement

## Status

- Wave 1: pending
- Wave 2: pending
- Wave 3: pending

