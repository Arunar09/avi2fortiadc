# Operational Glossary & Logic Reference

This document clarifies the tool's decision-making logic and the meaning of operator actions.

## 1. Action Meanings (Analysis Triage)

When reviewing discovered objects in the **Analysis Triage** gate, the operator must choose an action for each "unsupported" or "complex" pattern.

| Action | Meaning | Tool Result |
| :--- | :--- | :--- |
| **ACCEPT** | The tool will attempt to auto-transform the object using its internal mapping logic. | A corresponding FortiADC object is generated in the `fortiadc-config.json`. |
| **OVERRIDE** | The tool will skip auto-generation for this object. Instead, it expects the operator to provide a manual payload. | The object is listed in the **Manual Overrides** tab of the Transform gate for operator input. |
| **DEFER** | The object is excluded from the current migration cycle. | No FortiADC configuration is generated for this object or its children. |

---

## 2. Infrastructure Mapping (V-A-N-R)

The Transform Approval gate uses a **V-A-N-R** (VDOM-App-Network-Route) approach to infrastructure orchestration.

### Suppressed Tenants
The tool only displays tenants that contain at least one **Virtual Service**. 
*   **Why?** In Avi, many tenants are purely organizational or empty. FortiADC requires a VDOM (Landing Zone) for every application stack. 
*   **Result**: If a tenant has no "Applications" (VSes), it is suppressed from the mapping view to reduce noise. 

### Strategies
*   **CREATE NEW**: A new VDOM with the same name as the Avi tenant will be defined in the target config.
*   **MERGE / CONSOLIDATE**: The Avi tenant's objects will be merged into an existing VDOM (e.g., `root`).

---

## 3. Universal Notes
Every decision point (Import Scope, Triage, VDOM Mapping) now includes an **Operator Note** field.
*   These notes are persisted in `decision-manifest.json`.
*   Use these fields to document stakeholder approvals or technical rationales for audit purposes.
