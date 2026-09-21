# Avi → FortiADC Migration Tool — Security

The primary goal of this tool is to provide a secure and auditable path for migrating load balancer configurations in highly regulated, air-gapped environments.

---

## 1. Data Protection

### 1.1 Local Storage
- Configuration data, discovery snapshots, and reports are stored locally in the workspace.
- `config.yaml` is explicitly excluded from version control via `.gitignore`.
- The core migration pipeline does not require cloud services or automatic cloud upload.
- If an optional LLM gateway/proxy is configured, sanitized advisory material may leave the local environment according to that gateway's deployment and policy. Review the sanitized pack and gateway configuration before enabling external analysis.

### 1.2 Sanitization
- The `llm-pack` command creates specialized snapshots for external analysis.
- Configured sanitization replaces recognized IPs, hostnames, UUIDs, and secret-like values with deterministic placeholders (e.g., `VIP-1`, `Svr-Name-B`).
- LLM advisory requests should contain only sanitized prompt/context when sanitization is enabled; sanitized output must still be reviewed before external sharing.

---

## 2. Authentication & Authorization

- **Avi Access**: The tool requires a **Read-Only (Viewer)** service account. It never attempts to modify Avi configuration.
- **FortiADC Access**: Deployment requires a service account with **Admin** permissions on the target VDOM.
- **Credential Handling**:
    - Credentials should be provided via the environment variables documented in `config.example.yaml` (for example, `LB_MIGRATION_SOURCE_PASSWORD` and `LB_MIGRATION_TARGET_PASSWORD`) to avoid plaintext storage.
    - If stored in `config.yaml`, the file should be protected with standard OS filesystem permissions.

---

## 3. Governance & Audit

### 3.1 Immutable Logs
Logs are written in JSONL format and include a cryptographic hash chain. This ensures that any tampering with the log file (deletion or modification of past events) is detectable.

### 3.2 Deployment Gates
The tool enforces a human-in-the-loop workflow:
1. **Dry-Run by Default**: Commands that modify FortiADC require an explicit `--execute` flag.
2. **Maintenance Window Check**: The guided wizard requires confirmation that the operator is within an approved window.
3. **CR ID Enforcement**: Deployment events are not logged until a Change Request ID is associated with the environment.

---

## 4. Integrity

- **Deterministic Logic**: All mapping and transformation logic is in clear-text Python files and lookup tables.
- **Dependency Isolation**: All required libraries are vendored or installed locally. No external packages are fetched during the migration run.
