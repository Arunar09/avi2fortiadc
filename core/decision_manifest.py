"""
core/decision_manifest.py
Decision-driven manifest model and gate validation helpers.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _operator() -> str:
    return getpass.getuser() or "unknown"


def _canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def compute_content_hash(data: Any) -> str:
    return hashlib.sha256(_canonical_json(data).encode("utf-8")).hexdigest()


REQUIRED_FAMILIES = [
    "_meta",
    "virtual_services",
    "pools",
    "pool_groups",
    "health_monitors",
    "ssl_certificates",
    "ssl_profiles",
    "application_profiles",
    "network_profiles",
    "persistence_profiles",
    "http_policy_sets",
    "dns_policies",
    "waf_policies",
    "auth_profiles",
    "datascripts",
    "se_groups",
    "gslb_global",
    "gslb_services",
    "gslb_geo_db",
    "alert_configs",
    "connections",
    "clouds",
    "tenants",
    "vrf_contexts",
    "ip_addr_groups",
    "ipam_dns_providers",
    "networks",
]

VALID_IMPORT_ACTIONS = {"include", "exclude", "context_only"}
VALID_TRIAGE_ACTIONS = {"accept", "override", "defer"}
VALID_POST_ACTIONS = {"proceed", "hold", "rollback"}
VALID_STATUS = {"draft", "ready_for_phase", "locked", "superseded"}

# v2 raw-key import scope decisions
VALID_RAW_IMPORT_DECISIONS = {
    "include_in_pipeline",
    "context_only",
    "exclude",
}


@dataclass
class GateResult:
    ok: bool
    message: str
    missing: list[str]


class DecisionManifestStore:
    def __init__(self, env: str, state_dir: str = "state"):
        self.env = env
        
        # 1. Resolve path (Subdirectory priority)
        from services.common import resolve_env_file, ensure_env_dir
        
        # We look for organized file first
        target_file = resolve_env_file(state_dir, env, "-decision-manifest.json")
        
        if not target_file:
            # New environment: create subdirectory
            dir_path = ensure_env_dir(state_dir, env)
            self.path = dir_path / "decision-manifest.json"
        else:
            # Existing: If it was a flat file, we migrade it to subdirectory now
            if target_file.parent == Path(state_dir):
                dir_path = ensure_env_dir(state_dir, env)
                new_path = dir_path / "decision-manifest.json"
                try:
                    import shutil
                    shutil.move(str(target_file), str(new_path))
                    self.path = new_path
                except Exception:
                    self.path = target_file # fallback
            else:
                self.path = target_file

        # Decision logs also moved to grouped logs dir
        from services.common import ensure_env_dir
        proj_root = Path(state_dir).parent
        log_dir = ensure_env_dir(proj_root / "logs", env)
        self.log_path = log_dir / "decisions.jsonl"

    def default_manifest(self, snapshot_hash: str = "") -> dict:
        return {
            "meta": {
                "env": self.env,
                "created_by": _operator(),
                "created_at": _now(),
                "updated_at": _now(),
                "version": 1,
                "based_on_snapshot_hash": snapshot_hash,
                "manifest_hash": "",
            },
            "import_scope": {},
            # v2: raw-key inventory + user decisions (all top-level keys)
            "raw_key_inventory": {},
            "raw_key_decisions": {},
            "resolved_import_scope": False,
            # tenant_filter: when enabled, only objects whose tenant_ref matches
            # an entry in included_tenants are kept in the candidate discovery.
            # "Global / Shared" is always implicitly included.
            "tenant_filter": {
                "enabled": False,
                "available_tenants": [],   # populated from raw snapshot on stage
                "included_tenants": [],    # operator-selected subset
            },
            "mapping_approvals": {"resolved": False, "items": {}},
            "vdom_mapping": {"resolved": False, "mappings": {}},
            "network_mapping": {"resolved": False, "mappings": {}},
            "analysis_triage": {"resolved": False, "items": {}},
            "transform_approvals": {"resolved": False, "groups": {}},
            "deploy_approval": {
                "resolved": False,
                "mode": "",
                "dry_run_reviewed": False,
                "cr_id": "",
                "sod_approver": "",
                "operator_ack": False,
                "governance_confirmed": False,
                "maintenance_window": False,
                "notes": "",
            },
            "post_validation_decision": {
                "resolved": False,
                "action": "",
                "notes": "",
            },
            "status": "draft",
            "audit": [],
            # Operator manual overrides: any FortiADC object payload the user
            # has explicitly edited and staged in the UI.
            # Key: "{fortiadc_path}::{mkey}"  Value: full payload dict
            "manual_overrides": {},
        }

    def load(self) -> dict:
        if not self.path.exists():
            data = self.default_manifest()
            self.save(data)
            return data
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            data = self.default_manifest()
            self.save(data)
            return data

    def save(self, manifest: dict) -> dict:
        manifest.setdefault("meta", {})
        manifest["meta"]["updated_at"] = _now()
        manifest["meta"]["manifest_hash"] = compute_content_hash(
            {k: v for k, v in manifest.items() if k != "meta"} | {"meta": {k: v for k, v in manifest["meta"].items() if k != "manifest_hash"}}
        )
        self.path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest

    def create_snapshot(self, manifest: dict, label: str = "manual") -> str:
        """Saves a timestamped copy of the current manifest for rollback support."""
        version = manifest.get("meta", {}).get("version", 1)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot_name = f"manifest.v{version}.{label}.{timestamp}.json"
        snapshot_path = self.path.parent / snapshot_name
        
        try:
            # Update version in main manifest BEFORE saving snapshot
            manifest["meta"]["version"] = version + 1
            self.save(manifest)
            
            # Now copy to snapshot
            shutil.copy2(str(self.path), str(snapshot_path))
            self.append_audit(manifest, "snapshot_created", {"name": snapshot_name, "label": label})
            return snapshot_name
        except Exception:
            return ""

    def list_snapshots(self) -> list[dict]:
        """Returns a list of available snapshots for this environment."""
        snapshots = []
        if not self.path.parent.exists():
            return []
            
        for f in self.path.parent.glob("manifest.v*.json"):
            if f == self.path:
                continue
            try:
                # We can extract version and label from filename or read the file
                # For efficiency, we'll extract from filename first
                parts = f.name.split(".")
                # format: manifest.v1.manual.20260430_093222.json
                snapshots.append({
                    "name": f.name,
                    "version": parts[1] if len(parts) > 1 else "?",
                    "label": parts[2] if len(parts) > 2 else "?",
                    "timestamp": parts[3] if len(parts) > 3 else "?",
                    "size": f.stat().st_size,
                    "created_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat()
                })
            except Exception:
                pass
        return sorted(snapshots, key=lambda x: x["created_at"], reverse=True)

    def restore_snapshot(self, snapshot_name: str) -> bool:
        """Overwrites current manifest with a snapshot."""
        snapshot_path = self.path.parent / snapshot_name
        if not snapshot_path.exists():
            return False
        try:
            shutil.copy2(str(snapshot_path), str(self.path))
            # Reload and audit
            manifest = self.load()
            self.append_audit(manifest, "snapshot_restored", {"from": snapshot_name})
            return True
        except Exception:
            return False

    def append_audit(self, manifest: dict, action: str, detail: dict | None = None) -> dict:
        entry = {
            "timestamp": _now(),
            "operator": _operator(),
            "action": action,
            "detail": detail or {},
        }
        manifest.setdefault("audit", [])
        manifest["audit"].append(entry)
        self._append_jsonl_decision_event(entry)
        return self.save(manifest)

    def save_manual_override(self, manifest: dict, fortiadc_path: str,
                             mkey: str, payload: dict, note: str = "") -> dict:
        """Store an operator-edited payload.  Key = 'path::mkey'."""
        key = f"{fortiadc_path}::{mkey}"
        manifest.setdefault("manual_overrides", {})
        old = manifest["manual_overrides"].get(key, {})
        manifest["manual_overrides"][key] = {
            "fortiadc_path": fortiadc_path,
            "mkey": mkey,
            "payload": payload,
            "note": note,
            "saved_at": _now(),
            "saved_by": _operator(),
        }
        return self.append_audit(manifest, "manual_override_saved", {
            "key": key,
            "note": note,
            "prev_mkey": old.get("mkey"),
        })

    def set_status(self, manifest: dict, status: str) -> dict:
        if status not in VALID_STATUS:
            raise ValueError(f"Invalid manifest status '{status}'")
        manifest["status"] = status
        return self.append_audit(manifest, "manifest_status_set", {"status": status})

    def _append_jsonl_decision_event(self, entry: dict) -> None:
        event = {
            "level": "INFO",
            "phase": "DECISION",
            "message": f"Decision action: {entry['action']}",
            "object_type": "decision_manifest",
            "object_name": self.env,
            "detail": entry.get("detail", {}),
            "timestamp": entry["timestamp"],
            "operator": entry["operator"],
            "env": self.env,
        }
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")


def build_import_scope_inventory(payload: dict) -> dict[str, int]:
    inventory: dict[str, int] = {}
    for key in REQUIRED_FAMILIES:
        val = payload.get(key)
        if isinstance(val, list):
            inventory[key] = len(val)
        elif isinstance(val, dict):
            inventory[key] = len(val)
        else:
            inventory[key] = 0
    return inventory


def apply_import_scope(payload: dict, import_scope: dict) -> dict:
    scoped = dict(payload)
    for key in REQUIRED_FAMILIES:
        if key == "_meta":
            continue
        item = import_scope.get(key) or {}
        if isinstance(item, str):
            item = {"key": key, "decision": "include_in_pipeline"}
        decision = item.get("decision", "")
        if decision == "exclude":
            scoped[key] = []
    return scoped


def ensure_import_scope_gate(manifest: dict, payload: dict) -> GateResult:
    # v2 all-keys gate (raw_key_inventory + resolved_import_scope)
    raw_inv = manifest.get("raw_key_inventory", {})
    raw_decisions = manifest.get("raw_key_decisions", {})
    if isinstance(raw_inv, dict) and raw_inv:
        if not manifest.get("resolved_import_scope", False):
            return GateResult(
                ok=False,
                message="Import scope gate incomplete. Confirm Import Scope in Decision Center.",
                missing=["resolved_import_scope"],
            )

        missing: list[str] = []
        for raw_key in raw_inv.keys():
            d = raw_decisions.get(raw_key)
            if isinstance(d, str):
                d = {"key": raw_key, "decision": "include_in_pipeline"}
            if not isinstance(d, dict):
                missing.append(raw_key)
                continue
            decision = d.get("decision", "")
            if decision not in VALID_RAW_IMPORT_DECISIONS:
                missing.append(raw_key)

        if missing:
            return GateResult(
                ok=False,
                message="Raw key import scope decisions required before commit.",
                missing=missing,
            )
        return GateResult(ok=True, message="Import scope gate passed.", missing=[])

    # Backwards compatibility: older family-based import scope gate
    import_scope = manifest.get("import_scope", {})
    missing: list[str] = []
    inventory = build_import_scope_inventory(payload)
    for key, count in inventory.items():
        if key == "_meta" or count <= 0:
            continue
        item = import_scope.get(key, {})
        if isinstance(item, str):
            item = {"key": key, "decision": "include_in_pipeline"}
        decision = item.get("decision", "")
        if decision not in VALID_IMPORT_ACTIONS:
            missing.append(key)
    if missing:
        return GateResult(
            ok=False,
            message="Import scope decisions required before commit.",
            missing=missing,
        )
    return GateResult(ok=True, message="Import scope gate passed.", missing=[])


def ensure_phase_gate(manifest: dict, phase: str, ledger: dict | None = None) -> GateResult:
    """ Validates if a phase can proceed or has successfully passed. """
    if manifest.get("status") == "locked":
        return GateResult(ok=False, message="Decision manifest is locked.", missing=["status"])

    # Helper: Check if previous phases failed in ledger
    def _phase_ok(p_key):
        if not ledger: return True # Fallback if ledger not provided
        st = ledger.get("phases", {}).get(p_key, {}).get("status", "")
        return st == "done" or st == "completed" or st == "success"

    # Phase 1: Import Scope Review
    if phase == "import":
        if not manifest.get("resolved_import_scope", False):
            return GateResult(False, "Import scope decisions unresolved.", ["resolved_import_scope"])
        if not _phase_ok("discover"):
            return GateResult(False, "Discovery phase failed or missing.", ["discover"])
        return GateResult(True, "Import scope gate passed.", [])

    # Phase 2: Analysis Triage
    if phase == "analyse":
        if not manifest.get("resolved_import_scope", False):
            return GateResult(False, "Import scope must be committed first.", ["resolved_import_scope"])
        
        # New: Triage GATE. Does not pass until operator clicks 'Resolve'
        if not manifest.get("analysis_triage", {}).get("resolved", False):
            return GateResult(False, "Analysis triage pending resolution.", ["analysis_triage.resolved"])
            
        return GateResult(True, "Analysis triage complete.", [])

    # Phase 3: Transform Approval (Infrastructure Orchestration Gate)
    if phase == "transform":
        if not manifest.get("analysis_triage", {}).get("resolved", False):
            return GateResult(False, "Analysis triage unresolved.", ["analysis_triage"])
        
        # New: V-A-N-R Infrastructure Gate
        if not manifest.get("vdom_mapping", {}).get("resolved", False):
            return GateResult(False, "Infrastructure V-A-N-R mappings pending resolution.", ["vdom_mapping.resolved"])
            
        if not _phase_ok("transform"):
             return GateResult(False, "Transformation phase failed.", ["transform"])
        return GateResult(True, "Transform gate passed.", [])

    # Phase 4: Deployment
    if phase == "deploy":
        if not manifest.get("transform_approvals", {}).get("resolved", False):
            return GateResult(False, "Transform approvals must be resolved first.", ["transform_approvals"])
        deploy = manifest.get("deploy_approval", {})
        missing = []
        if not deploy.get("resolved", False):
            missing.append("deploy_approval.resolved")
        if deploy.get("mode", "") != "execute":
            missing.append("deploy_approval.mode")
        if not deploy.get("dry_run_reviewed", False):
            missing.append("deploy_approval.dry_run_reviewed")
        if not str(deploy.get("cr_id", "")).strip():
            missing.append("deploy_approval.cr_id")
        if not str(deploy.get("sod_approver", "")).strip():
            missing.append("deploy_approval.sod_approver")
        if not deploy.get("operator_ack", False):
            missing.append("deploy_approval.operator_ack")
        if not deploy.get("governance_confirmed", False):
            missing.append("deploy_approval.governance_confirmed")
        if not deploy.get("maintenance_window", False):
            missing.append("deploy_approval.maintenance_window")
        if missing:
            return GateResult(False, "Deploy approvals unresolved.", missing)
        
        # New: Did deployment execution already happen and fail?
        if ledger and "deploy" in ledger.get("phases", {}):
            if not _phase_ok("deploy"):
                return GateResult(False, "Deployment execution failed. Check logs.", ["deploy"])

        return GateResult(True, "Deploy gate passed.", [])

    # Phase 5: Post-Validation
    if phase in {"verify", "post-validation", "ops-check"}:
        # Critical Check: Did deployment actually succeed?
        if not _phase_ok("deploy"):
             return GateResult(False, "Post-validation blocked: Deployment failed.", ["deploy"])

        post = manifest.get("post_validation_decision", {})
        if not post.get("resolved", False):
            return GateResult(False, "Post-validation decision unresolved.", ["post_validation_decision"])
        if post.get("action", "") not in VALID_POST_ACTIONS:
            return GateResult(False, "Invalid post-validation action.", ["post_validation_decision.action"])
        return GateResult(True, "Post-validation gate passed.", [])

    return GateResult(True, "No gate defined for phase.", [])
