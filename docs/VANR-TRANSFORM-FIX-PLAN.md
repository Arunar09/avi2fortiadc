# Transform Gate: V-A-N-R Mapping End-to-End Fix

## Problem Summary

The current V-A-N-R Mapping tab is architecturally disconnected. Operator decisions saved in
manifest.vdom_mapping.mappings are never consumed downstream. The transformer ignores them,
the deployer ignores them, and the Fetch Target VDOMs button silently reads local data with
a misleading label. The entire tab is a UI-only facade with zero functional downstream effect.

This plan wires the mapping end-to-end across 6 layers.

---

## Open Questions

Q1 (VDOM scope for non-VS objects):
In FortiADC, health checks, SSL profiles, and persistence profiles can be global
or scoped per VDOM. Should shared objects stay in root or be duplicated per VDOM?
Default proposal: Shared objects remain in root. VS-specific objects inherit their tenant VDOM.

Q2 (Tenant on a VS):
The discovery JSONs virtual_services[].tenant_ref maps to an Avi tenant name.
Confirm tenant_ref is always present and is the key used for vdom_mapping.mappings.
Current topology_builder assumes this.

Q3 (Mapping gate block or warn):
Should ALL tenants require a resolved mapping before Approve? Or do unmapped tenants
default to create_new with the tenant name as the VDOM name?
Default proposal: Unmapped tenants default to create_new using tenant name. Warning shown but not blocking.

---

## Layer 1: New pipeline_service function get_transform_mapping_review()

File: services/pipeline_service.py

Mirrors get_import_scope_review() pattern. Returns a structured dict:

{
    tenant_mappings: [
        {
            tenant: TENANT-PROD-A,
            vs_count: 5,
            pool_count: 5,
            vip_list: [10.1.0.x, ...],
            current_strategy: create_new,
            current_target: TENANT-PROD-A,
            is_mapped: True,
            is_shared_target: False,
            shared_with: [],
            conflict_names: [],
        }
    ],
    connection_status: offline,
    available_vdoms: [root, ...],
    available_vdoms_source: local_generated_config,
    unmapped_count: 2,
    conflict_count: 0,
    summary: {
        total_tenants: 3,
        mapped_tenants: 1,
        create_new_count: 2,
        merge_count: 1,
    }
}

This data drives the new UI and is consumed by the transformer.

---

## Layer 2: Transform Gate UI

File: templates/decisions_gate.html

Replace the current minimal mapping table with a rich panel matching Import Scope quality.

Section A: Connection Status Banner (replaces misleading Fetch button)
- Shows OFFLINE Using local planning data or LIVE Connected to FortiADC
- Source label: local generated config / manual decisions / live API
- A single Probe FortiADC Connection button that tests connectivity and updates the banner.
  If offline, hides the live VDOM list and shows a clear message.

Section B: Summary Bar (new, mirrors Import Scope column totals)
- Total tenants: X | Mapped: Y | Create New: A | Merge to Existing: B | Unmapped: C
- Conflict indicator: N name collisions detected shown in red if conflicts exist.

Section C: Tenant Mapping Table
Each row expands on click to show detail (accordion like Import Scope). Columns:
  Tenant | VS Count | VIPs | Strategy | Target VDOM | Status

Expanded row shows:
- VS list: table of VS names and VIPs belonging to this tenant
- Pool list: pool names and member counts
- Conflict check: inline warning if objects name-collide in the shared VDOM
- Guidance: static text per strategy explaining the impact

Section D: Available Target VDOMs Panel
- VDOMs from local generated config and manifest decisions
- Each VDOM shows how many tenants map to it and whether conflicts exist
- Clear source label: local planning file or live FortiADC API

Commit Controls:
- Save Draft Mappings: saves without resolving
- Commit VDOM Mapping: sets vdom_mapping.resolved = True, prerequisite for Approve

Transform Approval Resolve is blocked unless vdom_mapping.resolved = True.

---

## Layer 3: Transform Approval Gate route update

File: ui/routes.py

In decisions_transform_approval(), pass transform_mapping_review from
pipeline_svc.get_transform_mapping_review() to the template.

In api_fortiadc_existing_vdoms(), rename source labels to be unambiguous:
- offline becomes local_generated_config
- manual orchestrator baseline becomes manual_decisions
- Add optional FortiADCClient.get(system/vdom) attempt from config.yaml.
  On exception, fall back to local sources with source = local_fallback.

---

## Layer 4: Transformer consumes vdom_mapping

File: migrate.py cmd_transform() (line 340)

After loading the manifest, build a tenant to VDOM lookup:

    vdom_mappings = manifest.get(vdom_mapping, {}).get(mappings, {})

    def resolve_vdom(tenant_name: str) -> str:
        mapping = vdom_mappings.get(tenant_name, {})
        if mapping.get(strategy) in (existing, create_new) and mapping.get(target):
            return mapping[target]
        return tenant_name  # default: create_new using tenant name

In the VS transform loop, after t_vs.transform(vs):
    tenant = vs.get(tenant_ref, ).split(/)[-1] or vs.get(_tenant, root)
    result[vdom] = resolve_vdom(tenant)
    result[payload][vdom] = resolve_vdom(tenant)

Pools and health checks that are VS-specific inherit the VS VDOM.
Shared objects (multi-tenant) stay in root.

---

## Layer 5: Deployer uses per-object VDOM

File: core/fortiadc_client.py

Add vdom parameter to get(), create(), and update():

    def _params(self, vdom=None) -> dict:
        return {vdom: vdom or self.vdom}

    def create(self, path, payload, vdom=None):
        params = self._params(vdom)
        ...

File: deployers/fortiadc_deployer.py

In _deploy_one(), extract per-object VDOM and pass to client:

    vdom = obj.get(vdom)
    self._client.create(path, payload, vdom=vdom)

---

## Layer 6: Gate validation adds vdom_mapping.resolved check

File: core/decision_manifest.py ensure_phase_gate()

In the transform phase block, add:

    if not manifest.get(vdom_mapping, {}).get(resolved, False):
        return GateResult(False, VDOM mapping not committed, [vdom_mapping.resolved])

vdom_mapping.resolved is set only by the Commit VDOM Mapping button in the UI.

---

## What Is NOT Changing

- Import Scope gate logic
- Analysis Triage gate
- Deploy gate checklist (SoD/CR/mode requirements)
- Topology visualization (D3 graph already reads vdom_mapping.mappings correctly)
- Audit trail mechanism (all changes flow through store.append_audit())

---

## Verification Steps

1. Complete import scope and analysis triage for a test environment
2. Open Transform gate V-A-N-R Mapping tab
3. Confirm tenant list is populated from the generated configs VS tenant_refs
4. Set TENANT-PROD-A to create_new with target VDOM-Production
5. Set TENANT-PERF-A to existing with target VDOM-Production (merge) and confirm conflict warning
6. Click Commit VDOM Mapping
7. Click Approve Transform and confirm gate passes
8. Re-run transform phase
9. Open fortiadc/<env>-config.json and confirm each VS has vdom: VDOM-Production
10. In dry-run deploy, confirm deployer log shows vdom=VDOM-Production per API call
11. Test connection probe: offline shows correct banner, bad credentials shows error badge not crash
