# Security
## AVI → FortiADC Migration Tool — Security Controls Reference

---

## 1. Security model summary

The tool is designed for **enterprise air-gapped environments** where:
- Internet access is restricted or prohibited
- Credentials must never leave the local environment
- Every action must be auditable
- Migration decisions must be deterministic and human-approved

The security model has five layers: access control, data sanitization, audit,
governance, and LLM boundary enforcement.

---

## 2. Access control

### 2.1 AVI (source system) — read-only

| Control | Implementation |
|---|---|
| Minimum role | `Application-Viewer` — read-only, no write permissions at any point |
| Scope | Scoped to the specific tenant being migrated (not system admin) |
| API methods used | `GET` and `POST /login` only. No `PUT`, `PATCH`, or `DELETE` methods exist in `core/avi_client.py` |
| Verification | After a discovery run, the AVI audit log shows only `GET` events for the service account |

**Verification command:**
```bash
# Packet capture confirms read-only behaviour
sudo tcpdump -i any -n host <avi-controller-ip> -w /tmp/cap.pcap &
python3 migrate.py discover --env Tenant-Dev-B
kill %1
# Inspect: all requests should be GET methods only
```

### 2.2 FortiADC (target system) — scoped write

| Control | Implementation |
|---|---|
| Minimum rights | Admin on target VDOM only — not global admin |
| VDOM scoping | Every API request includes `?vdom=<vdom>` — enforced at FortiADC API level |
| Dry-run | Deploy defaults to dry-run — `--execute` required explicitly |
| Governance gate | `--execute` requires CR ID, SoD check, and maintenance window |

### 2.3 Credential storage

| Location | Contains | Security |
|---|---|---|
| `config.yaml` | Credentials (optionally) | `chmod 600` enforced by `install.sh` |
| Environment variables | Credentials (preferred) | Injected by secrets manager |
| Log files | Nothing — sanitizer strips all credential patterns | |
| State ledger | Nothing — credentials never written to state | |
| Reports | Nothing — sanitized output only | |
| Git history | Nothing — `config.yaml` is in `.gitignore` | |

**Recommended credential injection pattern:**
```bash
# Do not put passwords in config.yaml
# Use environment variables injected by secrets manager
export MIGRATION_AVI_PASSWORD="$(vault kv get -field=pass secret/avi)"
export MIGRATION_FORTIADC_PASSWORD="$(vault kv get -field=pass secret/fortiadc)"
```

---

## 3. Data sanitization

Every piece of output that could be shared externally passes through the sanitizer.
The same sanitizer runs in `core/events.py` (event log) and `core/llm_gateway.py`
(before any LLM prompt is sent).

### 3.1 Sanitization rules

| Pattern | Replacement | Consistent? |
|---|---|---|
| IPv4 addresses `10.x.x.x` | `[IP_N]` | Yes — same IP always maps to same `[IP_N]` |
| UUIDs `a3f4-bc21-...` | `[UUID_N]` | Yes — consistent within one block |
| FQDNs with 3+ labels | `[HOST_N]` | Yes — consistent within one block |
| `"password": "value"` | `"[CREDENTIAL]": "[REDACTED]"` | Always |
| `Bearer eyJhb...` | `Bearer [TOKEN_REDACTED]` | Always |

### 3.2 Sanitized outputs

| File | Sanitized? | Purpose |
|---|---|---|
| `reports/<env>-sanitized.txt` | Yes | LLM-safe export — safe to share externally |
| `reports/<env>-llm-pack-*.txt` | Yes | External LLM assistance packs |
| All LLM prompts | Yes | Enforced before any gateway call |
| JSONL event logs | Partial | Raw messages may contain object names; `sanitized` field is fully clean |

### 3.3 What is never sanitized (intentionally internal)

- `discovery/<env>.json` — contains real IPs and hostnames (internal only)
- `fortiadc/<env>-config.json` — contains real VIPs (internal only)
- `state/<env>-ledger.json` — contains operator names and timestamps (internal only)

These files must stay on the jump host and must never be shared externally.

---

## 4. Audit controls

### 4.1 Structured event logging

Every operation emits a structured event to `logs/<env>-<phase>.jsonl`:

```json
{
  "timestamp":   "2026-04-15T14:32:11Z",
  "level":       "INFO",
  "phase":       "DEPLOY",
  "object_type": "virtual_server",
  "object_name": "vs-api-prod",
  "operator":    "engineer1",
  "message":     "Deployed virtual_server/vs-api-prod",
  "detail":      {"path": "load_balance/virtual_server", "dry_run": false},
  "sanitized":   "[INFO] [DEPLOY]\nType: virtual_server\n..."
}
```

### 4.2 Hash chain (tamper-evident)

```bash
# Add SHA-256 hash chain to a log file
python3 migrate.py audit chain --log logs/Tenant-Dev-B-deploy.jsonl

# Verify chain integrity — detects modification, deletion, or reordering
python3 migrate.py audit verify --log logs/Tenant-Dev-B-deploy.jsonl
```

Each entry's `prev_hash` field contains the SHA-256 of the previous entry.
Any modification to any entry breaks the chain from that point forward.

### 4.3 SIEM export

```bash
# Export to CEF (Common Event Format) for Splunk, ArcSight, ELK
python3 migrate.py audit cef \
  --log logs/Tenant-Dev-B-deploy.jsonl \
  --output logs/Tenant-Dev-B-deploy.cef
```

The CEF export maps migration events to standard SIEM fields:
`deviceAction`, `deviceCustomString*`, `msg`, `duser`, `cs1` (environment).

### 4.4 What is always recorded

| Event | Always logged | Operator identity | Hash-chained |
|---|---|---|---|
| Phase start/end | Yes | Yes | Yes |
| Every deployed object | Yes | Yes | Yes |
| Every failed object | Yes | Yes | Yes |
| Governance decisions | Yes | Yes | Yes (`state/<env>-governance.json`) |
| Governance overrides | Yes (indelible) | Yes | Yes |
| Lock acquire/release | Yes | Yes | Yes |
| LLM calls | Prompt hash only | Yes | Yes |

---

## 5. Governance controls

### 5.1 Change Request enforcement

Every live deployment (`--execute`) requires a Change Request ID.
Format is validated against configured patterns:

```yaml
# config.yaml
governance:
  cr_patterns:
    - "^CHG\\d{7}$"      # ServiceNow format: CHG0001234
    - "^CR-\\d{4}-\\d+"  # Generic: CR-2026-001
    - "^[A-Z]+-\\d+$"    # Jira: INFRA-1234
```

The CR ID is recorded in `state/<env>-governance.json` alongside the
operator identity, approver identity, and maintenance window timestamps.

### 5.2 Segregation of Duties

The operator running the deployment (`$USER`) must differ from the approver
recorded in the governance record. This is enforced in code via `governance.py::validate_sod()`.

### 5.3 Concurrency lock

Only one deployment can run per environment at a time.
The file-based lock (`state/<env>-deploy.lock`) contains operator, PID, hostname,
and start time. Stale locks (older than 4 hours) are automatically broken with a warning.

---

## 6. LLM security boundary

### 6.1 No direct cloud connections

The tool never connects to public LLM APIs (OpenAI, Anthropic, etc.).
All LLM requests route through an internal gateway:

```
Tool → LLM Gateway (internal) → [Ollama | Enterprise Proxy | API GW]
                                      ^
                            Never: direct public API
```

### 6.2 API key handling

API keys are **never** stored in `config.yaml`. The config stores the
environment variable **name** only:

```yaml
llm_gateway:
  apigw_key_env: "MIGRATION_LLM_KEY"   # Variable name — not the key
```

The key is read at runtime from the environment, where it should be
injected by a secrets manager (Vault, CyberArk, AWS Secrets Manager, etc.).

### 6.3 Prompt hashing

The raw prompt is never stored. The SHA-256 hash of the sanitized prompt
is logged for audit purposes. The same hash is used for response caching.

### 6.4 Advisory only

LLM responses are never automatically applied. Every LLM output is returned
to the engineer for review. The migration pipeline does not accept LLM
responses as input for any decision.

### 6.5 Disabling LLM completely

```yaml
# config.yaml — disables all LLM features with one setting
llm_gateway:
  enabled: false
```

When disabled:
- All `enrich_*()` functions return immediately with deterministic results
- `rag ask` falls back to extractive summaries (no LLM augmentation)
- No network calls are made related to LLM
- The tool behaves identically to versions before LLM was added

---

## 7. Static analysis and dependency security

```bash
# SAST scan
pip install bandit
bandit -r . -ll -x tests/

# Dependency CVE check
pip install safety
safety check -r requirements.txt
```

Expected findings: no HIGH severity SAST findings. Known false positives
in `subprocess` usage are limited to the UI Quick Actions handler, which
only invokes a fixed allow-list of migration tool commands.

---

## 8. Air-gap compliance summary

| Requirement | Status | Implementation |
|---|---|---|
| No runtime internet access | Compliant | Zero external calls in core pipeline |
| No CDN assets | Compliant | All static assets in `static/` directory |
| All dependencies bundled | Compliant | `vendor/` directory, `install.sh --bundle` |
| No external fonts | Compliant | System font stack only in `static/css/app.css` |
| No telemetry or beacons | Compliant | No analytics, update checks, or phone-home |
| LLM routes internal only | Compliant | Gateway enforced in `llm_gateway.py` |
| Secrets never transmitted | Compliant | Sanitizer applied before every external call |

---

## 9. Data classification

| Data | Classification | Storage | Shareable |
|---|---|---|---|
| `discovery/<env>.json` | Internal — Restricted | Jump host only | No |
| `fortiadc/<env>-config.json` | Internal — Restricted | Jump host only | No |
| `state/<env>-ledger.json` | Internal — Restricted | Jump host only | No |
| `config.yaml` | Internal — Confidential | Jump host, chmod 600 | Never |
| `reports/<env>-analysis.html` | Internal — Sensitive | Jump host / CR attachment | Internal only |
| `reports/<env>-sanitized.txt` | Internal — Safe | Jump host / LLM input | Safe to share |
| `logs/<env>-*.jsonl` | Internal — Restricted | Jump host / SIEM | SIEM only |
| `logs/<env>-*.cef` | Internal — SIEM | SIEM system | SIEM only |

---

## 10. Incident response

If a credential is believed to have been exposed:

1. Rotate the exposed credential immediately (AVI, FortiADC, or Infoblox account)
2. Inspect `logs/` for unexpected use of that credential using operator field
3. Run `migrate.py audit verify` on all log files to confirm no tampering
4. Review `state/<env>-governance.json` for any unexpected deployments
5. Re-examine `config.yaml` permissions and ensure it is not in git history
