# Local Laptop Test Setup
## Full AVI → FortiADC Migration Pipeline Simulation

**Audience:** Engineers who want to demo or test the tool without access to real AVI/FortiADC systems  
**System requirements:** 12 GB RAM, Python 3.8+, Docker Desktop (optional but recommended)  
**Time to first working test:** ~5 minutes (no Docker) / ~10 minutes (with Docker)

---

## Option A — Pure Python, No Docker (fastest start)

No Docker. All tests run offline using the fixture data and mocked API clients.
Everything works, including the full pipeline, governance, lock, and audit chain.

```bash
# 1. Install test dependencies
cd avi-fortiadc-migration-v0.8
pip install pytest pytest-mock pyyaml requests

# 2. Run the full offline test suite
python3 -m pytest tests/test_migration_tool.py -v

# 3. Run the full pipeline manually against the fixture
python3 migrate.py analyse  --input tests/fixtures/dev_b_discovery.json
python3 migrate.py transform --input tests/fixtures/dev_b_discovery.json \
                             --output fortiadc/test-config.json

# 4. Inspect outputs
cat fortiadc/test-config.json | python3 -m json.tool | head -60
cat state/dev_b_discovery-unsupported.json | python3 -m json.tool
python3 migrate.py audit chain   logs/dev_b_discovery-transform.jsonl
python3 migrate.py audit verify  logs/dev_b_discovery-transform.jsonl
python3 migrate.py audit summary logs/dev_b_discovery-transform.jsonl
```

**Expected results from fixture:**
- 4 VS in fixture → 3 auto-translated (web-http, api-https, tcp-db) + 1 skipped (ds-redirect — DataScript BLOCKED)
- 3 health monitors → all 3 translated (HTTP, HTTPS, TCP)
- 2 SSL certs → 1 translated (exportable wildcard) + 1 BLOCKED (HSM cert-0002)
- 1 SSL profile → translated with TLS 1.2/1.3 mapping
- 1 persistence profile → translated (cookie, 1200s)
- Pool members flattened into `real_servers` section
- DataScript classified SIMPLE (redirect pattern) with FortiADC approach

---

## Option B — Docker Compose (full pipeline with mock servers)

This runs a real mock AVI controller and mock FortiADC server in Docker.
The tool connects to them exactly as it would connect to real systems.
The complete discover → transform → deploy pipeline runs end-to-end.

### Prerequisites

```bash
# Install Docker Desktop
# macOS: brew install --cask docker
# Windows: https://docs.docker.com/desktop/install/windows-install/
# Linux: https://docs.docker.com/engine/install/

# Verify Docker is running
docker --version
docker compose version
```

### Start the mock environment

```bash
cd avi-fortiadc-migration-v0.8/tests

# Build and start mock AVI + mock FortiADC
docker compose up -d

# Wait for health checks to pass (~15 seconds)
docker compose ps

# Expected output:
# NAME              STATUS
# mock-avi          Up (healthy)
# mock-fortiadc     Up (healthy)
```

### Configure the tool to point at mock servers

Create a `config.yaml` in the project root:

```yaml
# config.yaml — points to local Docker mock servers
avi:
  controller:   "http://localhost:8080"
  username:     "admin"
  password:     "mock-password"
  api_version:  "22.1.5"
  verify_ssl:   false
  timeout:      10

fortiadc:
  host:         "http://localhost:8443"
  username:     "admin"
  password:     "mock-password"
  vdom:         "root"
  verify_ssl:   false
  timeout:      10

environments:
  - name:           "dev-b"
    avi_tenant:     "Tenant-Dev-B"
    fortiadc_vdom:  "DEV-B"
    priority:       1

network_map:
  "10.10.0.0/24": "port1"
  "10.10.1.0/24": "port2"

certificates:
  non_exportable_action: "flag"
  expire_warning_days:   60
```

### Run the full pipeline end-to-end

```bash
cd avi-fortiadc-migration-v0.8

# Step 1: Discover from mock AVI
python3 migrate.py discover --env dev-b

# Expected:
# Connected. Avi version: 22.1.5
# DISCOVERY COMPLETE
#   Virtual Services  : 4
#   Pools             : 4
#   SSL Certificates  : 2
#   Discovery saved to: discovery/dev-b.json

# Step 2: Analyse
python3 migrate.py analyse --input discovery/dev-b.json

# Expected:
# Migration Complexity: 4/10 — MEDIUM
# Reports written to: reports/

# Step 3: Transform
python3 migrate.py transform --input discovery/dev-b.json

# Expected:
# Transform complete:
#   Auto-translated objects : 12
#   Manual items (skipped)  : 2

# Step 4: Dry-run deploy to mock FortiADC
python3 migrate.py deploy \
  --fortiadc-config fortiadc/dev-b-config.json \
  --env dev-b \
  --dry-run

# Step 5: Live deploy to mock FortiADC (no CR required for mock — use --override-governance)
python3 migrate.py deploy \
  --fortiadc-config fortiadc/dev-b-config.json \
  --env dev-b \
  --execute \
  --override-governance

# Step 6: Verify what was deployed in mock FortiADC
curl http://localhost:8443/api/load_balance/virtual_server?vdom=DEV-B | python3 -m json.tool
curl http://localhost:8443/api/load_balance/real_server_pool?vdom=DEV-B | python3 -m json.tool
curl http://localhost:8443/api/load_balance/real_server?vdom=DEV-B | python3 -m json.tool

# Step 7: Ops-check (health, capacity, drift)
python3 migrate.py ops-check --env dev-b
```

### Stop the mock environment

```bash
cd tests
docker compose down
```

---

## Option C — With Ollama LLM (full intelligence layer)

Adds LLM-enhanced DataScript analysis and executive summaries.
Requires ~4 GB additional RAM for the model. Total: ~6 GB used.

```bash
# 1. Install Ollama
# macOS: brew install ollama
# Linux: curl -fsSL https://ollama.com/install.sh | sh

# 2. Pull a model (choose based on your RAM)
#    12 GB RAM → mistral:7b (recommended)
#     8 GB RAM → llama3.2:3b (fast, good enough)
ollama pull mistral:7b

# 3. Start Ollama server (leave terminal open)
ollama serve

# 4. Add LLM config to config.yaml
cat >> config.yaml << 'LLMCFG'

llm_gateway:
  enabled:           true
  mode:              opsai
  endpoint:          "http://localhost:11434"
  model:             "mistral:7b"
  timeout_seconds:   60
  max_tokens:        1024
  temperature:       0.1
  require_sanitize:  true
  log_responses:     false
LLMCFG

# 5. Run analysis with LLM enrichment
python3 migrate.py analyse --input discovery/dev-b.json

# The LLM will add:
# - Plain-English explanation of the DataScript
# - FortiADC approach recommendation
# - Executive summary for the Change Request
# - Output written to reports/dev-b-analysis-llm.txt

# 6. Run ops-check with LLM drift explanation
python3 migrate.py ops-check --env dev-b
# LLM will explain any drift items found

# 7. Also enable in docker-compose for all-in-one
# Uncomment the ollama service in tests/docker-compose.yml
# then: docker compose up -d
```

---

## What to show in a business demo

This is the pitch sequence. Run it in order on the laptop.

### Scene 1 — "We know what we have" (Discovery)

```bash
python3 migrate.py discover --env dev-b
cat discovery/dev-b.json | python3 -m json.tool | head -40
```
**Narrative:** "In 30 seconds we get a complete inventory of everything in AVI — every virtual service, pool, health monitor, certificate, and external integration. This is the source of truth for the entire migration."

### Scene 2 — "We know what's risky" (Analysis)

```bash
python3 migrate.py analyse --input discovery/dev-b.json
open reports/dev-b-analysis.html   # opens HTML report in browser
```
**Narrative:** "The tool automatically identifies every risk. The DataScript is flagged as requiring manual work. The HSM certificate is a blocker. The degraded pool is a warning. This is what a senior engineer would take 2 days to review manually — done in seconds, consistently."

### Scene 3 — "We know exactly what to build" (Transform)

```bash
python3 migrate.py transform --input discovery/dev-b.json
cat fortiadc/dev-b-config.json | python3 -m json.tool
```
**Narrative:** "The tool generates the complete FortiADC configuration. Every virtual server, every pool member, every health check. This eliminates the manual translation work that takes weeks in a typical migration."

### Scene 4 — "We control every change" (Governance)

```bash
# Show governance enforcement — try to deploy without CR
python3 migrate.py deploy \
  --fortiadc-config fortiadc/dev-b-config.json \
  --env dev-b --execute
# Enter different operator and approver when prompted
```
**Narrative:** "No deployment happens without a Change Request ID and a separate approver. Every action is in the audit log. This is enterprise change management built into the tool, not bolted on after the fact."

### Scene 5 — "We can roll back in seconds" (Rollback)

```bash
# Show the generated rollback script
cat scripts/rollback-dev-b.sh
bash scripts/rollback-dev-b.sh --env dev-b --dry-run
```
**Narrative:** "The rollback script is generated automatically the moment we deploy. One command returns everything to AVI. We never remove AVI until we're confident — it's always there as the fallback."

### Scene 6 — "We watch it every day" (Ops-check)

```bash
python3 migrate.py ops-check --env dev-b
```
**Narrative:** "After migration, we run this daily. It checks health across every virtual service, compares pool membership between AVI and FortiADC so we catch new servers that should be added, and alerts us before capacity becomes an issue."

---

## Troubleshooting

**`No module named 'yaml'`** → `pip install pyyaml`

**`No module named 'flask'`** → `pip install flask` (only needed for Docker servers)

**`Connection refused` when discovering** → Start Docker first: `cd tests && docker compose up -d`

**Transform produces empty virtual_servers** → Check fixture has clean VS (not all DataScript-blocked). The fixture has 3 clean VS out of 4.

**`SSLProfileTransformer not found`** → You need v0.8. Check `transformers/profiles.py` contains the class.

**Ollama times out** → Model is loading. Wait 30s and retry. Or try `llama3.2:3b` which loads faster.

**Docker `port already in use`** → Something is already on 8080 or 8443. Change the port mapping in docker-compose.yml.
