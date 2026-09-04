# Technical Architecture: Operation-by-Operation Reference

This document provides a deep-dive into the internal operations of the Avi-to-FortiADC migration tool. It is designed to be the primary technical source for the RAG engine when answering questions about "how" the tool performs its tasks.

## 1. Discovery & Ingestion (`core/import_classifier.py`)
The discovery phase is the "Foundation Gate."
- **Function**: `AviImportClassifier.classify()`
- **Operation**:
    1. Reads the raw Avi JSON/API payload.
    2. Maps every top-level key (e.g., `VirtualService`, `Pool`) to a **Mapped Family**.
    3. Categorizes objects as **Include**, **Context**, or **Exclude** based on their impact on traffic.
    4. **Inclusion Logic**: If a `VirtualService` is selected, the classifier recursively flags all children (Pools, Certs) as `Include` and all neighbors (VRFs, Networks) as `Context`.

## 2. Dependency Resolution (`core/resolver.py`)
The "Intelligence" behind the tool is the Dependency Graph.
- **Function**: `ConfigurationGraph`
- **Operation**:
    1. **Adjacency Matrix**: Builds a directed graph of all objects.
    2. **Reference Tracking**: Identifies cross-tenant and global-to-private references.
    3. **Impact Analysis**: When an operator excludes a tenant, the resolver identifies if any "Shared" objects are still needed by other included tenants, preventing broken references in the target FortiADC config.

## 3. Compatibility Analysis (`services/pipeline_service.py`)
This service orchestrates the "Decision Gates."
- **Function**: `get_analysis_triage_review()`
- **Operation**:
    1. Scans the discovery payload for **Known Patterns (P01-P08)**.
    2. **Pattern Matching**: Uses regex and value-range checks (e.g., checking if `HealthMonitor.timeout` >= `HealthMonitor.send_interval`).
    3. **Ledger Generation**: Creates the `state/env-ledger.json` which tracks every "Unsupported" item.
    4. **Decision State**: Manages the transitions between `accept`, `override`, and `defer`.

## 4. Transformation Pipeline (`transformers/`)
The transformation gate uses the **V-A-N-R (VDOM, Application, Network, Route)** model.
- **Core Orchestrator**: `transformers/base.py`
- **Object Converters**: 
    - `VirtualServiceTransformer`: Converts Avi VS + VsVip → `load_balance_virtual_server`.
    - `PoolTransformer`: Converts Avi Pool + Servers → `load_balance_pool`.
- **Payload Construction**:
    1. Fetches "Baseline" config from FortiADC to avoid ID collisions.
    2. Applies mapping rules from `transformers/mappings.py`.
    3. Performs **Auto-Correction** (e.g., rewriting HMAC-SHA1 to SHA256 where required).

## 5. RAG Retrieval Engine (`core/rag/`)
The "Expert Insight" system.
- **Function**: `query()` in `core/rag/augmentor.py`
- **Operation**:
    1. **Semantic Search**: Uses BM25 to find relevant chunks in the local SQLite DB.
    2. **Ranker**: Prioritizes `experience` source (learned patterns) over `docs`.
    3. **Augmentation**: Injects the **Environment Snapshot** (current tenant/object context) into the prompt before calling the LLM.
    4. **Knowledge Loop**: `save_experience()` writes markdown files that are immediately indexed to create a permanent learning effect.

## 6. Execution & Deployment (`core/deployer.py`)
The final gate that interacts with the FortiADC API.
- **Function**: `FortiADCDeployer.deploy()`
- **Operation**:
    1. **Idempotency Check**: Verifies if the object already exists in the target VDOM.
    2. **Dependency Order**: Deploys in the sequence: `Cert -> Pool -> VS`.
    3. **Rollback Journaling**: Every successful API call is recorded in `state/env-rollback.json` for atomic reversal if the migration fails.
