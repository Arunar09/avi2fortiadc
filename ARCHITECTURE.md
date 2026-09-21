# Architecture
## AVI → FortiADC Migration Tool — Technical Design

---

## 1. Design principles

| Principle | Implementation |
|---|---|
| **Deterministic by default** | All migration decisions (AUTO/WARN/MANUAL/BLOCKED) are rule-based. No LLM is required at any pipeline stage. |
| **LLM is optional and modular** | `llm_gateway.py` is the single integration point. Disable with one config line. All functions degrade gracefully. |
| **Air-gap first** | No runtime internet calls. All dependencies bundle in `vendor/`. RAG uses SQLite with stdlib only. |
| **Read-only on source** | The AVI client has no write methods. The tool cannot modify AVI at any point. |
| **Audit everything** | Every operation is logged with operator identity, timestamp, and SHA-256 hash chain. |
| **Dry-run default** | Live deployment requires explicit `--execute` flag and governance gate. |
| **Idempotent deployment** | Deploy checks object existence before create and supports update-on-repeat. Complete idempotency and preservation of unrelated target configuration must be validated against the target release/configuration. |

---

## 2. System context

```
                    ┌─────────────────────────────────────────────────┐
                    │           Migration Tool (this system)           │
                    │                                                   │
  [AVI Controller]  │  discover → analyse → transform → deploy → verify│
  (read-only API) ──┤──────────────────────────────────────────────────┤── [FortiADC]
                    │                                                   │   (write API)
  [AVI JSON export] │  wizard.py (guided)    migrate.py (CLI)          │
  (offline import)  │                                                   │
                    │  ┌──────────┐  ┌─────────────┐  ┌────────────┐  │
                    │  │ RAG / KB │  │ LLM Gateway │  │ Operator   │  │
                    │  │ SQLite   │  │ (optional)  │  │ Console UI │  │
                    │  └──────────┘  └──────┬──────┘  └────────────┘  │
                    └─────────────────────── │ ───────────────────────┘
                                             │
                                    [Internal Ollama │ Enterprise Proxy │ API GW]
                                    (never: direct cloud LLM API)
```

---

## 3. Module structure

```
avi-fortiadc-migration/
├── migrate.py              # CLI entry point — 10 commands
├── wizard.py               # Guided interactive wizard
├── registry.py             # All collectors and transformers registered here
├── config.example.yaml     # Annotated configuration template
│
├── core/                   # Foundation layer
│   ├── avi_client.py       # AVI REST client (read-only, retry, pagination)
│   ├── avi_import.py       # Offline AVI JSON import and normalisation
│   ├── fortiadc_client.py  # FortiADC REST client (write, dry-run mode)
│   ├── base.py             # BaseCollector, BaseTransformer abstract classes
│   ├── events.py           # EventBus, MigrationEvent, sanitizer
│   ├── state_ledger.py     # Persistent phase state and audit trail
│   ├── governance.py       # CR enforcement, SoD, maintenance window
│   ├── lock.py             # File-based concurrency lock (prevents dual-deploy)
│   ├── audit_export.py     # SHA-256 hash chain, CEF/SIEM export
│   ├── intelligence.py     # 8 pattern detectors, complexity scoring, next steps
│   ├── ops_automation.py   # Post-migration health watcher, capacity advisor, drift guard
│   ├── llm_gateway.py      # Enterprise LLM proxy — the single AI integration point
│   └── rag/
│       ├── __init__.py     # Public API: query(), get_retriever(), index_all()
│       ├── document.py     # Document and SearchResult dataclasses
│       ├── vectorstore.py  # SQLite TF-IDF vector store (stdlib only)
│       ├── retriever.py    # TF-IDF → BM25 rerank → MMR dedup pipeline
│       ├── indexer.py      # Document ingestion from 5 source types
│       └── augmentor.py    # Prompt construction for RAG+LLM and RAG-only
│
├── collectors/             # AVI API readers — one file per object type
├── analyzers/              # Compatibility, dependency graph, impact, cert audit
├── transformers/           # AVI payload → FortiADC payload converters
├── validators/             # Pre/post deployment checks, config drift
├── deployers/              # FortiADC deployment with dependency ordering
├── reporters/              # HTML, Markdown, sanitized, rollback outputs
│
├── ui/                     # Operator console (Flask + Jinja2, air-gap safe)
├── services/               # Service layer for the UI (read-only)
├── templates/              # Jinja2 templates (5 tabs)
├── static/                 # Self-contained CSS (no CDN)
│
├── tests/
│   ├── fixtures/           # Sample AVI discovery JSON for offline testing
│   ├── mock_servers/       # Docker-based mock AVI and FortiADC servers
│   └── test_migration_tool.py  # 60+ tests, all offline
│
├── scripts/
│   ├── dns-cutover.sh      # Infoblox WAPI DNS cutover
│   ├── rollback.sh         # FortiADC disable + DNS restore
│   └── parallel-run-check.sh  # Daily parallel validation check
│
└── docs/
    ├── README.md           # Landing page
    ├── USER-GUIDE.md       # This file's companion
    ├── ARCHITECTURE.md     # This document
    ├── SECURITY.md         # Security controls reference
    └── old/                # Archived internal documents (detailed references)
```

---

## 4. Core pipeline data flow

### 4.1 Discovery phase

```
AVI Controller ──[read-only REST GET]──► collectors/*.py
                                              │
                                    Enrichment fields added
                                    (prefixed with _)
                                              │
                                    EventBus emits events
                                    (INFO/WARN/CRITICAL/MANUAL)
                                              │
                                    discovery/<env>.json
                                    (canonical snapshot, immutable)
```

All enrichment fields added by collectors use the `_` prefix convention (e.g. `_vips`, `_health`, `_exportable`) to distinguish tool-added metadata from raw AVI fields.

### 4.2 Analysis phase (local, no network)

```
discovery/<env>.json
        │
        ├── analyzers/dependency_graph.py  → VSProfile per VS (full dep chain)
        ├── analyzers/compatibility.py     → AUTO/WARN/MANUAL/BLOCKED per object
        ├── analyzers/impact.py            → Risk level per VS (LOW/MEDIUM/HIGH)
        ├── analyzers/certificate_audit.py → Cert expiry and exportability audit
        ├── core/intelligence.py           → 8 patterns, score 1-10, next steps
        └── [optional] core/llm_gateway.py → LLM enrichment on findings
                │
        reports/<env>-analysis.html    (human report)
        reports/<env>-analysis.md      (Change Request attachment)
        reports/<env>-sanitized.txt    (LLM-safe export)
```

### 4.3 Transform phase (local, no network)

```
discovery/<env>.json + analyzers output
        │
        ├── transformers/pool.py
        │   ├── HealthCheckTransformer    → load_balance/health_check
        │   ├── SSLCertTransformer        → system/certificate/local
        │   └── PoolTransformer           → load_balance/real_server_pool
        │       └── sub_objects (members) → load_balance/real_server
        ├── transformers/profiles.py
        │   ├── ApplicationProfileTransformer → load_balance/profile/http|tcp|udp
        │   ├── NetworkProfileTransformer     → load_balance/profile/tcp
        │   ├── PersistenceProfileTransformer → load_balance/persistence/*
        │   └── SSLProfileTransformer         → load_balance/profile/client_ssl
        └── VirtualServerTransformer          → load_balance/virtual_server
                │
        fortiadc/<env>-config.json
        (dependency-ordered, idempotent, deploy-ready)
```

Transform is **purely functional** — same input always produces identical output.
All mappings are explicit lookup tables in `transformers/mappings.py`.

### 4.4 Deployment phase

```
fortiadc/<env>-config.json
        │
        ├── governance.py    → CR format check, SoD check
        ├── lock.py          → Acquire file lock (prevent dual-deploy)
        ├── pre_migration.py → VDOM exists? Conflicts? Capacity?
        ├── config_diff.py   → Any drift from last transform?
        │
        ├── fortiadc_deployer.py (DEPLOY_ORDER):
        │   1. ssl_certificates
        │   2. ssl_profiles
        │   3. health_checks
        │   4. real_server_pools
        │   5. real_servers       (pool members)
        │   6. persistence_profiles
        │   7. virtual_servers
        │   (each: exists()? → update : create)
        │
        ├── post_migration.py → VS status, pool member health, cert presence
        ├── audit_export.py  → Hash-chain the event log
        └── lock.py          → Release lock
```

---

## 5. RAG Knowledge Base (LBKB) — Technical Detail

### 5.1 Architecture position

The RAG knowledge base (internally called LBKB — Load Balancer Knowledge Base) sits
beside the migration pipeline. It does not participate in migration decisions.
The pipeline never reads from the KB to decide what to transform or deploy.

```
Migration pipeline ──writes──► LBKB (facts, decisions, resolutions)
                                    │
                         Engineers query LBKB ◄── migrate.py rag ask
                                    │
                         [optional] LLM augments retrieval result
```

### 5.2 Storage

```
state/rag_index.db   (SQLite, stdlib only, air-gap safe)
    │
    ├── chunks table      (id, source_type, title, content, tf_idf_vector JSON)
    ├── index_meta table  (IDF values, document frequencies)
    └── schema_version    (forward compatibility)
```

No external vector database. No embedding model. No neural network.
All retrieval runs with Python stdlib and SQLite.

### 5.3 Retrieval pipeline

```
Query string
    │
    ▼
[Stage 1: TF-IDF cosine similarity]
  - Tokenize query: lowercase, remove stop words, split on hyphens
  - Compute query TF-IDF vector (sparse dict {term: weight})
  - Cosine similarity against all chunks (linear scan, ~10-50ms for <10k chunks)
  - Return top candidates_k = top_k × bm25_multiplier
    │
    ▼
[Stage 2: BM25 reranking]   (k1=1.5, b=0.75)
  - BM25 score on candidate set
  - Improves precision by penalising very long documents
  - Reduces candidates to top_k
    │
    ▼
[Stage 3: MMR deduplication]
  - Maximal Marginal Relevance with configurable diversity weight λ
  - Prevents identical or near-identical chunks in results
  - Default λ=0.5 (balanced relevance and diversity)
    │
    ▼
SearchResult[top_k]
  - chunk (content, title, source_type, metadata)
  - score (float)
  - highlights (keyword-matching sentences)
```

### 5.4 Document sources

| Source type | Input | Chunking |
|---|---|---|
| `docs` | `docs/*.md` markdown files | 800-char overlapping chunks (100-char overlap) |
| `ledger` | `state/*-ledger.json` unsupported + resolved items | One chunk per item |
| `discovery` | `discovery/*.json` object summaries | One chunk per VS/pool/cert |
| `mappings` | `transformers/mappings.py` lookup tables | One chunk per mapping table |
| `builtin` | Bundled migration knowledge (DataScript patterns, FortiADC limits) | Pre-chunked |

### 5.5 LLM augmentation (optional)

When the LLM gateway is enabled, RAG operates in augmented mode:

```
Retrieved chunks (deterministic)
        │
        ▼
build_augmented_prompt()
  - Retrieved context injected as background
  - Sanitized before sending (no IPs/hostnames/UUIDs)
  - Prompt hash logged (never raw prompt)
        │
        ▼
LLMGateway.call()
  - Routes to OpsAI Ollama / proxy / API gateway (never direct)
  - Rate limited at tool level
  - Cached by prompt hash
        │
        ▼
LLMResponse (advisory only)
  - .content used as natural-language answer
  - .sources shows which KB chunks were used
  - .mode = "rag+llm" | "rag-only" (fallback if LLM unavailable)
```

When the gateway is disabled, `build_no_llm_summary()` generates a
deterministic extractive summary from the top-3 retrieved chunks.

---

## 6. LLM Gateway — Architecture

### 6.1 Core rule: no direct backend connections

The tool never connects to a public LLM API endpoint. All LLM requests
route through an internal gateway (Ollama / enterprise proxy / API GW).

```
Tool code                 LLM Gateway               Backend
─────────                 ───────────               ───────
enrich_datascript()  ──►  _call_opsai()        ──►  Ollama (internal)
enrich_report()      ──►  _call_via_proxy()    ──►  [approved LLM via proxy]
rag ask              ──►  _call_via_apigw()    ──►  [API GW → LLM]
                     ──►  disabled             ──►  (no call — graceful fallback)
```

### 6.2 What is sanitized before sending

Every prompt passes through the sanitizer before any network call:

| Pattern | Replacement | Example |
|---|---|---|
| IPv4 addresses | `[IP_N]` | `10.10.2.45` → `[IP_1]` |
| UUIDs | `[UUID_N]` | `a3f4-bc21-...` → `[UUID_1]` |
| FQDNs (3+ labels) | `[HOST_N]` | `avi.company-name.local` → `[HOST_1]` |
| `"password": "..."` | `"[CREDENTIAL]": "[REDACTED]"` | Credential fields |
| Bearer tokens | `Bearer [TOKEN_REDACTED]` | Auth headers |

The same placeholder `[IP_1]` is used consistently throughout the block —
the sanitized output remains coherent for LLM consumption.

### 6.3 What is never logged

- Raw prompt content (only SHA-256 hash is logged)
- Raw LLM response content (logged only if `log_responses: true`, default `false`)
- API keys or credentials

### 6.4 Modular enable/disable

```python
# In any code that uses LLM features:
gw = get_gateway()
if not gw.enabled:
    return deterministic_result   # always works without LLM

resp = gw.call(prompt)
return resp.content if resp.ok else deterministic_result   # graceful fallback
```

The gateway has a single `enabled` property. Setting `llm_gateway.enabled: false`
in `config.yaml` causes every LLM call to return immediately with the fallback.

---

## 7. Concurrency and governance controls

### 7.1 File-based deployment lock

Prevents two operators from deploying to the same environment simultaneously.

```
state/<env>-deploy.lock
  Contains: {operator, pid, hostname, started_at}
  Stale lock auto-broken after 4 hours
  Always released — even on exception (context manager)
```

### 7.2 Governance gate (deploy phase only)

Required for `--execute`:
1. CR ID format validated against configured regex
2. SoD check: `$USER` must differ from the approver recorded in governance file
3. Maintenance window start timestamp recorded
4. Override possible with `--override-governance` + mandatory justification text
   (override permanently logged, never silently accepted)

### 7.3 Audit hash chain

Each JSONL log entry contains the SHA-256 of the previous entry.
Any modification, deletion, or reordering of entries is detectable by
running `migrate.py audit verify --log <file>`.

---

## 8. Multi-tenant isolation

Isolation is enforced at three independent levels:

| Level | Mechanism | Enforced by |
|---|---|---|
| AVI API | `X-Avi-Tenant: <tenant>` header on every request | AVI controller |
| FortiADC API | `?vdom=<vdom>` parameter on every request | FortiADC |
| Tool config | Explicit `avi_tenant`/`fortiadc_vdom` per environment entry | `config.yaml` validation |

A discovery run for `Tenant-Dev-B` cannot see `Tenant-Prod-A` objects at the API level.
A deployment to `DEV-B` VDOM cannot affect `PROD-A` VDOM at the FortiADC level.
