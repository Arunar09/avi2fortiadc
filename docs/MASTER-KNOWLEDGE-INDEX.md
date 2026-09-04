# Master Knowledge Index: Avi to FortiADC Migration

This document serves as the authoritative index and predefined guidance for the Avi-to-FortiADC migration tool. Use this as a first-line reference for mapping logic, operational workflows, and compatibility patterns.

## 1. Operational Pipeline (The 5 Gates)

The migration process is divided into five logical gates to ensure transparency and safety.

| Gate | Purpose | Key Output |
| :--- | :--- | :--- |
| **1. Import Scope** | Define which tenants and Virtual Services to migrate. | `discovery/env.json` |
| **2. Analysis Triage** | Identify unsupported objects and required manual actions. | `state/env-ledger.json` |
| **3. Transform Approval** | Review the generated FortiADC API payloads (V-A-N-R). | `manifests/env/` |
| **4. Deployment** | Push configuration to FortiADC (Dry-run by default). | FortiADC API calls |
| **5. Post-Migration** | DNS cutover and verification. | Success Report |

---

## 2. Core Object Mapping Strategy

The tool uses a deterministic mapping logic for standard Avi objects.

### Virtual Services (VS)
- **Avi**: `VirtualService` + `VsVip`
- **FortiADC**: `load_balance_virtual_server`
- **Logic**: Combines IP/Port from VsVip with profiles and policies from VS.

### Pools & Members
- **Avi**: `Pool` + `Server`
- **FortiADC**: `load_balance_pool` + `load_balance_real_server`
- **Logic**: Maps LB algorithms (Round Robin, Least Conn) and ensures member weights are ≥ 1.

### Health Monitors (HM)
- **Avi**: `HealthMonitor`
- **FortiADC**: `load_balance_health_check`
- **Constraint**: Timeout MUST be less than Interval. The tool automatically adjusts this (Timeout = Interval - 1).

### SSL & Certificates
- **Avi**: `SSLKeyAndCertificate`
- **FortiADC**: `system_certificate_local`
- **Constraint**: HSM-backed (non-exportable) keys cannot be migrated automatically.

---

## 3. Compatibility Patterns (The P-Series)

Common issues flagged during the **Analysis Triage** gate:

- **P01 (Weight 0)**: Avi Pool member weight 0 (disabled). Mapped to Weight 1 + 'Status: Disable' in FortiADC.
- **P02 (Timeout Constraint)**: HM Timeout >= Interval. Auto-corrected.
- **P03 (DataScript)**: Lua scripts detected. **Requires Manual Conversion** to FortiADC features.
- **P04 (Non-Exportable Cert)**: HSM/TPM backed key. **Requires Manual Import** to FortiADC.
- **P05 (Unsupported Cipher)**: Legacy cipher detected. Mapped to nearest modern equivalent.
- **P06 (WAF Policy)**: Complex WAF rules. Requires manual verification of rule parity.
- **P07 (GSLB Usage)**: GSLB objects detected. **Out of Scope** for this tool.
- **P08 (Advanced Persistence)**: Cookie-hash or custom persistence. Mapped to standard cookie insertion.

---

## 4. Troubleshooting FAQ

### Q: Why is my certificate showing "Zero" in the Transform gate?
**A**: Ensure the certificate was included in the **Import Scope** gate. If it's an HSM-backed cert, it is excluded by default as it cannot be exported. Check the "Suppressed Objects" list in the Analysis gate.

### Q: Why do I only see 8 tenants when Avi has 50+?
**A**: The tool filters for "Landing Zones"—tenants that actually contain Virtual Services. Purely administrative or empty tenants are suppressed to reduce noise.

### Q: How do I override a specific mapping?
**A**: In the **Transform Approval** gate, select the **Override** action for the object. This opens a JSON editor where you can manually adjust the FortiADC payload before deployment.

---

## 5. RAG & AI Assistant Guidance

The ✨ button provides context-aware insights. To get the most out of the AI:
1. **Be Specific**: Instead of "How do I fix this?", ask "How do I map an Avi DataScript that does path-based routing?"
2. **Save Experiences**: When the AI provides a verified solution, click **Save to Knowledge Base**. This permanently "teaches" the tool that solution for future migrations.
3. **Check Sources**: Always review the "Sources" list in the AI drawer to see which documentation or ledger entry the advice came from.
