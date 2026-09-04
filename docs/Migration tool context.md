# Avi to FortiADC Migration Tool - Presentation & Demo Guide

## 1. Executive Summary
This tool automates the complex migration of Application Delivery Controller (ADC) configurations from VMware Avi (NSX Advanced Load Balancer) to Fortinet FortiADC. It is built on a **deterministic pipeline** that ensures high fidelity, air-gapped security, and operator-led governance.

---

## 2. Configuration Mapping (Holistic Context)

The following table summarizes how core Avi objects are mapped to FortiADC and how the migration tool handles each transition.

| Avi Object Category | Avi Source Object | FortiADC Equivalent | Tool Action |
| :--- | :--- | :--- | :--- |
| **Infrastructure** | Tenant | VDOM | **V-A-N-R Mapping**: Maps Avi tenants to FortiADC VDOMs (1:1, N:1 merge, or root). |
| **L7/L4 Traffic** | Virtual Service | Virtual Server | **Automated Translation**: Full payload generation including port, protocol, and profile attachment. |
| **Addressing** | VS VIP | VIP | **Extraction**: Pulls IP/Subnet from VSVIP inventory and associates with the Virtual Server. |
| **Backend** | Pool | Real Server Pool | **Flattening**: Translates pools and unrolls child servers into individual Real Server objects. |
| **Availability** | Health Monitor | Health Check | **Heuristic Mapping**: Converts Avi monitor types (HTTP, TCP, etc.) into FortiADC equivalents. |
| **Security** | SSL Cert / Key | SSL Certificate | **Sanitization**: Maps certificates while flagging missing private keys for manual HSM injection. |
| **Policy** | SSL/App Profile | Profile | **Profile Alignment**: Aligns ciphers, timeouts, and session behaviors with target platform defaults. |
| **Logic** | DataScript | Scripting / LUA | **Manual Flagging**: Identifies complex logic and provides an "LLM Pack" for manual conversion. |

---

## 3. The Object Lifecycle: Through the Decision Gates

The migration follows a structured four-gate workflow. Each gate represents a specialized "checkpoint" for the configuration objects.

### Phase 1: Import & Discovery (The Intake)
*   **Action**: The tool ingests a raw Avi JSON snapshot.
*   **Object Treatment**: 
    *   **Classification**: Objects are grouped into "Pipeline-Critical" (VS, Pools), "Context" (Network, VRF), and "Noise" (System logs).
    *   **Normalization**: Strips Avi-specific metadata (UUIDs, ref-links) to create a clean baseline.
*   **Gate Result**: A "Clean Snapshot" and an inventory of all discovered assets.

### Phase 2: Analysis Triage (The Filtering)
*   **Action**: Dependency graph resolution.
*   **Object Treatment**: 
    *   **Dependency Resolution**: If you migrate a Virtual Service, the tool automatically "drags in" its associated Pool, Certificates, and Profiles.
    *   **Triage Decisions**: Operators decide whether to **Include**, **Exclude**, or **Defer** objects.
    *   **Complexity Scoring**: Objects are scored based on compatibility (e.g., a simple TCP VS is "Low Complexity," while a VS with WAF and DataScripts is "High Complexity").
*   **Gate Result**: A finalized "Scope of Work" approved by the network architect.

### Phase 3: Transform Approval (The Infrastructure)
*   **Action**: V-A-N-R (VDOM-App-Network-Route) Orchestration.
*   **Object Treatment**: 
    *   **VDOM Mapping**: This is where the Avi "Tenant" becomes a FortiADC "VDOM." The tool detects name collisions (e.g., if two tenants both have a pool named `web_pool`).
    *   **Manual Overrides**: Operators can inspect the generated JSON and apply "Surgical Overrides" before the config is finalized.
    *   **Configuration Generation**: The tool runs the final transformation, producing a target-ready JSON payload.
*   **Gate Result**: A validated FortiADC configuration file (`fortiadc-config.json`).

### Phase 4: Deploy & Execution (The Implementation)
*   **Action**: Governance-checked API deployment.
*   **Object Treatment**: 
    *   **Dry Run**: The tool pushes the config to FortiADC with `--dry-run` to verify syntax and permission without making changes.
    *   **Separation of Duties (SoD)**: A second operator (Approver) must sign off on the Technical Manifest before live deployment.
    *   **Live Push**: The tool executes REST API calls to provision the new infrastructure.
*   **Gate Result**: Active configuration running on the target FortiADC cluster.

---

## 4. Key Demo Highlights for Stakeholders

1.  **Deterministic Logic**: No "black boxes." Every mapping is trace-able via the UI.
2.  **Conflict Detection**: Automatically flags VS name collisions when merging multiple Avi tenants into a single VDOM.
3.  **Operator-in-the-Loop**: The tool automates the "grunt work" (payload construction) but leaves the "architectural decisions" (mapping/triage) to the human expert.
4.  **Air-Gapped Ready**: Operates entirely within the local environment; no external cloud dependencies for core transformation.
