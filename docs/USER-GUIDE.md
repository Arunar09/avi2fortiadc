# Avi → FortiADC Migration Tool — User Guide

This guide covers the complete migration process from VMware Avi (NSX Advanced Load Balancer) to FortiADC using this tool.

---

## 1. Introduction

The Avi → FortiADC Migration Tool is a modular, air-gapped framework designed to automate the translation of load balancer configurations.

### Key Principles
- **Air-Gapped Compliance**: Zero internet required. All dependencies are bundled.
- **Deterministic**: Every transformation is based on explicit mapping tables, not AI inference.
- **Auditable**: Every action is cryptographically chained and exported for SIEM.
- **Safety First**: Dry-run mode by default. Explicit human approval for all mutations.

---

## 2. Installation

The tool is designed for deployment on a jump host or management server with connectivity to both Avi and FortiADC.

```bash
# 1. Extract the package
unzip avi-fortiadc-migration.zip
cd avi-fortiadc-migration

# 2. Run the offline installer
# This validates Python availability and bundles required libraries
bash install.sh

# 3. Create your configuration
cp config.example.yaml config.yaml
```

---

## 3. Interfaces

### 3.1 Web Console (Recommended)
The tool provides a premium dark-themed web console for managing migrations visually.

```bash
python run_ui.py
# Open http://127.0.0.1:5000 in your browser
```

> [!IMPORTANT]
> **Qualified Environments**: The Dashboard only displays "Qualified" environments. An environment is qualified once it has a valid Avi configuration snapshot (stored as `raw-snapshot.json` in the state folder). Incomplete imports or failed discovery attempts may create "zombie" folders on disk that remain hidden from the UI to prevent clutter.

### 3.2 Command Line Interface (CLI)
The primary CLI tool is `migrate.py`.

```bash
python migrate.py --help
```

### 3.3 Guided Wizard
A terminal-based wizard for step-by-step guidance.

```bash
python wizard.py
```

---

## 4. Configuration (`config.yaml`)

Configuration is stored in `config.yaml`. This file is git-ignored as it contains sensitive credentials.

```yaml
avi:
  controller: "https://avi-ctrl.local"
  username: "migration-viewer"
  password: "..." # Or export MIGRATION_AVI_PASS

fortiadc:
  host: "https://fortiadc.local"
  username: "migration-admin"
  password: "..." # Or export MIGRATION_FADC_PASS

environments:
  - name: "tenant-dev-b"
    avi_tenant: "Tenant-Dev-B"
    fortiadc_vdom: "VDOM-Dev-B"
```

---

## 5. Migration Pipeline

The migration proceeds through 8 deterministic phases:

1. **Discovery**: Snapshots Avi configuration using read-only API access.
2. **Analysis**: Classifies objects and runs risk pattern detectors (e.g., DataScript detection).
3. **Transformation**: Generates FortiADC API payloads.
4. **Dry-Run**: Validates FortiADC readiness without making changes.
5. **Deployment**: Creates objects in FortiADC.
6. **Parallel Run**: Ongoing health checks while Avi remains primary.
7. **DNS Cutover**: Triggers Infoblox/DNS updates to switch traffic.
8. **Verification**: Post-cutover drift and health analysis.

---

## 6. Importing Offline Configuration

If you do not have live API access to Avi, use the analyzer to generate normalized discovery JSON, then import that file.

1. Navigate to the **Import** tab in the Web Console.
2. Provide a name for the environment.
3. Upload the analyzer-generated normalized `.json` file.
4. The tool validates the strict schema and starts the pipeline from the **Discovery** phase.

---

## 7. Knowledge Base & RAG

The tool includes a built-in, offline **Retrieval-Augmented Generation (RAG)** knowledge base. Use it to query migration facts, mapping tables, and archived learnings.

```bash
# Index current knowledge (docs, mappings, previous migrations)
python migrate.py rag index

# Search for specific issues
python migrate.py rag ask --query "How do I handle pool monitoring differences?"
```

The Knowledge Base is also accessible via the **Knowledge Base** tab in the Web Console.

---

## 8. Rollback Procedures

Rollback is automated and safe. Avi remains live throughout the process until a final cutover is confirmed.

```bash
# Revert DNS to Avi endpoints
bash scripts/dns-cutover.sh --env tenant-dev-b --rollback

# Clean up FortiADC staging objects
bash scripts/rollback.sh --env tenant-dev-b --execute
```

## 9. State Management & Troubleshooting

### 9.1 The `state/` Directory
All decisions, manifests, and snapshots are stored in the local `state/` directory. Each environment has its own subfolder.
- **`decision-manifest.json`**: The source of truth for all operator decisions (mappings, excludes, overrides).
- **`raw-snapshot.json`**: The original configuration imported from Avi.
- **`ledger.json`**: A cryptographically signed record of every phase completed.

### 9.2 Purging Orphans
If you see directories in `state/` that do not appear in the Web Console:
1. They are likely "unqualified" fragments from failed or partial operations.
2. **Deletion**: Use the **Trash Can** icon on the Dashboard to purge an environment.
3. **Manual Purge**: If a folder is hidden (no UI button), it can be manually deleted from the `state/` and `logs/` directories.

### 9.3 Troubleshooting CSRF / Session Errors
If you encounter "Bad Request: CSRF token missing" or session timeouts:
- Ensure `run_ui.py` is running and the `SECRET_KEY` in `ui/app.py` is stable.
- The tool now uses a persistent secret to prevent session dropouts across application restarts.

---

## 10. Infrastructure Orchestration (V-A-N-R)

The tool uses a coordinated **V-A-N-R (VDOM-App-Network-Route)** mapping model.
- **Batch Commits**: Instead of row-by-row saving, use the **"Commit All Infrastructure Decisions"** button to validate your entire mapping strategy at once.
- **Merge Detection**: The UI automatically detects and flags when multiple Avi tenants are being consolidated into a single FortiADC VDOM.
- **Live Topology**: Decisions made in the Orchestrator reflect immediately in the Topology Verification graph.
