# AVI → FortiADC Migration Tool

**Enterprise-grade, air-gapped, fully deterministic migration framework.**  
Migrate load balancer configuration from AVI Networks (VMware NSX Advanced Load Balancer)
to FortiADC — safely, auditably, and without requiring any LLM or internet connection.

---

## What this tool does

| Capability | Detail |
|---|---|
| **Discovery** | Reads all AVI configuration via read-only REST API. Builds a complete dependency graph including runtime health state. |
| **Analysis** | Classifies every object as AUTO / WARN / MANUAL / BLOCKED. Runs 8 deterministic risk pattern detectors. Scores overall migration complexity 1–10. |
| **Transformation** | Translates AVI API payloads to FortiADC REST API format. All mappings are explicit, auditable lookup tables — no inference. |
| **Deployment** | Deploys to FortiADC in dependency order. Idempotent — safe to re-run. Dry-run by default. Governance-gated for live execution. |
| **Validation** | Pre-deployment reachability and conflict checks. Post-deployment health verification. Field-level config drift detection. |
| **Offline RAG** | SQLite-backed knowledge base with TF-IDF + BM25 retrieval. Query docs, migration learnings, and object mappings without internet. |
| **LLM Advisory** | Optional, modular, advisory-only. Routes through an internal gateway — no direct cloud API connections. Disable with one config line. |
| **Audit** | Every action logged with operator identity and timestamp. SHA-256 hash chain on log files. CEF export for SIEM ingestion. |

---

## Quick start

```bash
# 1. Install — offline, no internet required
bash install.sh

# 2. Configure
cp config.example.yaml config.yaml
# Edit: AVI controller URL, FortiADC host, credentials, environment names

# 3. Launch guided wizard
python3 wizard.py
```

The wizard walks through every phase with explanations, typed confirmations,
and resumable state. No CLI flags to remember.

---

## CLI reference

```bash
# Discovery
python3 migrate.py discover         --env Tenant-Dev-B
python3 migrate.py import-avi-json  --input normalized-discovery.json --env Tenant-Dev-B

# Analysis and transformation
python3 migrate.py analyse          --input discovery/Tenant-Dev-B.json
python3 migrate.py transform        --input discovery/Tenant-Dev-B.json

# Deployment
python3 migrate.py deploy           --fortiadc-config fortiadc/Tenant-Dev-B-config.json \
                                    --env Tenant-Dev-B --dry-run
python3 migrate.py deploy           --fortiadc-config fortiadc/Tenant-Dev-B-config.json \
                                    --env Tenant-Dev-B --execute

# Cutover and rollback
bash scripts/dns-cutover.sh --env Tenant-Dev-B --dry-run
bash scripts/dns-cutover.sh --env Tenant-Dev-B --execute
bash scripts/rollback.sh    --env Tenant-Dev-B --execute

# Operational checks
python3 migrate.py ops-check        --env Tenant-Dev-B

# Knowledge base
python3 migrate.py rag index
python3 migrate.py rag search       --query "DataScript migration"
python3 migrate.py rag ask          --query "How do I handle HSM certificates?"

# LLM assistance packs (sanitized — safe to share)
python3 migrate.py llm-pack         --type full        --input discovery/Tenant-Dev-B.json
python3 migrate.py llm-pack         --type datascript  --input discovery/Tenant-Dev-B.json \
                                    --name my-rate-limit-script

# Audit
python3 migrate.py audit chain      --log logs/Tenant-Dev-B-deploy.jsonl
python3 migrate.py audit verify     --log logs/Tenant-Dev-B-deploy.jsonl
python3 migrate.py audit cef        --log logs/Tenant-Dev-B-deploy.jsonl --output siem.cef
```

---

## Output files

| Path | Content |
|---|---|
| `discovery/<env>.json` | Complete AVI config snapshot — raw, unmodified |
| `reports/<env>-analysis.html` | Human-readable migration analysis report |
| `reports/<env>-analysis.md` | Markdown version for Change Request attachment |
| `reports/<env>-sanitized.txt` | Sanitized pack — safe to paste into any LLM |
| `fortiadc/<env>-config.json` | FortiADC API payloads — ready to deploy |
| `state/<env>-ledger.json` | Phase state, unsupported items, audit trail |
| `state/<env>-governance.json` | Change Request and approval records |
| `state/rag_index.db` | SQLite RAG knowledge base |
| `logs/<env>-<phase>.jsonl` | Structured event log with SHA-256 hash chain |

---

## Message severity

| Level | Meaning | Required action |
|---|---|---|
| `INFO` | Success | None |
| `WARN` | Completed with caveats | Review before proceeding |
| `ERROR` | Failed, non-blocking | Fix after migration |
| `CRITICAL` | Blocking — cannot proceed | Fix before deploying |
| `MANUAL` | Cannot be automated | Human action required before deploy |

---

## Extending the tool

Adding support for a new AVI object type requires three steps only:

1. Add a **collector** in `collectors/` (inherits `BaseCollector`)
2. Add a **transformer** in `transformers/` (inherits `BaseTransformer`)
3. Register both in `registry.py`

No other files need changing.

---

## Documentation

| Document | Purpose |
|---|---|
| [`docs/USER-GUIDE.md`](docs/USER-GUIDE.md) | Complete installation and usage guide |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Technical architecture, RAG design, data model |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Security controls, sanitization, credential handling |

---

## Requirements

- Python 3.8 or later
- `requests`, `pyyaml` (bundled in `vendor/` for air-gapped install)
- Optional: Docker (for local test environment with mock servers)
- Optional: Ollama (for local LLM advisory via OpsAI mode)

---

## License

This project is licensed under the **Apache License 2.0** - see the [LICENSE](LICENSE) file for details.

Copyright (c) 2026 Arunar09

