"""
services/pipeline_service.py
Reads pipeline state from the filesystem and returns structured data for the UI.

All functions:
- Read only (never write)
- Return plain dicts/lists (no Flask, no HTTP)
- Graceful on missing files (return partial data, not exceptions)
- Deterministic — same files in → same data out
"""
from __future__ import annotations

import csv
import io
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any

from core.topology_builder import build_vdom_topology

from services.common import canonical_env_name, load_known_envs, read_json, resolve_env_file, ensure_env_dir, flatten_avi_snapshot
from core.decision_manifest import (
    DecisionManifestStore,
    ensure_import_scope_gate,
    ensure_phase_gate,
)


# ── Pipeline phases in order ─────────────────────────────────────────────────

PHASES = [
    "discover",
    "analyse",
    "transform",
    "dry-run",
    "deploy",
    "parallel-run",
    "dns-cutover",
    "verify",
]

PHASE_LABELS = {
    "discover":     "Discovery",
    "analyse":      "Analysis",
    "transform":    "Transform",
    "dry-run":      "Dry Run",
    "deploy":       "Deploy",
    "parallel-run": "Parallel Run",
    "dns-cutover":  "DNS Cutover",
    "verify":       "Verify",
}

IMPORT_SCOPE_CATEGORIES = {
    "gslb":    ["Gslb", "GslbService", "GslbSite", "GslbGeoDbProfile", "GslbDnsUpdate", "GslbThirdPartySite"],
    "slb":     ["VirtualService", "VsVip", "ApplicationProfile", "NetworkProfile", "SSLProfile", "SSLKeyAndCertificate", "HTTPPolicySet", "DnsPolicy", "L4PolicySet", "AuthProfile", "SSOPolicy", "VSDataScriptSet"],
    "pool":    ["Pool", "PoolGroup", "HealthMonitor", "PriorityLabels", "PoolGroupDeploymentPolicy"],
    "network": ["Network", "VrfContext", "ServiceEngineGroup", "ServiceEngine", "NetworkService", "Cloud", "AvailabilityZone"],
}

STATUS_MAP = {
    "done": "completed",
    "completed": "completed",
    "success": "completed",
    "running": "in_progress",
    "in_progress": "in_progress",
    "failed": "failed",
    "error": "failed",
    "pending": "pending",
}

IMPORT_SCOPE_GROUP_ORDER = ["pipeline", "gslb_related", "context", "noise"]
SENSITIVE_PREVIEW_FIELDS = {
    "aes_key",
    "bearer",
    "certificate",
    "certificate_signing_request",
    "hmac_key",
    "key",
    "key_data",
    "key_material",
    "password",
    "private_key",
    "secret",
    "token",
    "private_key",
    "secret",
}

GLOBAL_KEY_PREFIXES = ("Waf", "SSL", "PKI", "Auth", "AlertConfig", "ApplicationProfile", "ApplicationPersistenceProfile", "VSDataScriptSet", "ErrorPage", "Bot", "IPReputation", "Gslb", "GSLB")


def _read_ledger(state_dir: str, env: str) -> dict:
    path = resolve_env_file(state_dir, env, "-ledger.json")
    return read_json(path) if path else {}


def _read_governance(state_dir: str, env: str) -> dict:
    path = resolve_env_file(state_dir, env, "-governance.json")
    return read_json(path) if path else {}


# ── Public API ────────────────────────────────────────────────────────────────

def get_topology_data(dirs: dict, env: str) -> list[dict]:
    """Returns the unified V-A-N-R topology graph for an environment."""
    state_dir = Path(dirs.get("state_dir", "state"))
    fadc_dir = Path(dirs.get("fortiadc_dir", "fortiadc"))
    
    # Load manifest
    store = DecisionManifestStore(env, state_dir=str(state_dir))
    manifest = store.load()
    
    # 1. Try Target State First (FortiADC JSON)
    fadc_file = fadc_dir / f"{env}-config.json"
    if fadc_file.exists():
        try:
            fadc_data = read_json(fadc_file)
            return build_vdom_topology(fadc_data, manifest)
        except Exception as e:
            print(f"Error parsing Forti config for topology: {e}")

    # 2. Fallback to Source State (Avi Discovery)
    discovery_file = resolve_env_file(dirs.get("discovery_dir", "discovery"), env, ".json")
    if not discovery_file:
        return []
        
    discovery = read_json(discovery_file)
    return build_vdom_topology(discovery, manifest)


def get_existing_vdoms(dirs: dict, env: str) -> tuple[list[str], str]:
    """Queries existing VDOMs on the target FortiADC baseline + manual manifest definitions."""
    fadc_dir = Path(dirs.get("fortiadc_dir", "fortiadc"))
    state_dir = Path(dirs.get("state_dir", "state"))
    vdoms = {"root"}
    source = "offline"
    
    # 1. Load from saved FortiADC config
    fadc_file = fadc_dir / f"{env}-config.json"
    if fadc_file.exists():
        try:
            data = read_json(fadc_file)
            found_any = False
            for vs in data.get("virtual_servers", []):
                if "vdom" in vs:
                    vdoms.add(vs["vdom"])
                    found_any = True
            if found_any:
                source = "cached target baseline"
        except Exception:
            pass
            
    # 2. Load from manual orchestrator decisions
    try:
        store = DecisionManifestStore(env, state_dir=str(state_dir))
        manifest = store.load()
        v_map = manifest.get("vdom_mapping", {}).get("mappings", {})
        for avi_tenant, mapping in v_map.items():
            target = mapping.get("target")
            if target:
                vdoms.add(target)
                if mapping.get("params", {}).get("manual_baseline"):
                    if source == "disconnected" or source == "offline":
                        source = "manual orchestrator baseline"
    except Exception:
        pass

    if len(vdoms) <= 1 and source == "offline":
        return [], "disconnected"
        
    return sorted(list(vdoms)), source


def get_existing_fortiadc_config(dirs: dict, env: str) -> dict:
    """Reads existing target FortiADC configurations for reference mapping."""
    fadc_dir = Path(dirs.get("fortiadc_dir", "fortiadc"))
    fadc_file = fadc_dir / f"{env}-config.json"
    if fadc_file.exists():
        try:
            return read_json(fadc_file)
        except Exception:
            pass
    return {"ssl_certificates": [], "virtual_servers": [], "real_server_pools": []}


def get_pipeline_status(dirs: dict) -> dict:
    """
    Return pipeline phase status for every known environment.
    Shape:
      { "environments": [ { "name", "phases": [...], "current_phase", "blocked", "last_action" } ] }
    """
    state_dir = dirs.get("state_dir", "state")
    envs = load_known_envs(dirs)
    from services.common import get_tenant_mapping
    tenant_map = get_tenant_mapping(dirs)

    # Pre-build manifest cache for hierarchy resolution
    manifest_cache = {}
    for e in envs:
        m = DecisionManifestStore(e, state_dir=state_dir).load()
        manifest_cache[e] = m.get("meta", {})

    env_data_map = {}
    for env in envs:
        ledger = _read_ledger(state_dir, env)
        gov_store = DecisionManifestStore(env, state_dir=state_dir)
        manifest = gov_store.load()
        parent_env = manifest.get("meta", {}).get("parent_env")

        phases_state = ledger.get("phases", {})
        audit = ledger.get("audit", [])
        last_action = audit[-1] if audit else None

        gate_import = ensure_phase_gate(manifest, "import", ledger)
        gate_analyse = ensure_phase_gate(manifest, "analyse", ledger)
        gate_transform = ensure_phase_gate(manifest, "transform", ledger)
        gate_deploy = ensure_phase_gate(manifest, "deploy", ledger)
        gate_verify = ensure_phase_gate(manifest, "verify", ledger)
        gate_statuses = {
            "discover": gate_import.ok,
            "analyse": gate_analyse.ok,
            "transform": gate_transform.ok,
            "deploy": gate_deploy.ok,
            "verify": gate_verify.ok,
        }

        phases = []
        current = None
        blocked = False

        for ph in PHASES:
            ph_data  = phases_state.get(ph, {})
            raw_status = str(ph_data.get("status", "pending")).strip().lower()
            status = STATUS_MAP.get(raw_status, raw_status or "pending")
            if ph in gate_statuses and not gate_statuses[ph]:
                if status == "completed":
                    status = "pending"
            if ph == "dry-run" and not gate_deploy.ok and status == "completed":
                status = "pending"
            if ph == "parallel-run" and not gate_deploy.ok and status == "completed":
                status = "pending"
            if ph == "dns-cutover" and not gate_verify.ok and status == "completed":
                status = "pending"
            phases.append({
                "key":       ph,
                "label":     PHASE_LABELS.get(ph, ph),
                "status":    status,
                "completed_at": ph_data.get("completed_at", ""),
                "operator":  ph_data.get("operator", ""),
                "notes":     ph_data.get("notes", ""),
            })
            if status == "in_progress":
                current = ph
            if status == "failed":
                blocked = True
                if current is None:
                    current = ph

        for ph in phases:
            if ph["key"] == "discover" and not gate_import.ok:
                current = "discover"
                break
            if ph["key"] == "analyse" and gate_import.ok and not gate_analyse.ok:
                current = "analyse"
                break
            if ph["key"] == "transform" and gate_analyse.ok and not gate_transform.ok:
                current = "transform"
                break
            if ph["key"] == "deploy" and gate_transform.ok and not gate_deploy.ok:
                current = "deploy"
                break
            if ph["key"] == "verify" and gate_deploy.ok and not gate_verify.ok:
                current = "verify"
                break

        # Object counts from discovery if present (Hierarchy-aware)
        counts = {}
        state_dir = Path(dirs.get("state_dir", "state"))
        
        def _get_tname(o):
            tr = o.get("tenant_ref", "")
            if "name=" in tr: return tr.split("name=")[-1].upper()
            return str(o.get("tenant", o.get("_tenant", "admin"))).upper()

        def resolve_disc_data(e_name, p_name):
            st_file = state_dir / e_name / "candidate-discovery.json"
            if st_file.exists(): return read_json(st_file)
            d_file = resolve_env_file(dirs.get("discovery_dir", "discovery"), e_name, ".json")
            if d_file: return read_json(d_file)
            if p_name:
                pd_file = resolve_env_file(dirs.get("discovery_dir", "discovery"), p_name, ".json")
                if pd_file: return read_json(pd_file)
            return {}

        raw_disc = resolve_disc_data(env, parent_env)
        if raw_disc:
            processed_disc = raw_disc
            # Master Exclusion
            if not parent_env:
                exclude_vses = set()
                exclude_tenants_upper = set()
                # Use manifest_cache (which we pre-built) to find children
                for other_env, e_meta in manifest_cache.items():
                    if str(e_meta.get("parent_env")).strip() == env:
                        m_other = DecisionManifestStore(other_env, state_dir=str(state_dir)).load()
                        tf = m_other.get("tenant_filter", {})
                        exclude_vses.update([v.strip() for v in tf.get("included_vses", [])])
                        exclude_tenants_upper.update([t.strip().upper() for t in tf.get("included_tenants", [])])
                
                if exclude_vses or exclude_tenants_upper:
                    processed_disc = {"_meta": raw_disc.get("_meta", {})}
                    for cat, items in raw_disc.items():
                        if not isinstance(items, list) or cat == "_meta": continue
                        processed_disc[cat] = [
                            o for o in items 
                            if o.get("name") not in exclude_vses and _get_tname(o) not in exclude_tenants_upper
                        ]
            
            # Sub-task Selective Loading
            elif parent_env and not (state_dir / env / "candidate-discovery.json").exists():
                 m_this = DecisionManifestStore(env, state_dir=str(state_dir)).load()
                 tf = m_this.get("tenant_filter", {})
                 vses = set([v.strip() for v in tf.get("included_vses", [])])
                 tenants_upper = set([t.strip().upper() for t in tf.get("included_tenants", [])])
                 processed_disc = {"_meta": raw_disc.get("_meta", {})}
                 for cat, items in raw_disc.items():
                     if not isinstance(items, list) or cat == "_meta": continue
                     processed_disc[cat] = [
                         o for o in items 
                         if o.get("name") in vses or _get_tname(o) in tenants_upper
                     ]

            # Standardize count keys for UI template
            counts = {k: len(v) for k, v in processed_disc.items() if isinstance(v, list) and k != "_meta"}
            if "virtual_services" in counts:
                counts["vs_count"] = counts["virtual_services"] # Backward compat

        block_reason = ""
        if blocked:
            if not gate_import.ok:
                block_reason = f"Import Gate: {gate_import.message}"
            elif not gate_analyse.ok:
                block_reason = f"Analysis Gate: {gate_analyse.message}"
            elif not gate_transform.ok:
                block_reason = f"Transform Gate: {gate_transform.message}"
            elif not gate_deploy.ok:
                block_reason = f"Deploy Gate: {gate_deploy.message}"
            elif not gate_verify.ok:
                block_reason = f"Verify Gate: {gate_verify.message}"
            else:
                block_reason = "A process step has failed. View timeline logs."

        env_data_map[env] = {
            "name":          env,
            "parent_env":    parent_env,
            "sub_tasks":     [],
            "tenant":        tenant_map.get(env, "Global/System"),
            "phases":        phases,
            "current_phase": current or (_next_pending(phases)),
            "blocked":       blocked,
            "block_reason":  block_reason,
            "counts":        counts,
            "last_action":   last_action,
            "unsupported_open": sum(
                1 for item in ledger.get("unsupported", [])
                if not item.get("resolved", False)
            ),
            "decision_gates": {
                "import": gate_import.ok,
                "analyse": gate_analyse.ok,
                "transform": gate_transform.ok,
                "deploy": gate_deploy.ok,
                "verify": gate_verify.ok,
            },
        }

    # Build Hierarchy and Summaries
    top_level_envs = []
    for env, data in env_data_map.items():
        p_env = data.get("parent_env")
        if p_env:
            p_env = p_env.strip()
            # If parent exists in our map, attach as sub-task
            if p_env in env_data_map:
                env_data_map[p_env]["sub_tasks"].append(data)
                continue # Do not add to top_level_envs
            # Fallback: if parent is defined but missing from scan, treat as top-level (orphaned)
            top_level_envs.append(data)
        else:
            top_level_envs.append(data)
    
    # Sort sub-tasks and calculate parent aggregate stats
    for data in env_data_map.values():
        if data["sub_tasks"]:
            data["sub_tasks"].sort(key=lambda x: x["name"])
            # Optional: bubble up VS counts to parent? 
            # For now just keep them distinct.

    # Group by tenant for the UI
    tenant_groups = {}
    for e in top_level_envs:
        t = e["tenant"]
        if t not in tenant_groups:
            tenant_groups[t] = {
                "tenant": t,
                "environments": [],
                "count": 0,
                "vs_total": 0,
                "blocked_count": 0
            }
        tenant_groups[t]["environments"].append(e)
        tenant_groups[t]["count"] += 1
        tenant_groups[t]["vs_total"] += e["counts"].get("virtual_services", 0)
        if e["blocked"]:
            tenant_groups[t]["blocked_count"] += 1

    # Calculate logical totals (roots only)
    root_envs = [e for e in env_data_map.values() if not e.get("parent_env")]
    
    return {
        "environments": list(env_data_map.values()),
        "root_count": len(root_envs),
        "total_count": len(env_data_map),
        "tenant_groups": sorted(tenant_groups.values(), key=lambda x: x["tenant"])
    }


def _next_pending(phases: list[dict]) -> str | None:
    for ph in phases:
        if ph["status"] == "pending":
            return ph["key"]
    return None


def get_environment_readiness(dirs: dict) -> list[dict]:
    """
    Return a readiness card per environment with key gate indicators.
    """
    state_dir = dirs.get("state_dir", "state")
    envs = load_known_envs(dirs)
    from services.common import get_tenant_mapping
    tenant_map = get_tenant_mapping(dirs)

    result = []
    for env in envs:
        ledger = _read_ledger(state_dir, env)
        gov    = _read_governance(state_dir, env)
        phases = ledger.get("phases", {})

        # Discovery file present?
        disc_file = resolve_env_file(dirs.get("discovery_dir", "discovery"), env, ".json")
        has_discovery = bool(disc_file)

        # Object counts
        vs_count = 0
        if disc_file:
            data = read_json(disc_file)
            vs_count = len(data.get("virtual_services", []))

        # Transform output present?
        fadc_file = resolve_env_file(dirs.get("fortiadc_dir", "fortiadc"), env, "-config.json")
        has_transform = bool(fadc_file)

        unsupported = ledger.get("unsupported_items", [])
        open_items  = [i for i in unsupported if not i.get("resolved", False)]

        # CR captured?
        has_cr = bool(gov.get("cr_id", ""))

        # Determine overall readiness
        deploy_status = phases.get("deploy", {}).get("status", "pending")
        if deploy_status == "completed":
            readiness = "deployed"
        elif open_items:
            readiness = "blocked"
        elif has_transform and has_cr:
            readiness = "ready"
        elif has_discovery:
            readiness = "in_progress"
        else:
            readiness = "not_started"

        result.append({
            "env":           env,
            "tenant":        tenant_map.get(env, "Global/System"),
            "readiness":     readiness,
            "has_discovery": has_discovery,
            "has_transform": has_transform,
            "has_cr":        has_cr,
            "open_items":    len(open_items),
            "vs_count":      vs_count,
            "cr_id":         gov.get("cr_id", ""),
            "deploy_phase":  deploy_status,
        })

    return result


def log_system_event(dirs: dict, message: str, level: str = "INFO", env_name: str = "system-auth"):
    """Write a structured event to the system audit log."""
    logs_dir = Path(dirs.get("logs_dir", "logs"))
    logs_dir.mkdir(parents=True, exist_ok=True)
    
    log_file = logs_dir / f"{env_name}.jsonl"
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level":     level,
        "message":   message,
        "phase":     "SYSTEM"
    }
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")

def get_recent_activity(dirs: dict, limit: int = 100, page: int = 1, env: str | None = None, since_hours: int | None = None, level: str | None = None, source_filter: str | None = None) -> dict:
    """
    Return paginated events across all environments from JSONL logs.
    Supports filtering by environment, time, severity level, and source badge.
    """
    from datetime import datetime, timedelta, timezone
    
    logs_dir = Path(dirs.get("logs_dir", "logs"))
    if not logs_dir.exists():
        return {"events": [], "total": 0, "pages": 0, "current_page": 1}

    cutoff = None
    if since_hours and since_hours > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)

    all_events = []
    log_files = list(logs_dir.glob("*.jsonl")) + list(logs_dir.glob("*/*.jsonl"))
    
    for log_file in sorted(log_files):
        source_env = canonical_env_name(log_file.stem)
        if env and source_env != env:
            continue
            
        # Source group filtering
        if source_filter == "system" and source_env != "system-auth":
            continue
        if source_filter == "migration" and source_env == "system-auth":
            continue

        try:
            lines = log_file.read_text(encoding="utf-8").strip().splitlines()
            for line in lines[-2000:]:
                try:
                    ev = json.loads(line)
                    
                    # Severity level filtering
                    if level and ev.get("level", "").lower() != level.lower():
                        continue

                    if cutoff:
                        ts_str = ev.get("timestamp")
                        if ts_str:
                            try:
                                if ts_str.endswith('Z'): ts_str = ts_str.replace('Z', '+00:00')
                                ts = datetime.fromisoformat(ts_str)
                                if ts < cutoff: continue
                            except: continue
                    
                    ev["_source"] = source_env
                    # Attribution fallback
                    if source_env != "system-auth" and not ev.get("env") and not ev.get("tenant"):
                        ev["env"] = source_env
                        
                    all_events.append(ev)
                except: pass
        except: pass

    all_events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    
    total = len(all_events)
    start = (page - 1) * limit
    end = start + limit
    
    return {
        "events":       all_events[start:end],
        "total":        total,
        "pages":        (total + limit - 1) // limit,
        "current_page": page
    }


def get_import_scope_review(dirs: dict, env: str, manifest: dict, activity_limit: int = 12) -> dict:
    """Return hierarchical tenant-grouped import-scope review data for the operator UI."""
    state_dir = Path(dirs.get("state_dir", "state"))
    # Bubbling isolation: resolve_env_file checks grouped dirs first
    snap_path = resolve_env_file(state_dir, env, "-raw-snapshot.json")
    # Generic flattening ensures all layers (AviConfig siblings and children) are merged
    raw_snapshot = flatten_avi_snapshot(read_json(snap_path)) if snap_path else {}
    raw_inventory = manifest.get("raw_key_inventory", {}) or {}
    raw_decisions = manifest.get("raw_key_decisions", {}) or {}

    # 1. Initialize Resolver Graph and build key_details
    from core.resolver import ConfigurationGraph
    graph = ConfigurationGraph(raw_snapshot)
    reference_counts: dict[tuple[str, str], int] = {}
    for dep_source_key, dep_items in graph._raw.items():
        if not isinstance(dep_items, list):
            continue
        for dep_item in dep_items:
            if not isinstance(dep_item, dict):
                continue
            deps = graph.get_dependencies(dep_source_key, dep_item)
            for dep_type, dep_names in deps.items():
                for dep_name in dep_names:
                    reference_counts[(dep_type, dep_name)] = reference_counts.get((dep_type, dep_name), 0) + 1
    
    key_details: dict[str, dict[str, Any]] = {}
    decision_totals = {"include_in_pipeline": 0, "context_only": 0, "exclude": 0}

    # 1.1 Calculate "Live" Scoping Set (Tracing based on Tenant/VS Selection)
    tf_data = manifest.get("tenant_filter", {"enabled": False, "available_tenants": [], "included_tenants": [], "included_vses": []})
    is_live_scope_active = bool(tf_data.get("enabled"))
    
    to_include = set() # set of (Type, Name)
    if is_live_scope_active:
        inc_tenants = {t.upper() for t in tf_data.get("included_tenants", [])}
        inc_vses = set(tf_data.get("included_vses", []))
        
        queue = [] # [(Type, ItemDict)]
        
        # Identify Roots
        for rk, items in graph._raw.items():
            if not isinstance(items, list): continue
            for item in items:
                if not isinstance(item, dict): continue
                t_name = graph.get_tenant(item).upper()
                
                # Normalize comparison (ADMIN/GLOBAL / SHARED)
                is_match = (t_name in inc_tenants)
                if not is_match and "GLOBAL / SHARED" in inc_tenants:
                    if t_name == "ADMIN" or rk.startswith(GLOBAL_KEY_PREFIXES):
                        is_match = True
                
                if is_match:
                    name = item.get("name")
                    if name:
                        to_include.add((rk, name))
                        queue.append((rk, item))
                
                # VS Specific roots
                if rk == "VirtualService":
                    vs_name = item.get("name")
                    if vs_name in inc_vses and (rk, vs_name) not in to_include:
                        to_include.add((rk, vs_name))
                        queue.append((rk, item))
        
        # Recursive Trace
        processed = set()
        while queue:
            rk, item = queue.pop(0)
            item_id = (rk, item.get("name", ""))
            if item_id in processed: continue
            processed.add(item_id)
            
            deps = graph.get_dependencies(rk, item)
            for d_type, d_names in deps.items():
                for d_name in d_names:
                    if (d_type, d_name) not in to_include:
                        target = graph.get_by_name(d_type, d_name)
                        if target:
                            to_include.add((d_type, d_name))
                            queue.append((d_type, target))

    # 1.2 Build Key Detail Summaries and Resolved Decisions
    for raw_key, inv in raw_inventory.items():
        rd = raw_decisions.get(raw_key, {})
        if isinstance(rd, str): rd = {"decision": rd}
        
        # Base Decision from Manifest (user overrides)
        base_decision = (
            rd.get("decision")
            or (inv or {}).get("default_decision")
            or "context_only"
        )
        
        # FINAL DECISION: Merge with Live Selection Engine
        if is_live_scope_active:
            # If any object of this type is in the 'to_include' set, we keep it as-is or upgrade to Include.
            # If NONE are in the set, we default to Exclude (unless it's Context)
            has_traced_members = False
            type_items = graph.get_all(raw_key)
            for itm in type_items:
                if (raw_key, itm.get("name", "")) in to_include:
                    has_traced_members = True
                    break
            
            if has_traced_members:
                # If traced, we want it in the pipeline (or at least context)
                current_decision = "include_in_pipeline" if (inv or {}).get("group") == "pipeline" else base_decision
                if current_decision == "exclude": current_decision = "context_only"
            else:
                # If not traced by selection engine, exclude it from counts (but keep as Context if it's noise/system)
                current_decision = "exclude" if (inv or {}).get("group") == "pipeline" else "context_only"
        else:
            current_decision = base_decision

        if current_decision in decision_totals:
            decision_totals[current_decision] += 1
        
        key_details[raw_key] = _build_import_key_detail(
            raw_key=raw_key,
            inventory_item=inv or {},
            current_decision=current_decision,
            raw_value=raw_snapshot.get(raw_key),
            graph=graph,
            reference_counts=reference_counts,
        )

    # 2. Extract Tenant-based Hierarchy and track localized counts
    # Uses Graph's shared-usage map to ensure sub-keys/associated objects are visible
    tenant_hierarchy = {}  # { "Tenant": { "category": [keys...] } }
    tenant_key_obj_counts = {}  # { "Tenant": { "key": count } }
    shared_usage = graph.get_shared_usage()

    def _get_category(rk: str) -> str:
        for cat, keys in IMPORT_SCOPE_CATEGORIES.items():
            if rk in keys: return cat
        return "context"

    for rk, val in raw_snapshot.items():
        if rk not in raw_inventory: continue
        
        is_global_natured = rk.startswith(GLOBAL_KEY_PREFIXES)
        cat = _get_category(rk)
        
        if isinstance(val, list):
            for item in val:
                if not isinstance(item, dict): continue
                name = item.get("name", "unnamed")
                t_raw = graph.get_tenant(item).upper()
                
                # Determine target tenants (Primary + Consumers)
                target_tenants = set()
                if t_raw == "ADMIN" or is_global_natured:
                    target_tenants.add("Global / Shared")
                    # If this global object is consumed by specific tenants, list it under them too
                    consumers = shared_usage.get((rk, name), set())
                    for c in consumers:
                        target_tenants.add(c.upper())
                else:
                    target_tenants.add(t_raw)

                for t_name in target_tenants:
                    # Update Hierarchy
                    t_entry = tenant_hierarchy.setdefault(t_name, {})
                    t_cat = t_entry.setdefault(cat, [])
                    if rk not in t_cat:
                        t_cat.append(rk)
                    
                    # Update Localized Object Counts
                    t_counts = tenant_key_obj_counts.setdefault(t_name, {})
                    t_counts[rk] = t_counts.get(rk, 0) + 1

    # Sort Tenant groups - Global / Shared always first
    tenant_names = sorted([t for t in tenant_hierarchy.keys() if t != "Global / Shared"])
    sorted_tenants = (["Global / Shared"] if "Global / Shared" in tenant_hierarchy else []) + tenant_names
    
    category_order = ["gslb", "slb", "pool", "network", "context"]

    # 3. Build group structures for template
    hierarchy_groups = []
    for t_name in sorted_tenants:
        t_data = tenant_hierarchy[t_name]
        cats = []
        for cat in category_order:
            if cat in t_data:
                keys = sorted(t_data[cat])
                cats.append({
                    "name": cat,
                    "label": cat.upper(),
                    "item_keys": keys,
                    "count": len(keys),
                    "include_count": sum(1 for k in keys if key_details.get(k, {}).get("current_decision") == "include_in_pipeline"),
                    "exclude_count": sum(1 for k in keys if key_details.get(k, {}).get("current_decision") == "exclude"),
                    "context_count": sum(1 for k in keys if key_details.get(k, {}).get("current_decision") == "context_only"),
                })
        if cats:
            hierarchy_groups.append({
                "tenant": t_name,
                "categories": cats
            })

    # 4. Calculate Granular Impact Summary (Multi-decision, Shared-aware, Structured Tooltips)
    # 4a. Get Usage Map for Shared Objects from Graph
    impact_summary = {}
    shared_usage = graph.get_shared_usage()  # { (raw_key, obj_name): set(tenant_names) }

    # 4b. Refined Node Calculator
    def _get_multi_node(rk, tenant_name):
        items = raw_snapshot.get(rk, [])
        if not isinstance(items, list): return None
        
        inc_names, ctx_names, exc_names = [], [], []
        shared_inc = []
        
        for item in items:
            name = item.get("name")
            if not name or name == "unnamed":
                name = f"<{rk}: {item.get('uuid', 'system')}>"
            
            t_raw = graph.get_tenant(item).upper()
            
            is_global = rk.startswith(GLOBAL_KEY_PREFIXES) or t_raw == "ADMIN"
            is_private = t_raw == tenant_name
            is_shared_with_us = (shared_usage.get((rk, name)) and tenant_name in shared_usage[(rk, name)])
            
            belongs = False
            is_item_shared = False
            if is_private:
                belongs = True
            elif is_global and is_shared_with_us:
                belongs = True
                is_item_shared = True
            elif tenant_name == "Global / Shared" and t_raw == "ADMIN":
                belongs = True
            
            if not belongs:
                continue

            # Check if handled by sub-task
            handled_by_id = None
            for st in tf_data.get("sub_tasks", []):
                if rk == "VirtualService" and name in st.get("included_vses", []):
                    handled_by_id = st.get("id") or st.get("name")
                    break
                st_tenants = [t.upper() for t in st.get("included_tenants", [])]
                if t_raw in st_tenants:
                    if not st.get("included_vses") or (rk == "VirtualService" and name in st.get("included_vses", [])):
                        handled_by_id = st.get("id") or st.get("name")
                        break
            
            # Use the LIVE decision from key_details
            live_detail = key_details.get(rk, {})
            decision = live_detail.get("current_decision") or raw_inventory.get(rk, {}).get("default_decision") or "context_only"

            if handled_by_id and decision == "include_in_pipeline":
                decision = "delegated"
            
            if rk == "GslbService":
                names_to_add = item.get("domain_names") or [name]
            else:
                names_to_add = [name]

            for n_to_add in names_to_add:
                if decision == "include_in_pipeline":
                    inc_names.append(n_to_add)
                    if is_item_shared:
                        shared_inc.append(n_to_add)
                elif decision == "context_only":
                    ctx_names.append(n_to_add)
                elif decision == "delegated":
                    exc_names.append(f"{n_to_add} (sub-task)")
                else:
                    exc_names.append(n_to_add)

        total_inc = len(inc_names)
        total_ctx = len(ctx_names)
        total_exc = len(exc_names)
        priv_inc = total_inc - len(shared_inc)
        
        if not any([total_inc, total_ctx, total_exc]):
            return None
        
        tt = []
        if inc_names:
            tt.append("✅ INCLUDED:")
            for name in inc_names[:10]:
                tt.append(f"  • {name}" + (" (shared)" if name in shared_inc else ""))
            if len(inc_names) > 10:
                tt.append(f"  ... (+{len(inc_names)-10} more)")
        if ctx_names:
            tt.append("\nℹ️ CONTEXT:")
            for name in ctx_names[:5]:
                tt.append(f"  • {name}")
        if exc_names:
            tt.append("\n❌ EXCLUDED:")
            for name in exc_names[:5]:
                tt.append(f"  • {name}")

        return {
            "inc": total_inc, "ctx": total_ctx, "exc": total_exc, 
            "display": f"{priv_inc}+{len(shared_inc)}" if shared_inc else str(total_inc),
            "inc_names": inc_names, "ctx_names": ctx_names, "exc_names": exc_names, "shared_inc": shared_inc,
            "tooltip": "\n".join(tt)
        }

    def _get_category_node(cat, tenant_name):
        keys = IMPORT_SCOPE_CATEGORIES.get(cat, [])
        total_inc, total_ctx, total_exc, total_priv_inc, total_shared_inc = 0, 0, 0, 0, 0
        all_tt = []
        for rk in keys:
            n = _get_multi_node(rk, tenant_name)
            if n:
                total_inc += n["inc"]
                total_ctx += n["ctx"]
                total_exc += n["exc"]
                total_shared_inc += len(n["shared_inc"])
                total_priv_inc += (n["inc"] - len(n["shared_inc"]))
                
                tt = []
                if n["inc_names"]:
                    tt.append(f"✅ {rk}:")
                    for name in n["inc_names"][:5]:
                        tt.append(f"  • {name}" + (" (shared)" if name in n["shared_inc"] else ""))
                    if len(n["inc_names"]) > 5:
                        tt.append(f"  ... (+{len(n['inc_names'])-5} more)")
                all_tt.append("\n".join(tt))

        if not any([total_inc, total_ctx, total_exc]):
            return {"inc": 0, "ctx": 0, "exc": 0, "display": "0", "tooltip": "No items found."}
        
        return {
            "inc": total_inc, "ctx": total_ctx, "exc": total_exc,
            "display": f"{total_priv_inc}+{total_shared_inc}" if total_shared_inc else str(total_inc),
            "tooltip": "\n\n".join([t for t in all_tt if t])
        }

    for t_name in sorted_tenants:
        node = lambda rk: _get_multi_node(rk, t_name) or {
            "inc": 0, "ctx": 0, "exc": 0, "display": "0", "tooltip": "No items found.",
            "inc_names": [], "ctx_names": [], "exc_names": [], "shared_inc": []
        }
        
        # Keys that have a dedicated column — exclude from "Others" catch-all
        _DEDICATED_COLS = {
            "GslbService", "VirtualService", "Pool", "PoolGroup",
            "HealthMonitor", "SSLKeyAndCertificate", "VSDataScriptSet",
            "WafPolicy", "ApplicationPersistenceProfile", "DnsPolicy",
            "Tenant"
        }
        other_keys = [k for k in tenant_hierarchy.get(t_name, {}).get("context", []) + tenant_hierarchy.get(t_name, {}).get("network", [])
                      if k not in _DEDICATED_COLS]
        
        o_inc, o_ctx, o_exc = 0, 0, 0
        o_inc_cat, o_ctx_cat, o_exc_cat = {}, {}, {}
        o_shared = []
        o_tt = []
        o_breakdown = {}
        for k in other_keys:
            n = _get_multi_node(k, t_name)
            if n:
                o_inc += n["inc"]; o_ctx += n["ctx"]; o_exc += n["exc"]
                if n["inc_names"]: o_inc_cat[k] = n["inc_names"]
                if n["ctx_names"]: o_ctx_cat[k] = n["ctx_names"]
                if n["exc_names"]: o_exc_cat[k] = n["exc_names"]
                o_shared.extend(n.get("shared_inc", []))
                o_tt.append(f"--- {k} ---")
                o_tt.append(n["tooltip"])
                if n["inc"] > 0: o_breakdown[k] = n["inc"]

        impact_summary[t_name] = {
            "gslb": node("GslbService"),
            "slb": node("VirtualService"),
            "pools": node("Pool"),
            "hmonitors": node("HealthMonitor"),
            "certs": node("SSLKeyAndCertificate"),
            "datascripts": node("VSDataScriptSet"),
            "waf": node("WafPolicy"),
            "persistence": node("ApplicationPersistenceProfile"),
            "others": {
                "inc": o_inc, "ctx": o_ctx, "exc": o_exc, "display": str(o_inc), "tooltip": "\n".join(o_tt[:20]),
                "inc_names": o_inc_cat, "ctx_names": o_ctx_cat, "exc_names": o_exc_cat,
                "shared_inc": o_shared, "is_categorized": True
            },
            "other_breakdown": o_breakdown
        }

    # Extract global representation of others
    global_others = {}
    for t_impact in impact_summary.values():
        for k, count in t_impact.get("other_breakdown", {}).items():
            global_others[k] = global_others.get(k, 0) + count

    # 5. Build the user-requested "tenants" and "stats" structure
    final_tenants = {}
    cat_map = {"gslb": "GSLB", "slb": "SLB", "pool": "POOL", "network": "NETWORK"}
    for t_name in sorted_tenants:
        t_data = tenant_hierarchy[t_name]
        t_output = {"GSLB": [], "SLB": [], "POOL": [], "NETWORK": [], "GLOBAL": []}
        for orig_cat, keys in t_data.items():
            mapped = cat_map.get(orig_cat, "GLOBAL")
            t_output[mapped].extend(keys)
        for c in t_output:
            t_output[c] = sorted(list(set(t_output[c])))
        final_tenants[t_name] = t_output

    stats = {
        "total": len(raw_inventory),
        "included": decision_totals["include_in_pipeline"],
        "context": decision_totals["context_only"],
        "excluded": decision_totals["exclude"],
        "resolved_import_scope": bool(manifest.get("resolved_import_scope")),
    }

    # 6. Build Individual VS Selection List with Resolved VIPs
    #    Uses ConfigurationGraph for robust, format-agnostic reference resolution.
    graph = ConfigurationGraph(raw_snapshot)

    vs_list = []
    vs_raw = raw_snapshot.get("VirtualService", [])

    for v in vs_raw:
        if not isinstance(v, dict): continue
        t_name = graph.get_tenant(v).upper()
        if t_name == "ADMIN":
            t_name = "ADMIN"

        ip = graph.get_vip_for_vs(v)
        vs_name = v.get("name", "unnamed")

        # Check if handled by a sub-task using UID if available, else name
        handled_by = None
        for st in tf_data.get("sub_tasks", []):
            if vs_name in st.get("included_vses", []):
                handled_by = st.get("id") or st.get("name")
                break
            if t_name in [t.upper() for t in st.get("included_tenants", [])]:
                if not st.get("included_vses") or vs_name in st.get("included_vses", []):
                    handled_by = st.get("id") or st.get("name")
                    break

        vs_list.append({
            "name": vs_name, 
            "tenant": t_name, 
            "vip": ip,
            "handled_by_subtask": handled_by
        })
    vs_list.sort(key=lambda x: (x["tenant"], x["name"]))


    # 7. Build Tenant-Partitioned Key Details
    tenant_key_details = {}
    for t_name, t_data in tenant_hierarchy.items():
        t_details = {}
        for cat, keys in t_data.items():
            for rk in keys:
                all_items = raw_snapshot.get(rk, [])
                if not isinstance(all_items, list): 
                    t_subset = all_items 
                else:
                    t_subset = []
                    for item in all_items:
                        if not isinstance(item, dict): continue
                        t_raw = graph.get_tenant(item).upper()
                        
                        if t_raw == "ADMIN" or rk.startswith(GLOBAL_KEY_PREFIXES):
                            item_t = "Global / Shared"
                        else:
                            item_t = t_raw
                        
                        if item_t == t_name:
                            t_subset.append(item)
                
                inv = raw_inventory.get(rk, {})
                rd = raw_decisions.get(rk, {})
                if isinstance(rd, str):
                    rd = {"key": rk, "decision": "include_in_pipeline"}
                decision = rd.get("decision") or inv.get("default_decision") or "context_only"
                
                t_inv = inv.copy()
                t_inv["count"] = len(t_subset) if isinstance(t_subset, list) else 1
                
                t_details[rk] = _build_import_key_detail(
                    raw_key=rk,
                    inventory_item=t_inv,
                    current_decision=decision,
                    raw_value=t_subset,
                    graph=graph,
                    reference_counts=reference_counts,
                )
        tenant_key_details[t_name] = t_details

    # 8. Calculate unique actual totals from the raw file (not summed from table)
    def _get_unique_total(rk):
        items = raw_snapshot.get(rk, [])
        if not isinstance(items, list): return 0
        
        if rk == "GslbService":
            # GSLB count based on unique domain names as requested
            domains = set()
            for item in items:
                if not isinstance(item, dict): continue
                # Null-safe iteration over domain names
                for d in (item.get("domain_names") or []):
                    if d: domains.add(d.lower())
            return len(domains)
        return len(items)

    column_totals = {
        "gslb": _get_unique_total("GslbService"),
        "vs": _get_unique_total("VirtualService"),
        "pools": _get_unique_total("Pool"),
        "hmonitors": _get_unique_total("HealthMonitor"),
        "certs": _get_unique_total("SSLKeyAndCertificate"),
        "datascripts": _get_unique_total("VSDataScriptSet"),
        "waf": _get_unique_total("WafPolicy"),
        "persistence": _get_unique_total("ApplicationPersistenceProfile"),
    }

    # 9. Dynamic Discovery Side-Effect: Update available_tenants if empty
    tf_data = manifest.setdefault("tenant_filter", {})
    tf_data.setdefault("enabled", False)
    tf_data.setdefault("available_tenants", [])
    tf_data.setdefault("included_tenants", [])
    tf_data.setdefault("included_vses", [])
    tf_data.setdefault("notes", {})
    if not tf_data.get("available_tenants"):
        tf_data["available_tenants"] = sorted_tenants

    return {
        "tenants": final_tenants,
        "impact_summary": impact_summary,
        "column_totals": column_totals,
        "global_others": global_others,
        "vs_list": vs_list,
        "stats": stats,
        "hierarchy": hierarchy_groups,
        "key_details": key_details,
        "tenant_key_details": tenant_key_details,
        "recent_activity": get_recent_activity(dirs, limit=activity_limit, env=env),
        "tenant_filter": tf_data,
    }


def get_transform_mapping_review(dirs: dict, env: str, manifest: dict) -> dict:
    """Return structured tenant-to-VDOM mapping data with associated object counts for the UI."""
    state_dir = Path(dirs.get("state_dir", "state"))
    discovery_file = resolve_env_file(dirs.get("discovery_dir", "discovery"), env, ".json")
    if not discovery_file:
        return {}

    discovery = read_json(discovery_file)
    from core.resolver import ConfigurationGraph
    graph = ConfigurationGraph(discovery)
    
    # 1. Identify all transformable tenants (those with VSes or relevant objects)
    # We use the same logic as topology_builder to find VS-owning tenants
    tenants_with_vs = {} # { tenant_name: { "vs_count": 0, "vs_names": [], "vip_list": [] } }
    
    for vs in discovery.get("virtual_services", []):
        if not isinstance(vs, dict): continue
        t_name = graph.get_tenant(vs).upper()
        if t_name == "ADMIN": t_name = "Global / Shared"
        
        t_info = tenants_with_vs.setdefault(t_name, {"vs_count": 0, "vs_names": [], "vip_list": []})
        t_info["vs_count"] += 1
        t_info["vs_names"].append(vs.get("name", "unnamed"))
        
        # Extract VIPs if backfilled
        vips = vs.get("_vips", [])
        if vips: t_info["vip_list"].extend(vips)

    # 2. Get existing VDOM baseline
    available_vdoms, vdom_source = get_existing_vdoms(dirs, env)
    
    # 3. Build Mapping Model
    v_map = manifest.get("vdom_mapping", {}).get("mappings", {})
    tenant_mappings = []
    
    mapped_count = 0
    create_new_count = 0
    merge_count = 0
    
    target_vdom_usage = {} # { vdom_name: [tenants] }
    
    # Sort tenants for consistent UI
    sorted_tenant_names = (["Global / Shared"] if "Global / Shared" in tenants_with_vs else []) + \
                          sorted([t for t in tenants_with_vs.keys() if t != "Global / Shared"])

    for t_name in sorted_tenant_names:
        info = tenants_with_vs[t_name]
        mapping = v_map.get(t_name, {})
        
        strategy = mapping.get("strategy") or "create_new"
        target = mapping.get("target") or (t_name if strategy == "create_new" else "root")
        is_mapped = "strategy" in mapping
        
        if is_mapped: mapped_count += 1
        if strategy == "create_new": create_new_count += 1
        else: merge_count += 1
        
        target_vdom_usage.setdefault(target, []).append(t_name)
        
        tenant_mappings.append({
            "tenant": t_name,
            "vs_count": info["vs_count"],
            "vs_names": info["vs_names"],
            "vip_list": sorted(list(set(info["vip_list"]))),
            "current_strategy": strategy,
            "current_target": target,
            "current_note": mapping.get("note", ""),
            "is_mapped": is_mapped,
        })


    # 4. Conflict Detection
    # If multiple tenants map to the same VDOM, check for VS name collisions
    conflict_count = 0
    for target, tenants in target_vdom_usage.items():
        if len(tenants) > 1:
            # Shared target - check for name collisions
            all_vs_names = []
            for t in tenants:
                all_vs_names.extend(tenants_with_vs[t]["vs_names"])
            
            # Simple collision check for now
            from collections import Counter
            counts = Counter(all_vs_names)
            collisions = [name for name, count in counts.items() if count > 1]
            
            if collisions:
                conflict_count += len(collisions)
                # Mark participating tenant mappings
                for tm in tenant_mappings:
                    if tm["tenant"] in tenants:
                        tm["is_shared_target"] = True
                        tm["shared_with"] = [other for other in tenants if other != tm["tenant"]]
                        tm["conflict_names"] = collisions

    # 5. Suppressed Tenants (those with NO VSes)
    all_tenants = set()
    # Attempt to find all tenants from raw inventory if available
    inventory = manifest.get("raw_key_inventory", {}).get("Tenant", {})
    if inventory and isinstance(inventory, dict):
        # We don't have the names here, just the count. 
        # Better to get from tenant_filter available_tenants
        pass
    
    available_tenants = manifest.get("tenant_filter", {}).get("available_tenants", [])
    suppressed_tenants = [t for t in available_tenants if t.upper() not in [tn.upper() for tn in tenants_with_vs.keys()]]

    return {
        "tenant_mappings": tenant_mappings,
        "connection_status": "offline", # Placeholder for Phase 3 connectivity check
        "available_vdoms": available_vdoms,
        "available_vdoms_source": vdom_source,
        "unmapped_count": len(tenants_with_vs) - mapped_count,
        "conflict_count": conflict_count,
        "suppressed_tenant_count": len(suppressed_tenants),
        "suppressed_tenant_names": suppressed_tenants,
        "summary": {
            "total_tenants": len(tenants_with_vs),
            "mapped_tenants": mapped_count,
            "create_new_count": create_new_count,
            "merge_count": merge_count,
        }
    }





def export_impact_summary_csv(dirs: dict, env: str, manifest: dict) -> str:
    """Generate a structured CSV export of the migration impact summary."""
    state_dir = Path(dirs.get("state_dir", "state"))
    # Bubbling isolation
    snap_path = resolve_env_file(state_dir, env, "-raw-snapshot.json")
    raw_snapshot = read_json(snap_path) if snap_path else {}
    raw_snapshot = flatten_avi_snapshot(raw_snapshot)
    
    raw_inventory = manifest.get("raw_key_inventory", {}) or {}
    raw_decisions = manifest.get("raw_key_decisions", {}) or {}
    
    from core.resolver import ConfigurationGraph
    graph = ConfigurationGraph(raw_snapshot)
    shared_usage = graph.get_shared_usage()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Tenant", "Object Type", "Name", "Decision"])
    
    for rk, items in raw_snapshot.items():
        if rk not in raw_inventory: continue
        if not isinstance(items, list): continue
        
        inv = raw_inventory.get(rk, {})
        rd = raw_decisions.get(rk, {})
        if isinstance(rd, str): rd = {"decision": rd}
        decision = rd.get("decision") or inv.get("default_decision") or "context_only"
        
        for item in items:
            if not isinstance(item, dict): continue
            name = item.get("name", "unnamed")
            t_raw = graph.get_tenant(item).upper()
            
            # Use the same normalized tenant name as the UI
            row_tenant = "Global / Shared" if t_raw == "ADMIN" else t_raw
            
            writer.writerow([row_tenant, rk, name, decision])
            
    return output.getvalue()

def _build_import_key_detail(
    raw_key: str,
    inventory_item: dict[str, Any],
    current_decision: str,
    raw_value: Any,
    graph: Any = None, # ConfigurationGraph
    reference_counts: dict[tuple[str, str], int] | None = None,
) -> dict[str, Any]:
    group = inventory_item.get("group", "context")
    mapped_family = inventory_item.get("mapped_family")
    sample_names = _extract_sample_names(raw_value)
    preview_payload = _redact_for_preview(raw_value)
    preview_json = json.dumps(preview_payload, indent=2, ensure_ascii=False) if preview_payload is not None else "null"
    if len(preview_json) > 4000:
        preview_json = preview_json[:4000].rstrip() + "\n..."

    summary_lines = _summarize_raw_value(raw_key, raw_value, inventory_item)
    search_parts = [raw_key]
    if mapped_family:
        search_parts.append(mapped_family)
        search_parts.append(mapped_family.replace("_", " "))
    
    search_parts.extend(sample_names)
    search_parts.extend(summary_lines)

    # Associated Content Tracking (Deep Tracing)
    associations = []
    shared_objects = []
    if graph and isinstance(raw_value, list) and raw_value:
        # Trace dependencies for all items in this subset
        all_refs = {} # { "Type": set(names) }
        for item in raw_value:
            if not isinstance(item, dict): continue
            deps = graph.get_dependencies(raw_key, item) # We assume this method exists or I'll add it
            for d_type, d_names in deps.items():
                all_refs.setdefault(d_type, set()).update(d_names)
        
        for d_type, d_set in sorted(all_refs.items()):
            sample = sorted(d_set)[:4]
            label = f"{len(d_set)} x {d_type}"
            if sample:
                label += f" ({', '.join(sample)}"
                if len(d_set) > len(sample):
                    label += f", +{len(d_set) - len(sample)} more"
                label += ")"
            associations.append(label)

        for item in raw_value:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            consumers = sorted(graph.get_shared_tenants(raw_key, name))
            ref_count = (reference_counts or {}).get((raw_key, name), 0)
            if not consumers and ref_count <= 1:
                continue
            shared_objects.append({
                "name": name,
                "tenant_count": len(consumers),
                "tenants": consumers,
                "reference_count": ref_count,
            })

    shared_lines = []
    for obj in shared_objects[:8]:
        if obj["tenant_count"]:
            line = f"{obj['name']} shared with {obj['tenant_count']} tenant(s)"
            if obj["reference_count"]:
                line += f", referenced {obj['reference_count']} time(s)"
        else:
            line = f"{obj['name']} reused {obj['reference_count']} time(s) in related objects"
        shared_lines.append(line)

    search_parts.extend(associations)
    search_parts.extend(shared_lines)
    search_blob = " | ".join(filter(None, search_parts)).lower()

    return {
        "raw_key": raw_key,
        "group": group,
        "count": inventory_item.get("count", 0),
        "value_type": inventory_item.get("value_type", "scalar"),
        "mapped_family": mapped_family,
        "default_decision": inventory_item.get("default_decision", "context_only"),
        "current_decision": current_decision,
        "decision_options": _decision_options_for_group(group),
        "sample_names": sample_names,
        "summary_lines": summary_lines,
        "associations": associations,
        "shared_objects": shared_objects,
        "shared_object_count": len(shared_objects),
        "shared_lines": shared_lines,
        "preview_json": preview_json,
        "guidance": _guidance_for_key(raw_key, inventory_item, raw_value),
        "search_blob": search_blob,
    }


def _decision_options_for_group(group: str) -> list[dict[str, str]]:
    if group == "pipeline":
        return [
            {"value": "include_in_pipeline", "label": "Include in pipeline"},
            {"value": "context_only", "label": "Keep as context only"},
        ]
    return [
        {"value": "context_only", "label": "Keep as context only"},
        {"value": "exclude", "label": "Exclude from candidate"},
    ]


def _extract_sample_names(raw_value: Any, limit: int = 500) -> list[str]:
    results: list[str] = []
    if isinstance(raw_value, list):
        for item in raw_value:
            if not isinstance(item, dict):
                if item is not None:
                    results.append(str(item))
                if len(results) >= limit:
                    break
                continue
            label = (
                item.get("name")
                or item.get("uuid")
                or item.get("url")
                or item.get("evt")
            )
            if label:
                results.append(str(label))
            if len(results) >= limit:
                break
    elif isinstance(raw_value, dict):
        results.extend(list(raw_value.keys())[:limit])
    return results


def _summarize_raw_value(raw_key: str, raw_value: Any, inventory_item: dict[str, Any]) -> list[str]:
    count = inventory_item.get("count", 0)
    value_type = inventory_item.get("value_type", "scalar")
    mapped_family = inventory_item.get("mapped_family")
    lines = [f"{count} item(s) detected as {value_type}."]
    if mapped_family:
        lines.append(f"Maps into discovery family `{mapped_family}`.")

    if raw_key == "VSDataScriptSet" and isinstance(raw_value, list):
        script_total = 0
        event_names: set[str] = set()
        line_total = 0
        for entry in raw_value:
            scripts = entry.get("datascript", []) if isinstance(entry, dict) else []
            script_total += len(scripts)
            for script in scripts:
                if isinstance(script, dict):
                    if script.get("evt"):
                        event_names.add(str(script["evt"]))
                    line_total += len(str(script.get("script", "")).splitlines())
        lines.append(f"{script_total} script block(s), {line_total} total line(s).")
        if event_names:
            lines.append("Events: " + ", ".join(sorted(event_names)))
    elif isinstance(raw_value, list) and raw_value and isinstance(raw_value[0], dict):
        named = sum(1 for item in raw_value if isinstance(item, dict) and item.get("name"))
        refs = sum(1 for item in raw_value if isinstance(item, dict) and item.get("uuid"))
        if named:
            lines.append(f"{named} item(s) have explicit names.")
        if refs:
            lines.append(f"{refs} item(s) include UUIDs for traceability.")
    elif isinstance(raw_value, dict) and raw_value:
        lines.append(f"Top-level fields: {', '.join(list(raw_value.keys())[:8])}")
    return lines


def _guidance_for_key(raw_key: str, inventory_item: dict[str, Any], raw_value: Any) -> str:
    group = inventory_item.get("group", "context")
    mapped_family = inventory_item.get("mapped_family")
    
    if raw_key == "VSDataScriptSet":
        return (
            "DataScripts provide complex logic that cannot be automatically converted. "
            "Keep as context to ensure the code is available for manual FortiADC Scripting later."
        )
    
    if group == "pipeline" and mapped_family:
        return (
            f"This object feeds the `{mapped_family}` discovery family. "
            "Choose 'Include' to allow the tool to transform it into a candidate FortiADC configuration."
        )
    
    if group == "gslb_related":
        return (
            "GSLB objects influence how traffic is routed between sites. "
            "These are kept for context to aid in the manual design of the multi-site deployment."
        )
    
    if group == "network" or raw_key in {"VrfContext", "Network", "VrfContextRuntime", "NetworkRuntime"}:
        return (
            "Network and Routing objects (VRFs, Interfaces, Static Routes) define the environment context. "
            "These are kept for reference; manual FortiADC network/VDOM provisioning is typically required as "
            "automated 1:1 translation is out of current scope."
        )
    
    if raw_key == "VsVip":
        return (
            "VsVip defines the IP/Port binding for a Virtual Service. During transformation, the tool extracts "
            "this data directly from the VirtualService object. The VsVip itself is kept as Context for "
            "reference and verification."
        )
    
    if group == "noise":
        return (
            "This key typically contains internal metadata or test configurations. "
            "Exclude is recommended unless you need it for deep forensic review."
        )
    
    return (
        "This object provides supporting environment context. It does not map to a standard FortiADC migration object, "
        "but keeping it as 'Context' ensures it remains visible during analysis."
    )


def _redact_for_preview(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "<truncated>"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= 18:
                result["..."] = "<additional fields omitted>"
                break
            lowered = str(key).lower()
            if lowered in SENSITIVE_PREVIEW_FIELDS:
                result[key] = "<redacted>"
            else:
                result[key] = _redact_for_preview(item, depth + 1)
        return result
    if isinstance(value, list):
        trimmed = value[:3]
        result = [_redact_for_preview(item, depth + 1) for item in trimmed]
        if len(value) > 3:
            result.append(f"<{len(value) - 3} additional item(s) omitted>")
        return result
    if isinstance(value, str):
        if len(value) > 280:
            return value[:280] + "..."
        return value
    return value


def get_environment_history(dirs: dict, env: str) -> list[dict]:
    """Return the full audit history for a specific environment."""
    state_dir = dirs.get("state_dir", "state")
    ledger = _read_ledger(state_dir, env)
    return ledger.get("audit", [])


def delete_environment(dirs: dict, env: str) -> bool:
    """Delete all artifacts associated with an environment."""
    deleted_any = False
    import shutil
    
    # Root directories to check
    # Logs gets a special treatment (subdir wipe)
    roots = {
        "discovery_dir": "discovery",
        "state_dir":     "state",
        "fortiadc_dir":  "fortiadc",
        "reports_dir":   "reports",
        "logs_dir":      "logs"
    }

    for key, default in roots.items():
        base = Path(dirs.get(key, default))
        if not base.exists():
            continue
        
        # 1. Try organized subdirectory (e.g. state/d2/)
        subdir = base / env
        if subdir.exists() and subdir.is_dir():
            try:
                print(f"[DELETE] Purging environment directory: {subdir}")
                shutil.rmtree(subdir)
                deleted_any = True
            except Exception as e:
                print(f"[DELETE] Failed to purge directory {subdir}: {e}")

        # 2. Try flat files / legacy artifacts
        # We use a broad glob and canonical_env_name check
        for f in base.glob("*"):
            if not f.is_file():
                continue
            
            # Safety: NEVER delete the config file itself or the ledger if we are just scanning
            if f.name == "config.yaml" or f.name == "config.example.yaml":
                continue

            if canonical_env_name(f.name) == env or f.name.startswith(f"{env}-"):
                try:
                    print(f"[DELETE] Unlinking artifact: {f}")
                    f.unlink()
                    deleted_any = True
                except Exception as e:
                    print(f"[DELETE] Failed to unlink {f}: {e}")

        # 2b. Recursive leftovers under grouped folders or nested logs
        for f in base.glob("**/*"):
            if not f.is_file():
                continue
            if canonical_env_name(f.name) != env and f.parent.name != env:
                continue
            try:
                print(f"[DELETE] Purging nested artifact: {f}")
                f.unlink()
                deleted_any = True
            except Exception as e:
                print(f"[DELETE] Failed to purge nested artifact {f}: {e}")

        for d in sorted(base.glob("**/*"), reverse=True):
            if not d.is_dir():
                continue
            if d.name != env:
                continue
            try:
                print(f"[DELETE] Removing nested environment directory: {d}")
                shutil.rmtree(d)
                deleted_any = True
            except Exception as e:
                print(f"[DELETE] Failed to remove nested environment directory {d}: {e}")

    # 3. Remove from config.yaml if it exists as a predefined environment
    import re
    config_path = Path(dirs.get("config_path", "config.yaml"))
    if config_path.exists():
        try:
            content = config_path.read_text(encoding="utf-8")
            
            # --- Safety: Create a backup before modifying config ---
            bak_path = config_path.with_suffix(".yaml.bak")
            bak_path.write_text(content, encoding="utf-8")
            
            lines = content.splitlines()
            new_lines = []
            in_target_env = False
            for line in lines:
                # Match the start of the target environment's list entry
                if re.match(rf'^[ \t]*-[ \t]*name:[ \t]*["\']?{re.escape(env)}["\']?[ \t]*(?:#.*)?$', line):
                    in_target_env = True
                    print(f"[DELETE] Removing environment '{env}' from config.yaml")
                    continue
                
                if in_target_env:
                    # IMPROVED: Only stop skipping if we hit a new environment or a new top-level key
                    if re.match(r'^[ \t]*-[ \t]*name:', line) or re.match(r'^[A-Za-z0-9_]+:', line) or line.startswith('# ──'):
                        in_target_env = False
                    else:
                        continue # still in target env, ignore lines

                if not in_target_env:
                    new_lines.append(line)
            
            new_content = "\n".join(new_lines)
            if content.endswith("\n") and not new_content.endswith("\n"):
                new_content += "\n"

            if new_content != content:
                config_path.write_text(new_content, encoding="utf-8")
                deleted_any = True
        except Exception as e:
            print(f"[DELETE] Failed to update config.yaml: {e}")

    return deleted_any


def run_readonly_action(dirs: dict, action: str, env: str) -> dict:
    """
    Run a read-only CLI action as a subprocess.
    Allowed actions only (caller already validated).
    Returns stdout truncated to 4000 chars.
    """
    tool_root = dirs.get("tool_root", ".")
    config_path = dirs.get("config_path", "config.yaml")

    cmd_map = {
        "discover_dry":      [sys.executable, "migrate.py", "discover",
                               "--config", config_path, "--env", env],
        "analyse":           [sys.executable, "migrate.py", "analyse",
                               "--input", f"discovery/{env}.json"],
        "ops_check":         [sys.executable, "migrate.py", "ops-check", "--env", env],
        "llm_pack_full":     [sys.executable, "migrate.py", "llm-pack", "--type", "full",
                               "--input", f"discovery/{env}.json"],
        "llm_pack_next_steps": [sys.executable, "migrate.py", "llm-pack",
                                 "--type", "next_steps",
                                 "--input", f"discovery/{env}.json"],
    }

    cmd = cmd_map.get(action)
    if not cmd:
        return {"error": f"Unknown action: {action}"}

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=120, cwd=tool_root,
        )
        return {
            "action":  action,
            "env":     env,
            "stdout":  proc.stdout[:4000],
            "stderr":  proc.stderr[:1000],
            "rc":      proc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": "Action timed out after 120s"}
    except Exception as e:
        return {"error": str(e)}


def import_avi_config(
    dirs: dict,
    file_content: str,
    env_name: str,
    payload_type: str = "normalized_discovery",
) -> dict:
    """
    Stage an uploaded Avi JSON payload for later user-approved commit.

    v2 behavior:
      - capture every top-level key and seed decision manifest defaults
      - build candidate discovery artifact (filtered by default decisions)
      - do NOT write discovery/<env>.json and do NOT mark ledger discover as done
      - return decision_required so UI can confirm in Decision Center
    """
    from core.avi_import import is_raw_avi_export, normalize_raw_avi_export, DISCOVERY_KEYS, RAW_TO_DISCOVERY_MAP
    from core.events import EventBus
    from core.import_classifier import classify_raw_keys
    
    discovery_dir = Path(dirs.get("discovery_dir", "discovery"))
    discovery_dir.mkdir(parents=True, exist_ok=True)
    
    logs_dir = Path(dirs.get("logs_dir", "logs"))
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{env_name}-import-stage.jsonl"
    
    bus = EventBus(log_path=log_path) # Persistent bus for logging the import event

    try:
        if payload_type not in {"normalized_discovery", "raw_avi_export"}:
            return {
                "success": False,
                "error": (
                    f"Unsupported payload_type '{payload_type}'. "
                    "Supported values: normalized_discovery, raw_avi_export."
                ),
            }

        raw_payload = json.loads(file_content)
        classifier = classify_raw_keys(raw_payload)

        # Normalize only for candidate discovery generation (commit is gated later).
        normalized_from_raw = is_raw_avi_export(raw_payload) or payload_type == "raw_avi_export"

        state_dir = Path(dirs.get("state_dir", "state"))
        # Environment Bubbling: always write new imports to their own subdirectory
        env_state_dir = ensure_env_dir(state_dir, env_name)
        raw_snapshot_path = env_state_dir / "raw-snapshot.json"
        candidate_path = env_state_dir / "candidate-discovery.json"

        # Persist raw snapshot so commit can deterministically rebuild candidate discovery.
        raw_snapshot_path.write_text(json.dumps(raw_payload, indent=2, default=str), encoding="utf-8")

        manifest_store = DecisionManifestStore(env_name, state_dir=str(state_dir))
        manifest = manifest_store.load()

        snapshot_hash = str(hash(file_content))
        if manifest.get("meta", {}).get("based_on_snapshot_hash") != snapshot_hash:
            manifest.setdefault("meta", {})["based_on_snapshot_hash"] = snapshot_hash
            manifest["resolved_import_scope"] = False
            manifest["raw_key_inventory"] = {}
            manifest["raw_key_decisions"] = {}

        # Seed/refresh raw key inventory & decisions.
        manifest["raw_key_inventory"] = classifier.get("inventory", {})
        raw_decisions = manifest.setdefault("raw_key_decisions", {})

        for raw_key, inv in manifest["raw_key_inventory"].items():
            default_decision = inv.get("default_decision", "context_only")
            if raw_key not in raw_decisions or not isinstance(raw_decisions.get(raw_key), dict):
                raw_decisions[raw_key] = {
                    "decision": default_decision,
                    "rationale": "Auto-seeded during import; confirm in Decision Center.",
                }
                continue

            # Ensure decision value is always present; keep user edits if snapshot matches.
            raw_decisions[raw_key].setdefault("decision", default_decision)
            raw_decisions[raw_key].setdefault("rationale", "")

        manifest["resolved_import_scope"] = False  # staging always resets unless confirmed

        # ── Seed tenant_filter from the raw snapshot ───────────────────────
        # Scan every list in the raw payload for tenant_ref values to build
        # the full set of available tenants the operator can choose from.
        import re as _re
        _global_prefixes = ("Waf", "SSL", "PKI", "Auth", "AlertConfig",
                            "ApplicationProfile", "ApplicationPersistenceProfile",
                            "VSDataScriptSet", "ErrorPage", "Bot", "IPReputation")
        _found_tenants: set[str] = set()
        for _rk, _val in raw_payload.items():
            if not isinstance(_val, list) or _rk.startswith(_global_prefixes):
                continue
            for _item in _val:
                if not isinstance(_item, dict): continue
                _t_ref = _item.get("tenant_ref", "")
                _t_m = _re.search(r"name=([^&]+)", str(_t_ref))
                _t_raw = _t_m.group(1) if _t_m else None
                if _t_raw and _t_raw.lower() != "admin":
                    _found_tenants.add(_t_raw.upper())

        _tf = manifest.setdefault("tenant_filter", {"enabled": False, "available_tenants": [], "included_tenants": []})
        _tf["available_tenants"] = sorted(_found_tenants)
        # Pre-populate included_tenants only when it is empty (first time)
        if not _tf.get("included_tenants"):
            _tf["included_tenants"] = list(_tf["available_tenants"])
        # ──────────────────────────────────────────────────────────────────

        # Mapping approvals are deterministic from the mapping tables.
        manifest.setdefault("mapping_approvals", {"resolved": False, "items": {}})
        manifest["mapping_approvals"]["resolved"] = True
        manifest["mapping_approvals"].setdefault("items", {})
        manifest["mapping_approvals"]["items"]["raw_to_discovery"] = {
            "decision": "auto_normalize_for_candidate" if normalized_from_raw else "already_normalized_input",
            "rationale": "Candidate discovery generation; final commit is gated by resolved_import_scope.",
        }

        manifest_store.save(manifest)

        # Build candidate discovery using default decisions
        candidate = _build_candidate_discovery_from_raw_and_decisions(
            raw_payload=raw_payload,
            env_name=env_name,
            raw_key_inventory=manifest["raw_key_inventory"],
            raw_key_decisions=manifest["raw_key_decisions"],
            tenant_filter=manifest.get("tenant_filter")
        )
        candidate_path.write_text(json.dumps(candidate, indent=2, default=str), encoding="utf-8")

        # Start ledger phase so UI shows we're at discovery/import stage.
        from core.state_ledger import StateLedger
        ledger = StateLedger(env_name, state_dir=str(state_dir))
        ledger.phase_start("discover")

        return {
            "success": False,
            "decision_required": True,
            "env": env_name,
            "candidate_path": str(candidate_path),
            "manifest_path": str(manifest_store.path),
            "raw_key_total": classifier.get("raw_key_total", 0),
            "normalized_from_raw": normalized_from_raw,
            "redirect_to": f"/decisions/{env_name}/import-scope",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        bus.close()


def _build_candidate_discovery_from_raw_and_decisions(
    raw_payload: dict,
    env_name: str,
    raw_key_inventory: dict,
    raw_key_decisions: dict,
    tenant_filter: dict | None = None,
) -> dict:
    """Build strict discovery candidate from raw payload + raw-key decisions.

    Candidate always contains all DISCOVERY_KEYS with correct types.
    Only families controlled by raw keys with decision `include_in_pipeline`
    are kept; all others are emptied.
    """
    from core.avi_import import DISCOVERY_KEYS, RAW_TO_DISCOVERY_MAP, is_raw_avi_export, normalize_raw_avi_export

    # 1) Normalize to strict discovery skeleton for all known families.
    if is_raw_avi_export(raw_payload):
        normalized = normalize_raw_avi_export(raw_payload, env_name=env_name, source_file="import_candidate")
    else:
        # Already normalized discovery (or partially). Ensure strict key set.
        normalized = dict(raw_payload)
        if not isinstance(normalized.get("_meta"), dict):
            normalized["_meta"] = {}
        for key in DISCOVERY_KEYS:
            if key not in normalized:
                normalized[key] = [] if key != "_meta" else {}
            if key != "_meta" and not isinstance(normalized.get(key), list):
                normalized[key] = []

    # 2) Invert mapping tables: which raw keys can control which discovery families?
    family_to_raw_keys: dict[str, set[str]] = {
        fam: {fam} for fam in DISCOVERY_KEYS if fam != "_meta"
    }
    for raw_key, family in RAW_TO_DISCOVERY_MAP.items():
        family_to_raw_keys.setdefault(family, set()).add(raw_key)

    # 3) Apply object-type decisions.
    candidate = dict(normalized)
    for family in DISCOVERY_KEYS:
        if family == "_meta":
            continue
        controlling_raw_keys = family_to_raw_keys.get(family, set())
        include = False
        for rk in controlling_raw_keys:
            d = raw_key_decisions.get(rk)
            if isinstance(d, str):
                d = {"key": rk, "decision": "include_in_pipeline"}
            if isinstance(d, dict) and d.get("decision") == "include_in_pipeline":
                include = True
                break
        if not include:
            candidate[family] = []

    # 4) Apply tenant and individual VS filter (when enabled).
    # Strict containment is derived from traced roots + dependencies so only the
    # selected tenant/VS scope and the objects it actually needs survive.
    _tf = tenant_filter or raw_payload.get("_tenant_filter") or {}
    _included_vses = {str(v).strip() for v in (_tf.get("included_vses") or []) if str(v).strip()}
    _included_tenants = {str(t).strip().upper() for t in (_tf.get("included_tenants") or []) if str(t).strip()}

    if (_tf.get("enabled") and _included_tenants) or _included_vses:
        from core.resolver import ConfigurationGraph

        graph = ConfigurationGraph(raw_payload)
        traced_targets: dict[str, set[str]] = {}
        queue: list[tuple[str, dict]] = []
        seen: set[tuple[str, str]] = set()

        def _add_traced(raw_key: str, item: dict) -> None:
            if not isinstance(item, dict):
                return
            name = str(item.get("name", "")).strip()
            if not name:
                return
            item_id = (raw_key, name)
            if item_id in seen:
                return
            seen.add(item_id)
            family = RAW_TO_DISCOVERY_MAP.get(raw_key)
            if family:
                traced_targets.setdefault(family, set()).add(name)
            queue.append((raw_key, item))

        for raw_key, items in graph._raw.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                tenant_name = graph.get_tenant(item).upper()
                is_global_match = (
                    "GLOBAL / SHARED" in _included_tenants
                    and (tenant_name == "ADMIN" or raw_key.startswith(GLOBAL_KEY_PREFIXES))
                )
                if tenant_name in _included_tenants or is_global_match:
                    _add_traced(raw_key, item)
                if raw_key == "Tenant" and name.upper() in _included_tenants:
                    _add_traced(raw_key, item)
                if raw_key == "VirtualService" and name in _included_vses:
                    _add_traced(raw_key, item)

        while queue:
            raw_key, item = queue.pop(0)
            deps = graph.get_dependencies(raw_key, item)
            for dep_type, dep_names in deps.items():
                for dep_name in dep_names:
                    target = graph.get_by_name(dep_type, dep_name)
                    if target:
                        _add_traced(dep_type, target)

        for family in DISCOVERY_KEYS:
            if family == "_meta":
                continue
            items = candidate.get(family, [])
            if not isinstance(items, list):
                continue
            allowed_names = traced_targets.get(family, set())
            filtered = []
            for obj in items:
                if not isinstance(obj, dict):
                    continue
                obj_name = str(obj.get("name", "")).strip()
                if obj_name and obj_name in allowed_names:
                    filtered.append(obj)
            candidate[family] = filtered

    # Final strict typing sanity
    for k in DISCOVERY_KEYS:
        if k == "_meta":
            candidate.setdefault("_meta", {})
            if not isinstance(candidate["_meta"], dict):
                candidate["_meta"] = {}
        else:
            if not isinstance(candidate.get(k), list):
                candidate[k] = []

    return candidate


def create_subtask(dirs: dict, parent_env: str, sub_name: str, included_tenants: list[str], included_vses: list[str]) -> dict:
    """Create a sub-task environment from a parent environment and automatically commit it to start at Analysis."""
    import shutil
    from core.decision_manifest import DecisionManifestStore
    from services.common import resolve_env_file, ensure_env_dir

    included_tenants = [t for t in included_tenants if str(t).strip()]
    included_vses = [v for v in included_vses if str(v).strip()]
    if not included_tenants and not included_vses:
        return {"success": False, "error": "Select at least one tenant or virtual service before creating a sub-task."}

    subtask_id = f"st-{uuid.uuid4().hex[:8]}"
    new_env = f"{parent_env}-sub-{sub_name}-{subtask_id}"
    state_dir = Path(dirs.get("state_dir", "state"))

    parent_raw_snapshot_path = resolve_env_file(state_dir, parent_env, "-raw-snapshot.json")
    if not parent_raw_snapshot_path or not parent_raw_snapshot_path.exists():
        return {"success": False, "error": f"Parent snapshot not found for {parent_env}"}

    try:
        # 1. Create new env directory
        new_env_dir = ensure_env_dir(state_dir, new_env)
        
        # 2. Copy and enrich raw snapshot
        new_raw_snapshot_path = new_env_dir / "raw-snapshot.json"
        raw_data = json.loads(parent_raw_snapshot_path.read_text(encoding="utf-8"))
        if isinstance(raw_data, dict):
            raw_data["_subtask_id"] = subtask_id
            raw_data["_parent_env"] = parent_env
        new_raw_snapshot_path.write_text(json.dumps(raw_data, indent=2, default=str), encoding="utf-8")


        # 3. Initialize Manifest
        parent_store = DecisionManifestStore(parent_env, state_dir=str(state_dir))
        parent_manifest = parent_store.load()

        new_store = DecisionManifestStore(new_env, state_dir=str(state_dir))
        new_manifest = new_store.load()

        new_manifest.setdefault("meta", {})["subtask_id"] = subtask_id
        new_manifest["meta"]["parent_env"] = parent_env
        new_manifest["raw_key_inventory"] = parent_manifest.get("raw_key_inventory", {})
        new_manifest["raw_key_decisions"] = parent_manifest.get("raw_key_decisions", {})
        new_manifest["tenant_filter"] = {
            "enabled": True,
            "available_tenants": parent_manifest.get("tenant_filter", {}).get("available_tenants", []),
            "included_tenants": included_tenants,
            "included_vses": included_vses
        }
        new_manifest["resolved_import_scope"] = True
        new_store.save(new_manifest)

        # 4. Call commit_import_scope to generate discovery and initial analysis
        res = commit_import_scope(dirs, new_env)
        if not res.get("success"):
            return {"success": False, "error": f"Failed to commit sub-task: {res.get('error')}"}

        # 4b. Force bypass Discovery gate (mark as completed in ledger)
        from core.state_ledger import StateLedger
        sub_ledger = StateLedger(new_env, state_dir=str(state_dir))
        sub_ledger.phase_done("discover", "Automated bypass for sub-task orchestration")

        # 5. Update Parent Manifest to track sub-task
        parent_tf = parent_manifest.setdefault("tenant_filter", {})
        sub_tasks = parent_tf.setdefault("sub_tasks", [])
        
        # Remove existing with same subtask_id or env name if exists, then append
        sub_tasks = [st for st in sub_tasks if st.get("id") != subtask_id and st.get("env") != new_env]
        
        sub_tasks.append({
            "id": subtask_id,
            "name": sub_name,
            "env": new_env,
            "included_tenants": included_tenants,
            "included_vses": included_vses,
            "created_at": new_manifest["meta"].get("created_at")
        })
        parent_tf["sub_tasks"] = sub_tasks
        parent_store.save(parent_manifest)

        return {"success": True, "new_env": new_env}
    except Exception as e:
        return {"success": False, "error": f"Error creating subtask: {str(e)}"}

def commit_import_scope(dirs: dict, env_name: str) -> dict:
    """Commit staged candidate discovery after user confirms resolved_import_scope."""
    from core.decision_manifest import ensure_import_scope_gate
    from core.avi_import import import_discovery_payload
    from core.events import EventBus

    state_dir = Path(dirs.get("state_dir", "state"))
    raw_snapshot_path = resolve_env_file(state_dir, env_name, "-raw-snapshot.json")
    candidate_path = ensure_env_dir(state_dir, env_name) / "candidate-discovery.json"

    manifest_store = DecisionManifestStore(env_name, state_dir=str(state_dir))
    manifest = manifest_store.load()

    if not raw_snapshot_path.exists():
        return {"success": False, "error": "Missing raw snapshot; stage import again."}

    raw_payload = json.loads(raw_snapshot_path.read_text(encoding="utf-8"))

    # Gate validation
    gate = ensure_import_scope_gate(manifest, payload={})
    if not gate.ok:
        return {
            "success": False,
            "error": gate.message,
            "missing": gate.missing,
        }

    # Rebuild candidate based on current decisions (user may have changed selections).
    # Embed tenant_filter into raw_payload so the builder can apply it.
    _tf_for_build = manifest.get("tenant_filter", {"enabled": False, "available_tenants": [], "included_tenants": []})
    raw_payload_with_filter = dict(raw_payload)
    raw_payload_with_filter["_tenant_filter"] = _tf_for_build
    candidate = _build_candidate_discovery_from_raw_and_decisions(
        raw_payload=raw_payload_with_filter,
        env_name=env_name,
        raw_key_inventory=manifest.get("raw_key_inventory", {}),
        raw_key_decisions=manifest.get("raw_key_decisions", {}),
    )
    candidate_path.write_text(json.dumps(candidate, indent=2, default=str), encoding="utf-8")

    logs_dir = Path(dirs.get("logs_dir", "logs"))
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{env_name}-import-commit.jsonl"
    bus = EventBus(log_path=log_path)

    try:
        output_path = Path(dirs.get("discovery_dir", "discovery")) / f"{env_name}.json"
        prior_commit_exists = output_path.exists()
        if prior_commit_exists:
            from core.state_ledger import StateLedger
            ledger = StateLedger(env_name, state_dir=str(state_dir))
            ledger._data["unsupported"] = []
            ledger._write_unsupported_flat()
            ledger._data["translated"] = []
            for phase_name in ["analyse", "transform", "dry-run", "deploy", "parallel-run", "dns-cutover"]:
                ledger._data["phases"].setdefault(phase_name, {})
                ledger._data["phases"][phase_name].update({
                    "status": "pending",
                    "started_at": "",
                    "finished_at": "",
                    "operator": "",
                    "notes": "",
                })
            ledger._audit("discover", "recommit_scope_reset", {"env": env_name})
            ledger._save()

            manifest.setdefault("analysis_triage", {"resolved": False, "items": {}})
            manifest["analysis_triage"] = {"resolved": False, "items": {}}
            manifest["transform_approvals"] = {"resolved": False, "groups": {}}
            manifest["deploy_approval"] = {
                "resolved": False,
                "mode": "",
                "operator_ack": False,
                "governance_confirmed": False,
                "notes": "",
            }
            manifest["post_validation_decision"] = {
                "resolved": False,
                "action": "",
                "notes": "",
            }
            manifest_store.append_audit(manifest, "import_scope_recommitted", {"env": env_name})

            forti_config = Path(dirs.get("fortiadc_dir", "fortiadc")) / f"{env_name}-config.json"
            if forti_config.exists():
                try:
                    forti_config.unlink()
                except PermissionError:
                    pass
            for suffix in ("analysis.html", "analysis.md"):
                report_path = Path(dirs.get("reports_dir", "reports")) / f"{env_name}-{suffix}"
                if report_path.exists():
                    try:
                        report_path.unlink()
                    except PermissionError:
                        pass

        discovery = import_discovery_payload(candidate, env_name=env_name, bus=bus, source_file="import_commit")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(discovery, indent=4, default=str), encoding="utf-8")

        from core.state_ledger import StateLedger
        from core.events import Phase
        ledger = StateLedger(env_name, state_dir=str(state_dir))
        vs_count = len(discovery.get("virtual_services", []))
        bus.info(Phase.COLLECT, f"Committing import scope for {env_name}")
        ledger.phase_done("discover", f"Committed user-approved import-scope; VS count={vs_count}")

        # ── Auto-analysis: run full pipeline immediately after commit ─────
        # Best-effort — never blocks the commit if analysis fails.
        analysis_reports: dict = {}
        try:
            from analyzers.dependency_graph import build_dependency_graph, find_shared_objects
            from analyzers.compatibility import analyse_compatibility
            from analyzers.impact import assess_impact
            from core.intelligence import detect_patterns, score_migration_complexity, generate_next_steps
            from reporters.html import generate_html
            from reporters.markdown import generate_markdown
            from core.events import EventBus

            _abus = EventBus(log_path=logs_dir / f"{env_name}-auto-analyse.jsonl", verbose=False)

            # 1. Initialize graph once for all logic
            from core.resolver import ConfigurationGraph
            graph = ConfigurationGraph(discovery)
            
            vs_profiles   = build_dependency_graph(discovery)
            compat_results = analyse_compatibility(discovery, vs_profiles)
            impact_list   = assess_impact(vs_profiles, discovery, find_shared_objects(vs_profiles))
            patterns      = detect_patterns(discovery, vs_profiles, _abus)
            
            # --- Triage Auto-Population ---
            # Inject intelligence patterns and strict compatibility blockers directly into the ledger
            # so the Analysis Triage UI has immediate actionable data before Transform phase.
            from analyzers.compatibility import Compat
            for c in compat_results:
                if getattr(c, "status", None) in (Compat.MANUAL, Compat.BLOCKED, "MANUAL", "BLOCKED"):
                    ledger.add_unsupported(
                        object_type=c.object_type.upper(),
                        object_name=c.object_name,
                        reason=c.reason,
                        action="; ".join(c.actions) if c.actions else "Manual review required",
                        severity=c.status.value if hasattr(c.status, "value") else str(c.status)
                    )
            for p in patterns:
                if p.detected:
                    for obj_name in p.affected:
                        # Patterns use "VS: Name" or "Name || Info"
                        clean_name = obj_name
                        if " || " in clean_name:
                            # For P09 style groups, take the first name in the comma list after the separator
                            parts = clean_name.split(" || ")
                            sub_parts = parts[1].split(",")
                            clean_name = sub_parts[0].strip()
                        if ": " in clean_name: clean_name = clean_name.split(": ", 1)[1]
                        clean_name = clean_name.split(" (")[0].strip()
                        
                        otype = "unknown"
                        for k, items in discovery.items():
                            if not isinstance(items, list): continue
                            if any(str(i.get("name")).strip() == clean_name for i in items):
                                otype = k
                                break
                        
                        # Fallback: Inference from Pattern ID or Name if still unknown
                        if otype == "unknown":
                            p_id = p.id.upper()
                            p_name = p.name.lower()
                            if "P07" in p_id or "gslb" in p_name:
                                otype = "GslbService"
                            elif "P09" in p_id or "datascript" in p_name:
                                otype = "VSDataScriptSet"
                            elif "P01" in p_id:
                                otype = "VSDataScriptSet"
                            elif "P02" in p_id or "cert" in p_name:
                                otype = "SSLKeyAndCertificate"
                            elif "P03" in p_id or "pool" in p_name:
                                otype = "Pool"
                            elif "P05" in p_id:
                                otype = "VirtualService"
                            elif "P08" in p_id:
                                otype = "Network"
                        
                        ledger.add_unsupported(
                            object_type=otype, 
                            object_name=obj_name,
                            reason=f"[{p.id}] {p.name}: {p.explanation}",
                            action=p.recommendation,
                            severity=p.severity
                        )
            # ------------------------------

            complexity    = score_migration_complexity(vs_profiles, compat_results, patterns)
            next_steps    = generate_next_steps(patterns, compat_results, complexity)

            reports_dir = Path(dirs.get("reports_dir", "reports"))
            reports_dir.mkdir(parents=True, exist_ok=True)

            html_path = reports_dir / f"{env_name}-analysis.html"
            html_path.write_text(
                generate_html(env_name, _abus, discovery, vs_profiles,
                              compat_results, patterns, complexity, next_steps),
                encoding="utf-8",
            )

            md_path = reports_dir / f"{env_name}-analysis.md"
            md_path.write_text(
                generate_markdown(env_name, _abus, vs_profiles, compat_results,
                                  impact_list, discovery),
                encoding="utf-8",
            )

            _abus.close()
            analysis_reports = {
                "html": str(html_path),
                "markdown": str(md_path),
                "complexity_score": complexity.get("score"),
                "complexity_verbal": complexity.get("verbal"),
                "patterns_detected": len(patterns),
                "auto_vs": sum(1 for p in vs_profiles.values() if p.can_auto_migrate),
                "blocked_vs": sum(1 for p in vs_profiles.values() if p.blockers),
            }
            ledger.phase_done("analyse", notes=f"auto-analysis; reports={reports_dir}")
        except Exception as _ae:
            bus.error(Phase.ANALYSE, f"Auto-analysis failed: {str(_ae)}")
            analysis_reports = {"error": str(_ae)}
        # ─────────────────────────────────────────────────────────────────

        from core.events import Phase
        bus.info(Phase.ANALYSE, f"Import scope committed and analyzed for {env_name}")
        bus.close()

        return {
            "success": True,
            "message": f"Successfully committed {vs_count} virtual services. Auto-analysis complete.",
            "env": env_name,
            "analysis": analysis_reports
        }
    except Exception as e:
        if 'bus' in locals():
            from core.events import Phase
            bus.error(Phase.COLLECT, f"Commit failed: {str(e)}")
            bus.close()
        return {"success": False, "error": f"Commit failed: {str(e)}"}

def extract_datascripts(dirs: dict, env_name: str) -> dict:
    """Extract all scripts from VSDataScriptSet into standalone .lua files."""
    state_dir = Path(dirs.get("state_dir", "state"))
    snapshot_path = resolve_env_file(state_dir, env_name, "-raw-snapshot.json")
    if not snapshot_path or not snapshot_path.exists():
        return {"success": False, "error": "Raw snapshot missing."}
    
    import json
    raw_data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    scripts_data = raw_data.get("VSDataScriptSet", [])
    if not isinstance(scripts_data, list):
        return {"success": False, "error": "No DataScripts found."}

    out_root = Path(dirs.get("tool_root", ".")) / "scripts" / "datascripts" / env_name
    out_root.mkdir(parents=True, exist_ok=True)
    
    count = 0
    for entry in scripts_data:
        if not isinstance(entry, dict): continue
        name = entry.get("name", "unknown")
        datascripts = entry.get("datascript", [])
        if not isinstance(datascripts, list): continue

        for idx, ds in enumerate(datascripts):
            if not isinstance(ds, dict): continue
            evt = ds.get("evt", "unknown")
            code = ds.get("script_content") or ds.get("script", "")
            if not str(code).strip(): continue
            
            # Sanitized filename: Name_Event_idx.lua
            safe_name = "".join(c if c.isalnum() else "_" for c in name)
            safe_evt = "".join(c if c.isalnum() else "_" for c in str(evt))
            filename = f"{safe_name}_{safe_evt}_{idx}.lua"
            
            header = (
                "-- =============================================\n"
                f"-- Avi DataScript -> FortiADC Lua Migration\n"
                f"-- Original Name : {name}\n"
                f"-- Avi Event     : {evt}\n"
                "-- ---------------------------------------------\n"
                "-- MIGRATION HINTS (Avi -> FortiADC equivalents):\n"
                "-- avi.http.redirect()     -> HTTP:redirect()\n"
                "-- avi.http.add_header()   -> HTTP:header_insert()\n"
                "-- avi.http.get_uri()      -> HTTP:uri()\n"
                "-- avi.http.get_host()     -> HTTP:host()\n"
                "-- avi.http.get_remote_ip()-> IP:client_addr()\n"
                "-- ---------------------------------------------\n"
                "-- Reference : https://docs.fortinet.com/document/fortiadc/8.0.0/script-reference-guide/144544/overview\n"
                "-- =============================================\n\n"
            )
            
            (out_root / filename).write_text(header + code, encoding="utf-8")
            count += 1
            
    return {"success": True, "count": count, "message": f"Successfully extracted {count} scripts to scripts/datascripts/{env_name}/", "path": str(out_root)}
def run_pipeline_phase(dirs: dict, env: str, phase: str, live_execution: bool = False) -> dict:
    """
    Execute a migration phase as a subprocess.
    Updates the ledger with phase status.
    """
    from core.state_ledger import StateLedger
    from core.decision_manifest import DecisionManifestStore, ensure_phase_gate
    
    # 1. Load governance manifest and check gate
    store = DecisionManifestStore(env)
    manifest = store.load()
    gate = ensure_phase_gate(manifest, phase)
    if not gate.ok:
        return {"success": False, "error": f"Gate blocked: {gate.message}"}

    # 2. Setup Ledger and Command
    ledger = StateLedger(env)
    tool_root = dirs.get("tool_root", ".")
    config_path = dirs.get("config_path", "config.yaml")

    deploy_cmd = [sys.executable, "migrate.py", "deploy", "--fortiadc-config", f"fortiadc/{env}-config.json", "--env", env]
    if not live_execution:
        deploy_cmd.append("--dry-run")

    cmd_map = {
        "analyse":   [sys.executable, "migrate.py", "analyse", "--input", f"discovery/{env}.json"],
        "transform": [sys.executable, "migrate.py", "transform", "--input", f"discovery/{env}.json"],
        "deploy":    deploy_cmd,
    }

    cmd = cmd_map.get(phase)
    if not cmd:
        return {"success": False, "error": f"Unsupported automated phase: {phase}"}

    # 3. Execute with Ledger Tracking
    try:
        ledger.phase_start(phase)
        proc = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=180, cwd=tool_root
        )
        
        # Persist full log for UI review
        log_dir = Path(dirs.get("logs_dir", "logs")) / "executions" / env
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_filename = f"{phase}_{ts}.log"
        log_path = log_dir / log_filename
        
        full_content = f"--- STDOUT ---\n{proc.stdout}\n\n--- STDERR ---\n{proc.stderr}"
        log_path.write_text(full_content, encoding="utf-8")
        
        # Update ledger with log reference
        log_rel_path = f"executions/{env}/{log_filename}"
        
        if proc.returncode == 0:
            ledger.phase_done(phase, notes=f"Execution triggered via UI. Log: {log_rel_path}")
            log_system_event(dirs, f"Phase '{phase}' completed. Full log: {log_rel_path}", level="INFO", env_name=env)
            return {
                "success": True, 
                "message": f"Phase '{phase}' completed successfully.",
                "log_path": log_rel_path,
                "stdout": proc.stdout[:2000] # Still return snippet for quick toast
            }
        else:
            ledger.phase_failed(phase, reason=proc.stderr[:500])
            log_system_event(dirs, f"Phase '{phase}' failed. Full log: {log_rel_path}", level="ERROR", env_name=env)
            return {
                "success": False, 
                "error": f"Phase '{phase}' failed.",
                "log_path": log_rel_path,
                "details": proc.stderr[:2000]
            }
            
    except subprocess.TimeoutExpired:
        ledger.phase_failed(phase, reason="Execution timed out after 180s")
        return {"success": False, "error": "Phase execution timed out."}
    except Exception as e:
        ledger.phase_failed(phase, reason=str(e))
        return {"success": False, "error": str(e)}
