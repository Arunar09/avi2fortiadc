# Decision Page Reference Guide

The **Decision Page** is the primary operator workbench for a migration environment. It is the control plane between automated pipeline phases. No pipeline phase advances without explicit operator decisions made here.

The page is accessed via the **Decisions** button on the Dashboard and serves as a sequential gate system. Each gate must be resolved in order before the next unlocks.

---

## Overview Page

Before entering any gate, the overview page shows the current state of all 5 gates in a linear sequence.

| Gate | Route | Manifest Key Checked |
|---|---|---|
| Import Scope | /decisions/ENV/import-scope | resolved_import_scope |
| Analysis Triage | /decisions/ENV/analysis-triage | analysis_triage.resolved |
| Transform Approval | /decisions/ENV/transform-approval | transform_approvals.resolved |
| Deploy Approval | /decisions/ENV/deploy-approval | deploy_approval.resolved |
| Post Validation | /decisions/ENV/post-validation | post_validation_decision.resolved |

The first gate that is not resolved becomes the current_progress shown on the overview.

---

## Gate 1: Import Scope

### Purpose
Defines **what** from the uploaded Avi configuration will be included in the migration. This is the triage of raw Avi object types before any transformation occurs.

### What the Operator Does
1. Reviews every Avi object type discovered in the raw snapshot (e.g., VirtualService, Pool, GslbService, WafPolicy).
2. Assigns each object type one of three decisions:
   - **include_in_pipeline** - Object will be transformed and deployed to FortiADC.
   - **context_only** - Object is referenced for awareness but not deployed.
   - **exclude** - Object is completely ignored.
3. Optionally selects specific Tenants and individual Virtual Services to scope the migration to a subset of the environment.
4. Optionally creates a Sub-Task (a child environment) to delegate a subset of tenants/VSes to an isolated pipeline.
5. Clicks Commit Import Scope to lock the decisions and trigger automatic Analysis.

### Pass Condition (from core/decision_manifest.py)
- resolved_import_scope must be True.
- Every key in raw_key_inventory must have a valid decision in raw_key_decisions.
- The discover phase in the ledger must be done.

### Manifest Fields Written
- raw_key_decisions: Map of each raw Avi key to {decision, rationale}.
- resolved_import_scope: Boolean set to True on commit.
- tenant_filter.included_tenants: List of selected tenants.
- tenant_filter.included_vses: List of individually selected Virtual Services.

### On Resolution
Triggers pipeline_svc.commit_import_scope() which filters the raw snapshot down to the committed scope and writes the candidate discovery file. The operator is automatically redirected to Gate 2.

> WARNING: Re-committing an already-committed scope replaces all downstream analysis results. The UI requires a Force Re-commit checkbox to prevent accidental overwrites.

---

## Gate 2: Analysis Triage

### Purpose
Reviews all objects that the automated analyzer flagged as unsupported or requiring manual attention. Objects are grouped by their failure reason and severity to avoid repetitive acknowledgement.

### What the Operator Does
1. Reviews grouped issues. Each group represents items sharing the same object_type, reason, and severity.
2. Assigns each group one of three triage decisions:
   - **accept** - The operator acknowledges the limitation and accepts the risk. Migration proceeds without this object.
   - **override** - The operator will provide a manual configuration fix.
   - **defer** - The issue is out of scope for this migration (e.g., GSLB). Items in this category default to defer automatically.
3. Optionally expands each group to inspect raw Avi JSON, dependency chains, and VIP information.
4. Clicks Mark Resolved to lock triage and trigger automated Transform.

### Group Keying Logic
Groups are keyed by an MD5 hash of {object_type}:{reason}:{severity}:{content_hint}. For DataScripts, the content_hint is an MD5 of the script content itself, ensuring identical scripts are grouped even if their names differ.

### Pass Condition
- analysis_triage.resolved must be True in the manifest.

### Manifest Fields Written
- analysis_triage.items: Map of {object_type}:{object_name} to {decision, rationale, notes, group_key}.
- analysis_triage.resolved: Boolean set to True on resolution.

### On Resolution
Automatically triggers run_pipeline_phase(dirs, env, "transform") and redirects to Gate 3.

> NOTE: GSLB-related patterns are pre-populated with defer and a standard rationale to minimize operator effort for out-of-scope items.

---

## Gate 3: Transform Approval

### Purpose
Reviews the generated FortiADC configuration (the actual JSON that will be deployed). The operator validates each category of generated objects before authorizing deployment.

### Tabs in This Gate

#### Tab A: Guided Category Review
Reviews each generated object category (Certificates, SSL Profiles, Health Checks, Pools, Real Servers, Pool Members, Persistence Profiles, Virtual Servers). For each category, the operator sets a review state:
- review_pending - Not yet reviewed.
- approved - Category content is correct.
- needs_changes - Operator has flagged an issue.
- not_applicable - Category is empty / not relevant.

#### Tab B: V-A-N-R Mapping (VDOM-App-Network-Route)
The infrastructure orchestrator. The operator:
1. Fetches existing FortiADC VDOMs to see the target baseline.
2. Maps each Avi source tenant to a target FortiADC VDOM:
   - create_new: A new VDOM will be created for this tenant.
   - existing: The tenant's objects will be merged into an already-existing VDOM.
3. Optionally adds manual VDOM definitions (for VDOMs with specific capacity/networking properties).
4. Uses Commit All Infrastructure Decisions to batch-save all mappings atomically.

#### Tab C: V-A-N-R Topology
A D3.js visualization of the current mapping decisions, showing VDOMs, Application Stacks (Virtual Services -> Pools -> Real Servers), and Network Stacks. Automatically flags VDOMs with conflicts when two tenants sharing a VS name are merged into one VDOM.

#### Tab D: Operator Overrides
Allows the operator to stage a custom JSON payload for any individual FortiADC object. The override key is {fortiadc_path}::{mkey}. Staged overrides are applied on the next Transform run.

#### Tab E: Full Raw JSON
Read-only view of the complete generated {env}-config.json file.

### Pass Condition
- transform_approvals.resolved must be True.
- analysis_triage.resolved must be True (upstream).
- The transform phase in the ledger must be done.

### Manifest Fields Written
- transform_approvals.groups: Map of category name to {decision, rationale, notes, saved_at}.
- transform_approvals.resolved: Boolean set to True on resolution.
- vdom_mapping.mappings: Map of Avi tenant to {strategy, target, params, updated_at, updated_by}.
- manual_overrides: Map of {path}::{mkey} to {fortiadc_path, mkey, payload, note, saved_at, saved_by}.

### Snapshot & Rollback
At any point in this gate, the operator can create a versioned manifest snapshot stored as manifest.v{N}.{label}.{timestamp}.json. Previous snapshots can be restored, reverting all manifest decisions to that point in time.

> WARNING: Clicking Approve resolves the gate but does NOT deploy anything. It only unlocks Gate 4.

---

## Gate 4: Deploy Approval

### Purpose
The final governance gate before live deployment. Enforces enterprise change management and Separation of Duties (SoD).

### Pre-Flight Checklist (all 5 required)

| # | Check | Manifest Field |
|---|---|---|
| 1 | Dry-run output reviewed | dry_run_reviewed |
| 2 | Change Request ID recorded | cr_id |
| 3 | Second approver (SoD) confirmed | sod_approver + governance_confirmed |
| 4 | Maintenance window confirmed | maintenance_window |
| 5 | Rollback readiness acknowledged | operator_ack |

The operator also selects the execution strategy:
- **dry_run** (default): All 5 checks can be filled but deployment is blocked.
- **execute**: Required to unlock the final authorization signature.

Only a user with the approver or admin role can sign off. The sod_approver must be a different user from the current operator (SoD enforcement from source code line 1664 in routes.py).

### Pass Condition (all must be true simultaneously)
- transform_approvals.resolved = True
- deploy_approval.resolved = True
- deploy_approval.mode = "execute"
- dry_run_reviewed = True
- cr_id is non-empty
- sod_approver is non-empty AND different from current operator
- operator_ack = True
- governance_confirmed = True
- maintenance_window = True

### Live Execution
The Execute Deployment button calls /decisions/ENV/execute-deploy which calls run_pipeline_phase with live_execution=True. This is the actual REST API push to FortiADC.

> CAUTION: The live execution is irreversible. The tool only proceeds if both deploy_approval.resolved = True AND mode = "execute".

---

## Gate 5: Post Validation

### Purpose
The post-cutover close-out gate. Records the final operational state of the migration, including parallel run confirmation and DNS cutover completion.

### What the Operator Does
1. **Parallel Run Milestone**: Marks when FortiADC has been running alongside Avi and is confirmed healthy. Records operational notes.
2. **DNS Cutover Milestone**: Marks when DNS has been switched to FortiADC as the primary. Records notes.
3. Selects a final action:
   - **proceed** - Migration is complete. Avi can be decommissioned.
   - **hold** - Migration is paused. Avi remains primary.
   - **rollback** - Migration is reverted. DNS is switched back to Avi.
4. Clicks Mark Resolved to close the migration lifecycle.

### Pass Condition
- The deploy phase in the ledger must be done.
- post_validation_decision.resolved must be True.
- post_validation_decision.action must be one of: proceed, hold, rollback.

### Manifest Fields Written
- post_validation_decision.action: Final action string.
- post_validation_decision.notes: Operator closure notes.
- post_validation_decision.milestones.parallel_run: Boolean.
- post_validation_decision.milestones.dns_cutover: Boolean.
- post_validation_decision.resolved: Boolean set to True on resolution.

### On Resolution
Writes phase_done("parallel-run") and phase_done("dns-cutover") entries to the State Ledger. Logs a SUCCESS system event closing the migration lifecycle.

---

## Manifest State Machine Summary

`
raw_key_decisions (all set)
        |
        v
resolved_import_scope = True
        |
        v
analysis_triage.resolved = True  <-- auto-triggers transform
        |
        v
transform_approvals.resolved = True
        |
        v
deploy_approval.resolved = True + mode = "execute"  <-- SoD enforced
        |
        v
[LIVE DEPLOY: /decisions/ENV/execute-deploy]
        |
        v
post_validation_decision.resolved = True + action in {proceed, hold, rollback}
`

---

## Audit Trail

Every operator action across all gates appends an entry to manifest.audit[] and writes a line to logs/ENV/decisions.jsonl. Each entry contains:
- timestamp (UTC ISO)
- operator (username from getpass.getuser())
- action (e.g., transform_category_review_saved, deploy_approval_updated)
- detail (action-specific payload)

The manifest is protected by a manifest_hash (SHA-256 of all non-meta fields) recomputed on every save.
