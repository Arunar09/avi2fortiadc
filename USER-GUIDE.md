# User Guide
## AVI → FortiADC Migration Tool — Complete Reference

---

## 1. Prerequisites

Before running the tool, ensure the following are in place.

### 1.1 System requirements

| Requirement | Minimum | Recommended |
|---|---|---|
| Python | 3.8 | 3.11+ |
| RAM | 2 GB | 4 GB |
| Disk | 500 MB | 2 GB (for large environments) |
| OS | Linux / macOS / Windows (WSL2) | Linux (jump host) |
| Network | Access to AVI controller and FortiADC | Air-gapped supported |

### 1.2 Access requirements

**AVI (source system):**
- Read-only service account with `Application-Viewer` role
- Account scoped to the specific tenant being migrated (not system-wide admin)
- Verify: `curl -k -u svc-reader:pass https://avi-controller.local/api/virtualservice?page_size=1`

**FortiADC (target system):**
- Service account with admin rights on the target VDOM only
- REST API enabled: System → Admin → REST API access
- Verify: `curl -k -u svc-writer:pass https://fortiadc.local/api/system/status`

**Infoblox (DNS cutover only):**
- Account with `Record:A` modify rights on migration zones only
- WAPI v2.10+ enabled

### 1.3 What the tool does NOT need

- Internet access (fully air-gapped capable)
- Write access to AVI at any point
- Admin-level AVI account
- Any LLM or AI service (optional, not required)

---

## 2. Installation

```bash
# Clone or extract the tool package
cd avi-fortiadc-migration

# Default install — uses vendor/ directory (no internet)
bash install.sh

# Verify install
bash install.sh --check

# Internet machine: bundle dependencies for transfer to air-gapped host
bash install.sh --bundle
# Transfer the zip, then on air-gapped host:
bash install.sh
```

---

## 3. Configuration

```bash
cp config.example.yaml config.yaml
chmod 600 config.yaml      # credentials file — restrict permissions
```

### 3.1 Minimum required fields

```yaml
avi:
  controller:   "https://avi-controller.company-name.local"
  username:     "svc-migration-reader"
  password:     ""           # Set here or via MIGRATION_AVI_PASSWORD env var
  api_version:  "22.1.5"

fortiadc:
  host:         "https://fortiadc.company-name.local"
  username:     "svc-migration-writer"
  password:     ""           # Set here or via MIGRATION_FORTIADC_PASSWORD env var
  vdom:         "root"

environments:
  - name:           "Tenant-Dev-B"
    avi_tenant:     "Dev-B"
    fortiadc_vdom:  "DEV-B"
    priority:       1        # Migrate first — lowest risk

  - name:           "Tenant-Prod-A"
    avi_tenant:     "Prod-A"
    fortiadc_vdom:  "PROD-A"
    priority:       5        # Migrate last
```

### 3.2 LLM gateway (optional)

LLM features are fully optional. The tool is deterministic without them.
To disable completely, leave `llm_gateway.enabled: false` (the default).

```yaml
llm_gateway:
  enabled:         false     # Default: off — tool runs deterministically
  mode:            opsai     # opsai | proxy | apigw | disabled
  endpoint:        "http://ollama.company-name.local:11434"
  model:           "mistral:7b"
```

See [Section 10: LLM Gateway](#10-llm-gateway) for full configuration options.

### 3.3 Security note

`config.yaml` is listed in `.gitignore` and must never be committed.
The tool refuses to start if the file has world-readable permissions.

---

## 4. Running the wizard (recommended)

The wizard is the primary interface. It handles all phases with explanations,
typed confirmations, and resumable progress state.

```bash
python3 wizard.py
```

What the wizard does:

1. Checks `config.yaml` is present and valid
2. Lists available environments and asks which to migrate
3. Walks through each phase with explanation before execution
4. Requires typing the environment name to confirm live deployment
5. Tracks progress in `state/<env>-ledger.json` — resumes after interruption
6. Generates rollback script immediately after deployment

---

## 5. Pipeline phases in detail

### Phase 1 — Discover

Connects to AVI via read-only REST API and snapshots all configuration.

```bash
python3 migrate.py discover --env Tenant-Dev-B
# Output: discovery/Tenant-Dev-B.json
```

What is collected:

| Object type | Detail |
|---|---|
| Virtual Services | All properties + runtime health state |
| Pools | Members + health state per member |
| Health Monitors | All 15 supported types |
| SSL Certificates | Expiry, exportability, HSM detection |
| SSL Profiles | TLS version, cipher configuration |
| Application Profiles | HTTP/TCP/UDP/DNS type and settings |
| Network Profiles | TCP proxy and idle timeout settings |
| Persistence Profiles | Cookie, source-IP, SSL session ID |
| HTTP Policy Sets | Rules, actions, complexity score |
| WAF Policies | CRS group references |
| Auth Profiles | LDAP/SAML integration details |
| DataScript Sets | Full Lua/Python code captured |
| SE Groups | Informational only |
| GSLB Services | Full GSLB topology |
| External Connections | Infoblox, OpenStack, LDAP, SNMP, syslog |

**Alternative: import analyzer-normalized discovery JSON**

```bash
# Normalize raw export in the analyzer first, then import the discovery payload
python3 migrate.py import-avi-json \
  --input normalized-discovery.json \
  --env Tenant-Dev-B
```

### Phase 2 — Analyse

Reads discovery JSON locally (no network calls) and produces:
- Compatibility matrix (AUTO / WARN / MANUAL / BLOCKED per object)
- Dependency graph (VS → pool → cert → health monitor chain)
- 8 risk pattern detections (DataScript concentration, HSM cascade, etc.)
- Migration complexity score (1–10)
- Ordered list of next steps

```bash
python3 migrate.py analyse --input discovery/Tenant-Dev-B.json
# Outputs: reports/Tenant-Dev-B-analysis.html
#          reports/Tenant-Dev-B-analysis.md
#          reports/Tenant-Dev-B-sanitized.txt
```

**Reading the HTML report:** Open `reports/Tenant-Dev-B-analysis.html` in a browser.
The report shows the complexity score, all MANUAL items with resolution guidance,
all patterns detected with recommendations, and the ordered next-steps list.

### Phase 3 — Transform

Converts the discovery JSON to FortiADC REST API payloads.
No network calls. Pure, deterministic, reproducible.

```bash
python3 migrate.py transform --input discovery/Tenant-Dev-B.json
# Output: fortiadc/Tenant-Dev-B-config.json
```

Objects that cannot be automatically translated are flagged MANUAL and
excluded from the output with a detailed explanation of what is needed.

Running transform twice on the same input always produces identical output.

### Phase 4 — Deploy (dry-run first, always)

```bash
# Always dry-run first
python3 migrate.py deploy \
  --fortiadc-config fortiadc/Tenant-Dev-B-config.json \
  --env Tenant-Dev-B \
  --dry-run

# After reviewing dry-run output: live deployment
# Requires: Change Request ID, different operator as approver
python3 migrate.py deploy \
  --fortiadc-config fortiadc/Tenant-Dev-B-config.json \
  --env Tenant-Dev-B \
  --execute
```

Deployment is **idempotent** — objects that already exist in FortiADC are updated,
not duplicated. Deployment order respects dependencies:
SSL certs → health checks → real servers → pools → persistence → virtual servers.

### Phase 5 — Parallel run (7 days minimum)

AVI continues serving all traffic. FortiADC is validated in parallel.

```bash
# Run daily during the parallel run window
bash scripts/parallel-run-check.sh Tenant-Dev-B
```

### Phase 6 — DNS cutover

Switches DNS A-records from AVI VIPs to FortiADC VIPs via Infoblox WAPI.

```bash
# Dry-run mandatory before live cutover
bash scripts/dns-cutover.sh --env Tenant-Dev-B --dry-run
bash scripts/dns-cutover.sh --env Tenant-Dev-B --execute
```

The script: backs up original DNS records → lowers TTL to 60s → updates records →
verifies new IPs resolve correctly.

### Phase 7 — Rollback (any time)

```bash
bash scripts/rollback.sh --env Tenant-Dev-B --dry-run
bash scripts/rollback.sh --env Tenant-Dev-B --execute
```

Rollback: disables FortiADC virtual servers → restores Infoblox DNS from backup →
verifies AVI is responding. AVI was never modified and is always available.

---

## 6. Operational health checks

```bash
python3 migrate.py ops-check --env Tenant-Dev-B
```

Runs six checks without modifying anything:
- **HealthWatcher** — pool member health, trend detection
- **CapacityAdvisor** — VS/connection/SSL utilisation vs limits (warns at 70% and 85%)
- **PoolSyncAdvisor** — compares AVI pool members vs FortiADC (cloud connector replacement)
- **DriftGuard** — field-level config drift since last transform
- **DataScriptAdvisor** — classifies DataScripts as SIMPLE/MEDIUM/COMPLEX
- **AlertBridge** — normalises signals to P1-P4 severity

---

## 7. Audit commands

```bash
# Add SHA-256 hash chain to a log file (tamper-evident)
python3 migrate.py audit chain --log logs/Tenant-Dev-B-deploy.jsonl

# Verify the hash chain integrity
python3 migrate.py audit verify --log logs/Tenant-Dev-B-deploy.jsonl

# Export to CEF format for SIEM (Splunk, ArcSight, ELK)
python3 migrate.py audit cef \
  --log logs/Tenant-Dev-B-deploy.jsonl \
  --output logs/Tenant-Dev-B-deploy.cef

# Summarise log by level and phase
python3 migrate.py audit summary --log logs/Tenant-Dev-B-deploy.jsonl
```

---

## 8. LLM assistance packs

Generate a sanitized block safe to paste into any LLM — all IPs, hostnames,
UUIDs, and credentials replaced with consistent placeholders before output.

```bash
# Full migration analysis (for any LLM)
python3 migrate.py llm-pack \
  --type full \
  --input discovery/Tenant-Dev-B.json

# DataScript translation request
python3 migrate.py llm-pack \
  --type datascript \
  --input discovery/Tenant-Dev-B.json \
  --name rate-limit-api-script

# What to do next
python3 migrate.py llm-pack \
  --type next_steps \
  --input discovery/Tenant-Dev-B.json
```

Each pack includes a structured prompt telling the LLM exactly what analysis
to perform. The engineer validates all suggestions before implementation.

---

## 9. Offline testing (laptop, 12 GB RAM)

A complete test environment runs without AVI or FortiADC access.

**Option A — No Docker (5 minutes):**

```bash
pip install pytest pytest-mock pyyaml requests

# Run full test suite
python3 -m pytest tests/ -v

# Run full pipeline against fixture data
python3 migrate.py analyse  --input tests/fixtures/dev_b_discovery.json
python3 migrate.py transform --input tests/fixtures/dev_b_discovery.json
```

Expected from fixture: 4 VS → 3 auto-translated + 1 MANUAL (DataScript);
2 SSL certs → 1 translated + 1 BLOCKED (HSM); all pool members in `real_servers`.

**Option B — Docker (10 minutes, full end-to-end):**

```bash
cd tests
docker compose up -d
# Wait ~15 seconds for health checks

# Configure tool to point at mock servers
cp config.example.yaml config.yaml
# Edit: controller to http://localhost:8080, fortiadc to http://localhost:9090

# Run full discover → transform → deploy pipeline
python3 ../migrate.py discover --env Tenant-Dev-B
python3 ../migrate.py analyse  --input discovery/Tenant-Dev-B.json
python3 ../migrate.py transform --input discovery/Tenant-Dev-B.json
python3 ../migrate.py deploy   --fortiadc-config fortiadc/Tenant-Dev-B-config.json \
                                --env Tenant-Dev-B --dry-run
```

See `tests/LOCAL-TEST-SETUP.md` for full demo pitch setup instructions.

---

## 10. LLM Gateway

The LLM gateway is modular and can be enabled or disabled without touching
any pipeline code. All four modes degrade gracefully — the tool always runs
deterministically regardless of LLM availability.

### 10.1 Gateway modes

| Mode | What happens | Use case |
|---|---|---|
| `disabled` | No LLM calls. Tool runs fully deterministically. | Default. Air-gap without LLM. |
| `opsai` | Connects to internal Ollama instance at configured endpoint. | Air-gapped with local LLM. |
| `proxy` | Routes via enterprise HTTP proxy to approved external LLM. | Enterprise with internet proxy. |
| `apigw` | Routes via internal API Gateway (Kong, AWS APIM, etc.). | Enterprise with API gateway. |

### 10.2 Enable OpsAI mode (Ollama)

```yaml
# config.yaml
llm_gateway:
  enabled:   true
  mode:      opsai
  endpoint:  "http://ollama.company-name.local:11434"
  model:     "mistral:7b"
```

```bash
# On the Ollama host, pull the model first
ollama pull mistral:7b
```

### 10.3 Enable enterprise proxy mode

```yaml
llm_gateway:
  enabled:         true
  mode:            proxy
  endpoint:        "https://approved-llm-api.company-name.local/v1/chat/completions"
  proxy_url:       "http://squid.company-name.local:3128"
  apigw_key_env:   "MIGRATION_LLM_KEY"   # env var name — never hardcode the key
```

```bash
# Inject key via secrets manager or environment
export MIGRATION_LLM_KEY="$(vault kv get -field=key secret/llm-api)"
```

### 10.4 What LLM is used for

| Function | How triggered | LLM role |
|---|---|---|
| DataScript classification | `migrate.py analyse` | Explains what script does, suggests FortiADC approach |
| Migration report enrichment | `migrate.py analyse` | Plain-English executive summary |
| Unsupported item guidance | `migrate.py analyse` | Step-by-step resolution suggestions |
| Drift explanation | `migrate.py ops-check` | Explains why config changed |
| RAG Q&A | `migrate.py rag ask` | Augments retrieved KB chunks with natural answer |
| LLM packs | `migrate.py llm-pack` | Content is sanitized; LLM is external consumer |

### 10.5 What LLM is never used for

- Migration decisions (all AUTO/WARN/MANUAL/BLOCKED classifications are deterministic)
- Approving deployments
- Accessing AVI or FortiADC API
- Generating executable scripts that are auto-deployed
- Storing or echoing credentials

---

## 11. Knowledge Base and RAG

The tool includes an offline knowledge base using SQLite with TF-IDF + BM25 retrieval.
It works without any LLM — retrieval is deterministic.

### 11.1 Building the index

```bash
# Index all sources
python3 migrate.py rag index

# Index specific sources only
python3 migrate.py rag index --sources docs,ledger

# Incremental update (preserve existing chunks)
python3 migrate.py rag index --no-rebuild

# Check index statistics
python3 migrate.py rag stats
```

### 11.2 Sources indexed

| Source | Content | When to update |
|---|---|---|
| `docs` | All markdown files in `docs/` | After doc changes |
| `ledger` | Unsupported items + resolutions from `state/*.json` | After each analysis |
| `discovery` | Object type summaries from `discovery/*.json` | After discovery |
| `mappings` | Avi→FortiADC mapping tables from `transformers/mappings.py` | After mapping changes |
| `builtin` | Bundled migration knowledge | Shipped with tool |

### 11.3 Querying

```bash
# Search without LLM (pure retrieval)
python3 migrate.py rag search --query "DataScript rate limiting FortiADC"
python3 migrate.py rag search --query "HSM certificate" --source docs

# Full Q&A (retrieval + optional LLM augmentation)
python3 migrate.py rag ask --query "How do I handle a DataScript that does rate limiting?"
python3 migrate.py rag ask --query "Which VS are BLOCKED?" --env Tenant-Dev-B

# RAG-only mode (no LLM, even if gateway is configured)
python3 migrate.py rag ask --query "What is the rollback procedure?" --no-llm
```

### 11.4 Retrieval pipeline

Query → TF-IDF cosine similarity (broad recall) → BM25 reranking (precision) → MMR deduplication (diversity) → top-K results

The three-stage pipeline ensures results are relevant, precise, and not repetitive.
All computation runs in SQLite — no external dependencies.

---

## 12. Governance controls

Every live deployment requires:

1. **Change Request ID** — format validated (ServiceNow CHG, Jira, or generic)
2. **Segregation of Duties** — the deploying operator cannot be the same person who approved
3. **Maintenance window confirmation** — start/end timestamps recorded
4. **Typed environment name** — must type the exact environment name to proceed

Override is possible with `--override-governance` but is permanently logged with
mandatory written justification.

---

## 13. Migration environment sequence

Migrate environments in order, never in parallel:

| Order | Environment | Min parallel days | Notes |
|---|---|---|---|
| 1st | Dev-B | 7 days | Lowest risk, best test case |
| 2nd | Dev-A | 7 days | Validate parity |
| 3rd | Sandpit | 7 days | Pre-production validation |
| 4th | Prod-B | 14 days | With full monitoring |
| 5th | Prod-A | 14 days | Last — after all others validated |

Never remove AVI config until the parallel run period is complete and
application teams confirm FortiADC is serving traffic correctly.

---

## 14. Troubleshooting quick reference

| Symptom | Likely cause | Fix |
|---|---|---|
| `AviAuthError: authentication failed` | Wrong credentials or expired account | Check config.yaml, verify Avi account active |
| `KeyError: results` during discovery | API version mismatch | Set `api_version` to match your Avi version |
| VS shows DOWN after deploy | Pool members unreachable from FortiADC | Check firewall rules between FortiADC and backends |
| SSL cert import fails | Cert and key from different keypairs | Verify with `openssl verify` |
| `DRIFT DETECTED` before deploy | FortiADC was modified outside this tool | Review drift report or use `--skip-drift-check` |
| LLM calls timeout | Ollama model not loaded / endpoint wrong | Run `ollama pull <model>`, verify endpoint |
| RAG returns no results | Index not built | Run `python3 migrate.py rag index` |

Full troubleshooting guide: `docs/old/TROUBLESHOOTING.md` (archived internal version).
