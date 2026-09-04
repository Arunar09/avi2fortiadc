"""
services/analytics_service.py
Reads discovery and state data to produce analytics summaries.
All computation is deterministic — no LLM, no randomness.
"""
from __future__ import annotations

import json
from pathlib import Path

from services.common import load_known_envs, read_json, resolve_env_file


def _load_discovery(dirs: dict, env: str) -> dict:
    """Loads discovery data, resolving hierarchy and object ownership."""
    from core.decision_manifest import DecisionManifestStore
    state_dir = Path(dirs.get("state_dir", "state"))
    
    # 1. Try Local State Discovery First (Pre-filtered for sub-tasks)
    state_disc_file = state_dir / env / "candidate-discovery.json"
    if state_disc_file.exists():
        return read_json(state_disc_file)

    # 2. Standard Discovery Fallback
    disc_file = resolve_env_file(dirs.get("discovery_dir", "discovery"), env, ".json")
    if not disc_file:
         return {}
    
    full_disc = read_json(disc_file)
    
    # 3. Handle Master (Exclusion Check)
    # If this is a Master, check if sub-tasks have claimed some of its objects
    manifest_store = DecisionManifestStore(env, state_dir=str(state_dir))
    manifest = manifest_store.load()
    if not manifest.get("meta", {}).get("parent_env"):
        from services.common import load_known_envs
        all_envs = load_known_envs(dirs)
        exclude_vses = set()
        exclude_tenants = set()
        
        for other_env in all_envs:
            if other_env == env: continue
            other_m = DecisionManifestStore(other_env, state_dir=str(state_dir)).load()
            if other_m.get("meta",{}).get("parent_env") == env:
                tf = other_m.get("tenant_filter", {})
                exclude_vses.update(tf.get("included_vses", []))
                exclude_tenants.update(tf.get("included_tenants", []))
        
        if exclude_vses or exclude_tenants:
            def _get_tname(o):
                tr = o.get("tenant_ref", "")
                if "name=" in tr: return tr.split("name=")[-1].upper()
                return str(o.get("tenant", o.get("_tenant", "admin"))).upper()

            exclude_vses_stripped = {v.strip() for v in exclude_vses}
            exclude_tenants_upper = {t.strip().upper() for t in exclude_tenants}
            
            pruned = {"_meta": full_disc.get("_meta", {})}
            for cat, items in full_disc.items():
                if not isinstance(items, list) or cat == "_meta": continue
                pruned[cat] = [
                    o for o in items
                    if o.get("name") not in exclude_vses_stripped and _get_tname(o) not in exclude_tenants_upper
                ]
            return pruned
            
    return full_disc


def _load_ledger(dirs: dict, env: str) -> dict:
    path = resolve_env_file(dirs.get("state_dir", "state"), env, "-ledger.json")
    return read_json(path) if path else {}


def _load_unsupported(dirs: dict, env: str) -> list[dict]:
    ledger = _load_ledger(dirs, env)
    unsupported = ledger.get("unsupported", [])
    if unsupported:
        return unsupported
    path = Path(dirs.get("state_dir", "state")) / env / "unsupported.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


def get_migration_summary(dirs: dict, env: str = "") -> dict:
    """
    Aggregate summary across all environments.
    Returns counts for dashboard top row.
    """
    envs = [env] if env else load_known_envs(dirs)
    total_vs = total_pools = total_certs = total_manual = total_blocked = 0
    env_statuses = {"not_started": 0, "in_progress": 0,
                    "blocked": 0, "deployed": 0}

    for env in envs:
        disc   = _load_discovery(dirs, env)
        ledger = _load_ledger(dirs, env)

        total_vs    += len(disc.get("virtual_services",  []))
        total_pools += len(disc.get("pools",             []))
        total_certs += len(disc.get("ssl_certificates",  []))

        unsupported = _load_unsupported(dirs, env)
        open_items  = [i for i in unsupported if not i.get("resolved", False)]
        total_manual  += len(unsupported)
        total_blocked += len([i for i in open_items
                               if i.get("severity") == "BLOCKED"])

        deploy_status = (ledger.get("phases", {})
                               .get("deploy", {})
                               .get("status", "pending"))
        if deploy_status == "completed":
            env_statuses["deployed"] += 1
        elif any(i.get("resolved") is False for i in open_items):
            env_statuses["blocked"]  += 1
        elif disc:
            env_statuses["in_progress"] += 1
        else:
            env_statuses["not_started"] += 1

    # Root count for reporting
    from core.decision_manifest import DecisionManifestStore
    root_envs = []
    for e in envs:
        m = DecisionManifestStore(e, state_dir=dirs.get("state_dir", "state")).load()
        if not m.get("meta", {}).get("parent_env"):
            root_envs.append(e)

    return {
        "env_count":     len(root_envs),
        "total_tasks":   len(envs),
        "total_vs":      total_vs,
        "total_pools":   total_pools,
        "total_certs":   total_certs,
        "total_manual":  total_manual,
        "total_blocked": total_blocked,
        "env_statuses":  env_statuses,
        "envs":          envs,
    }


def get_compatibility_breakdown(dirs: dict, env: str = "") -> dict:
    """
    Compatibility status counts per environment (or all if env='').
    Returns: { auto, warn, manual, blocked, total }
    """
    envs = [env] if env else load_known_envs(dirs)
    counts = {"AUTO": 0, "WARN": 0, "MANUAL": 0, "BLOCKED": 0}

    for e in envs:
        ledger = _load_ledger(dirs, e)
        for item in _load_unsupported(dirs, e):
            severity = item.get("severity", "MANUAL").upper()
            if severity == "BLOCKED":
                counts["BLOCKED"] += 1
            else:
                counts["MANUAL"] += 1

        translated = ledger.get("translated", [])
        for obj in translated:
            warnings = obj.get("warnings", [])
            if warnings:
                counts["WARN"] += 1
            else:
                counts["AUTO"] += 1

    counts["total"] = sum(counts.values())
    return counts


def get_pattern_findings(dirs: dict, env: str = "") -> list[dict]:
    """
    Return intelligence pattern findings from state ledger notes.
    Patterns were computed at analyse time and stored in ledger.
    """
    envs = [env] if env else load_known_envs(dirs)
    findings = []

    for e in envs:
        ledger = _load_ledger(dirs, e)
        analyse_phase = ledger.get("phases", {}).get("analyse", {})
        notes = analyse_phase.get("notes", "")
        # Patterns are encoded in notes as key=value; score=N; patterns=P
        score = None
        for part in str(notes).split(";"):
            part = part.strip()
            if part.startswith("score="):
                try:
                    score = int(part.split("=", 1)[1])
                except ValueError:
                    pass

        # Unsupported items indicate patterns
        unsupported = _load_unsupported(dirs, e)
        type_counts: dict[str, int] = {}
        for item in unsupported:
            ot = item.get("object_type", "unknown")
            type_counts[ot] = type_counts.get(ot, 0) + 1

        findings.append({
            "env":          e,
            "complexity":   score,
            "type_counts":  type_counts,
            "total_items":  len(unsupported),
            "open_items":   len([i for i in unsupported
                                 if not i.get("resolved", False)]),
        })

    return findings


def get_cert_status(dirs: dict, env: str = "") -> list[dict]:
    """
    Return certificate status summary from discovery data.
    """
    envs = [env] if env else load_known_envs(dirs)
    results = []

    for e in envs:
        disc  = _load_discovery(dirs, e)
        certs = disc.get("ssl_certificates", [])
        for cert in certs:
            days  = cert.get("_days_until_expiry")
            exp   = cert.get("_exportable", True)
            if days is None and exp:
                continue   # skip certs without metadata
            results.append({
                "env":        e,
                "name":       cert.get("name", "?"),
                "subject":    cert.get("_subject", ""),
                "expiry":     cert.get("_not_after", ""),
                "days_left":  days,
                "exportable": exp,
                "status": (
                    "CRITICAL" if not exp else
                    "EXPIRED"  if isinstance(days, int) and days < 0 else
                    "EXPIRING" if isinstance(days, int) and days < 30 else
                    "WARN"     if isinstance(days, int) and days < 60 else
                    "OK"
                ),
            })

    return sorted(results,
                  key=lambda c: (c["status"] != "CRITICAL",
                                 c["status"] != "EXPIRED",
                                 c["status"] != "EXPIRING",
                                 c.get("days_left") or 9999))
