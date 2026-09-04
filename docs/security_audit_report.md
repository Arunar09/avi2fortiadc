# Enterprise Security Audit Report: AVI → FortiADC Migration Tool

**Status:** ![High Risk](https://img.shields.io/badge/Risk-High-red) (Pending remediation of UI authentication flaws)

## 1. Executive Summary
The AVI to FortiADC Migration Tool is an enterprise-grade migration utility designed for air-gapped security environments. The core transformation engine is robust, deterministic, and includes features for data sanitization and tamper-evident audit trails. However, the accompanying **Operator Console (UI)** has several critical security vulnerabilities—primarily missing authentication on administrative routes and lack of CSRF protection—that MUST be resolved before the tool is deployed in a shared organization environment.

---

## 2. Architecture & Air-Gap Compliance
The tool adheres to strict air-gap design principles:
- **No External Connectivity**: No CDNs, remote fonts, or external API calls (except through configured enterprise gateways).
- **Read-Only Source Access**: Connects to AVI Controller with scoped read-only credentials.
- **Scoped Target Writes**: Connects to FortiADC targeting specific VDOMs.
- **Local Data Model**: All migration state, reports, and knowledge bases are stored locally in the `state/`, `reports/`, and `discovery/` directories.

---

## 3. Governance & Access Control
### 3.1 Role-Based Access Control (RBAC)
The tool implements an identity model using Flask-Login and SQLAlchemy. Defined roles include:
- `admin`: User management and full tool access.
- `approver`: Authorized to sign off on deployments (Separation of Duties).
- `user`: Standard migration operations.

### 3.2 Deployment Sign-off (Four-Eyes Principle)
The tool enforces **Separation of Duties (SoD)** at the Deployment Approval gate. An operator cannot approve their own deployment; a second signature from a user with the `approver` role is required for production execution.

---

## 4. Observations & Critical Vulnerabilities

| ID | Finding | Severity | Description |
|:---|:---|:---|:---|
| **V01** | **Missing Authentication** | **CRITICAL** | Several critical routes (`/decisions/<env>/run-phase/<phase>`, `/api/import/commit`, `/reports/view/...`, `/logs/view/...`) lack the `@login_required` decorator. Unauthenticated users can trigger migration phases or view sensitive logs. |
| **V02** | **Lack of CSRF Protection** | **HIGH** | The Flask application is not configured with CSRF protection (e.g., Flask-WTF). This allows potential Cross-Site Request Forgery attacks against authenticated operators. |
| **V03** | **Permissive CORS Policy** | **MEDIUM** | The `api_import_commit` endpoint reflects `Access-Control-Allow-Origin: *`. In an internal network, this could allow malicious browser-based scripts to inject configuration data into the tool. |
| **V04** | **Default Credentials** | **MEDIUM** | The tool bootstraps a default `admin:admin` account. While convenient for initial setup, this is a risk if not immediately changed upon deployment. |
| **V05** | **Path Traversal Risk** | **LOW** | While `send_from_directory` is used, the existence check `target = reports_dir / filename` could be bypassed with `..` segments to probe for files outside the intended directory. |

---

## 5. Security Boundary Check (LLM Gateway)
The LLM integration is hardened for enterprise use:
- **Sanitization Layer**: All prompts are passed through a regex-based sanitizer in `core/llm_gateway.py` to redact IPs, UUIDs, FQDNs, and credentials.
- **Hash-Only Logging**: Prompt content is NEVER logged. Instead, a SHA-256 hash is recorded for auditability without data exposure.
- **Internal Routing**: Supports "OpsAI" (internal Ollama) or authenticated enterprise proxies only. Direct cloud API access is unsupported.

---

## 6. Audit & Integrity
The tool provides sophisticated audit mechanisms:
- **State Ledger**: Every phase transition is recorded with operator identity and timestamp.
- **Hash Chaining**: Deployment logs (`.jsonl`) can be "chained" (via `migrate.py audit chain`), where each entry includes the hash of the previous one, making tampering detectable.
- **CEF Export**: Logs can be exported to Common Event Format (CEF) for direct ingestion into SIEMs (Splunk, ArcSight).

---

## 7. Recommendations

### 7.1 Immediate (Pre-adoption)
1. **Apply Authentication**: Add `@login_required` or `@roles_required` to all routes in `ui/routes.py` (except `/login` and `/favicon.ico`).
2. **Enable CSRF Protection**: Initialize `flask_wtf.CSRFProtect` in `ui/app.py`.
3. **Restrict CORS**: Limit `Access-Control-Allow-Origin` to specific internal domains or remove the header entirely if not needed.
4. **Enforce Password Change**: Require a password change on first login for the bootstrapped `admin` user.

### 7.2 Enhancement (Post-adoption)
1. **Vault Integration**: Support fetching AVI/FortiADC credentials from HashiCorp Vault or CyberArk instead of `.env` / `config.yaml`.
2. **Path Sanitization**: Explicitly validate that `filename` in file-serving routes does not contain `..` or resolve outside the target directory.
3. **Internal TLS**: Ensure the Flask server is fronted by a reverse proxy (e.g., Nginx) providing TLS, or configure Flask to use an internal certificate.

---
**Audit Performed By:** Senior Enterprise Security Auditor (Antigravity AI)
**Audit Date:** 2026-04-26
