"""
ui/routes.py
Flask blueprint — one route per UI tab.

Rules:
- Routes call service functions only. No logic here.
- Every route passes a flat context dict to render_template.
- On service error, pass error string in context — never raise 500.
- Quick Actions routes accept POST; all others are GET-only.
"""
from __future__ import annotations

import html
import json
import os
from pathlib import Path
from datetime import datetime, timezone
import re
import unicodedata

from flask import Blueprint, render_template, request, current_app, redirect, url_for, jsonify, flash, send_from_directory, abort
from flask_login import login_user, logout_user, login_required, current_user
from functools import wraps

import services.pipeline_service   as pipeline_svc
import services.report_service     as report_svc
import services.analytics_service  as analytics_svc
import services.kb_service         as kb_svc
import services.rag_service        as rag_svc
import services.user_service       as user_svc
from core.models import User
from core.state_ledger import StateLedger
from core.decision_manifest import (
    DecisionManifestStore,
    VALID_IMPORT_ACTIONS,
    VALID_POST_ACTIONS,
    VALID_TRIAGE_ACTIONS,
    ensure_phase_gate,
)

bp = Blueprint("main", __name__)


def roles_required(allowed_roles: list[str]):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('main.login'))
            user_roles = current_user.get_roles()
            # Admin always has access to everything
            if 'admin' in user_roles:
                return f(*args, **kwargs)
            # Check if any of user's roles are in allowed_roles
            if any(role.lower() in allowed_roles for role in user_roles):
                return f(*args, **kwargs)
            
            flash(f"Insufficient privileges. Required roles: {', '.join(allowed_roles).upper()}")
            return redirect(url_for('main.home'))
        return decorated_function
    return decorator


# Legacy helper for specific admin routes
def admin_required(f):
    return roles_required(['admin'])(f)


# ── helpers ──────────────────────────────────────────────────────────────────

def _dirs() -> dict:
    """Extract path config from Flask app config into a plain dict."""
    return {
        "tool_root":     current_app.config["TOOL_ROOT"],
        "state_dir":     current_app.config["STATE_DIR"],
        "reports_dir":   current_app.config["REPORTS_DIR"],
        "discovery_dir": current_app.config["DISCOVERY_DIR"],
        "logs_dir":      current_app.config["LOGS_DIR"],
        "fortiadc_dir":  current_app.config["FORTIADC_DIR"],
        "config_path":   current_app.config["TOOL_CONFIG_PATH"],
    }


def _safe_filename(filename: str) -> str:
    """Sanitize filename to prevent path traversal (Finding V05)."""
    if not filename:
        return ""
    # Remove any directory components
    base = os.path.basename(filename)
    # Remove common traversal patterns
    return base.replace("..", "").strip("/")


@bp.route("/favicon.ico")
def favicon():
    """Handle automatic browser favicon requests to avoid 404s."""
    return "", 204


def _safe(fn, *args, **kwargs):
    """Call fn; return (result, None) on success or (None, error_str) on failure."""
    try:
        return fn(*args, **kwargs), None
    except Exception as exc:
        return None, str(exc)


def _get_fortiadc_client(env: str, dry_run: bool = True):
    from core.fortiadc_client import FortiADCClient
    import yaml
    
    cfg_path = current_app.config["TOOL_CONFIG_PATH"]
    try:
        cfg = yaml.safe_load(Path(cfg_path).read_text())
    except Exception:
        return None
        
    envs = {e["name"]: e for e in (cfg.get("environments") or [])}
    env_cfg = envs.get(env, {})
    
    return FortiADCClient(
        host       = cfg["fortiadc"]["host"],
        username   = cfg["fortiadc"]["username"],
        password   = cfg["fortiadc"]["password"],
        vdom       = env_cfg.get("fortiadc_vdom", cfg["fortiadc"].get("vdom", "root")),
        verify_ssl = cfg["fortiadc"].get("verify_ssl", True),
        dry_run    = dry_run,
    )


@bp.route("/api/validation/pre-flight/<env>", methods=["POST"])
@roles_required(['admin', 'user'])
def api_pre_flight_validation(env):
    """Runs a full syntax-only dry-run deployment against the target FortiADC."""
    dirs = _dirs()
    fadc_dir = Path(dirs.get("fortiadc_dir", "fortiadc"))
    config_file = fadc_dir / f"{env}-config.json"
    
    if not config_file.exists():
        return jsonify({"success": False, "error": "No transformed configuration found. Run Transform first."})
        
    try:
        fortiadc_config = json.loads(config_file.read_text(encoding="utf-8"))
    except Exception as e:
        return jsonify({"success": False, "error": f"Failed to parse config: {e}"})

    # Use dry_run=False but the preflight_check uses ?dry-run=1 so no changes are made
    client = _get_fortiadc_client(env, dry_run=False)
    if not client:
        return jsonify({"success": False, "error": "Could not initialize FortiADC client. Check config.yaml."})
        
    from core.events import EventBus, Phase
    from deployers.fortiadc_deployer import FortiADCDeployer
    
    log_dir = Path(dirs.get("logs_dir", "logs")) / env
    log_dir.mkdir(parents=True, exist_ok=True)
    bus = EventBus(log_path=log_dir / "pre-flight.jsonl", verbose=False)
    
    try:
        deployer = FortiADCDeployer(client, bus)
        results = deployer.preflight_all(fortiadc_config)
        
        passed = all(r.success for r in results)
        summary = {
            "success": passed,
            "total": len(results),
            "passed": sum(1 for r in results if r.success),
            "failed": sum(1 for r in results if not r.success),
            "details": [{"name": r.name, "path": r.path, "success": r.success, "error": r.error} for r in results]
        }
        
        # Store validation results in manifest
        store = _manifest_store(env)
        manifest = store.load()
        manifest.setdefault("deploy_approval", {})["pre_flight_results"] = summary
        store.save(manifest)
        
        return jsonify(summary)
    finally:
        client.close()
        bus.close()


@bp.route("/api/advisory/datascript/<env>/<name>")
@login_required
def api_datascript_advisory(env, name):
    """Returns AI-assisted pattern analysis and migration advice for a DataScript."""
    dirs = _dirs()
    from services.llm_advisory_service import get_datascript_advice
    return jsonify(get_datascript_advice(dirs, env, name))


TRANSFORM_CATEGORY_META = {
    "ssl_certificates": {
        "label": "Certificates",
        "family": "Certificate import",
        "instruction": "Review certificate names, key presence, and any HSM or renewal constraints before deployment.",
    },
    "ssl_profiles": {
        "label": "SSL Profiles",
        "family": "Client SSL setup",
        "instruction": "Confirm TLS versions and cipher posture align with the target FortiADC standard.",
    },
    "health_checks": {
        "label": "Health Checks",
        "family": "Health monitoring",
        "instruction": "Validate intervals, timeouts, and expected response codes so member health matches Avi intent.",
    },
    "real_server_pools": {
        "label": "Pools",
        "family": "Pool definition",
        "instruction": "Check load-balancing method and inherited health monitors for each FortiADC pool.",
    },
    "real_servers": {
        "label": "Real Servers",
        "family": "Backend nodes",
        "instruction": "These are the backend servers FortiADC will create globally before attaching them to pools.",
    },
    "pool_members": {
        "label": "Pool Member Bindings",
        "family": "Pool attachment",
        "instruction": "Verify each pool member binding points to the right real server and port so pools are not created empty.",
    },
    "persistence_profiles": {
        "label": "Persistence Profiles",
        "family": "Session stickiness",
        "instruction": "Review persistence type and timeout caps because FortiADC may narrow long Avi persistence windows.",
    },
    "virtual_servers": {
        "label": "Virtual Servers",
        "family": "Frontend listeners",
        "instruction": "Confirm VIP, port, pool, and SSL references. Missing items here usually block a usable cutover config.",
    },
}

TRANSFORM_REVIEW_STATES = {"review_pending", "approved", "needs_changes", "not_applicable"}


def _load_transform_events(dirs: dict, env: str) -> list[dict]:
    log_path = Path(dirs.get("logs_dir", "logs")) / env / "transform.jsonl"
    if not log_path.exists():
        return []
    events = []
    seen = set()
    for raw_line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            event = json.loads(raw_line)
        except Exception:
            continue
        key = (
            event.get("level", ""),
            event.get("phase", ""),
            event.get("object_type", ""),
            event.get("object_name", ""),
            event.get("message", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        events.append(event)
    return events


def _build_transform_review(env: str, dirs: dict, manifest: dict, config_data: dict) -> dict:
    discovery_file = Path(dirs.get("discovery_dir", "discovery")) / f"{env}.json"
    discovery_data = pipeline_svc.read_json(discovery_file) if discovery_file.exists() else {}

    vsvip_index = {
        item.get("name", ""): item
        for item in discovery_data.get("vs_vips", [])
        if isinstance(item, dict) and item.get("name")
    }
    auto_vs_candidates = 0
    datascript_vs = 0
    for vs in discovery_data.get("virtual_services", []):
        if not isinstance(vs, dict):
            continue
        if vs.get("_has_datascripts"):
            datascript_vs += 1
            continue
        vips = list(vs.get("_vips", []) or [])
        if not vips:
            vsvip_name = (vs.get("_resolved") or {}).get("vsvip_ref") or ""
            vsvip_obj = vsvip_index.get(vsvip_name, {})
            vips = [
                vip.get("ip_address", {}).get("addr", "")
                for vip in vsvip_obj.get("vip", [])
                if isinstance(vip, dict) and vip.get("ip_address", {}).get("addr")
            ]
        if vips:
            auto_vs_candidates += 1

    tenants = set()
    for vs in discovery_data.get("virtual_services", []):
        if not isinstance(vs, dict): continue
        t_ref = vs.get("tenant_ref", "")
        if "name=" in t_ref:
            tenants.add(t_ref.split("name=")[-1])
        elif "/" in t_ref:
            tenants.add(t_ref.split("/")[-1])
            
    if not tenants and discovery_data.get("virtual_services"):
        tenants.add("admin")

    expected_counts = {
        "ssl_certificates": len(discovery_data.get("ssl_certificates", [])),
        "ssl_profiles": len(discovery_data.get("ssl_profiles", [])),
        "health_checks": len(discovery_data.get("health_monitors", [])),
        "real_server_pools": len(discovery_data.get("pools", [])),
        "real_servers": sum(len(pool.get("servers", [])) for pool in discovery_data.get("pools", []) if isinstance(pool, dict)),
        "pool_members": sum(len(pool.get("servers", [])) for pool in discovery_data.get("pools", []) if isinstance(pool, dict)),
        "persistence_profiles": len(discovery_data.get("persistence_profiles", [])),
        "virtual_servers": auto_vs_candidates,
    }

    overrides = manifest.get("manual_overrides", {})
    saved_groups = manifest.get("transform_approvals", {}).get("groups", {})
    categories = []
    sections_ready = 0
    total_objects = 0
    reviewed_count = 0

    for idx, (section, meta) in enumerate(TRANSFORM_CATEGORY_META.items(), start=1):
        objects = config_data.get(section, []) if isinstance(config_data, dict) else []
        if not isinstance(objects, list):
            objects = []
        expected = expected_counts.get(section, 0)
        count = len(objects)
        total_objects += count
        if count and (expected == 0 or count <= expected or section == "pool_members"):
            sections_ready += 1
        if count == 0 and expected > 0:
            status = "missing"
            summary = f"{expected} expected from discovery, none generated."
        elif expected > 0 and count < expected:
            status = "partial"
            summary = f"{count} generated out of {expected} expected from discovery."
        elif count > 0:
            status = "ready"
            summary = f"{count} generated and ready for operator review."
        else:
            status = "empty"
            summary = "No objects required for this category in the current scope."

        category_objects = []
        for obj in objects:
            path = obj.get("fortiadc_path", section)
            mkey = obj.get("name", "") or obj.get("payload", {}).get("mkey", "")
            override_key = f"{path}::{mkey}"
            category_objects.append({
                "section": section,
                "name": mkey,
                "fortiadc_path": path,
                "override_key": override_key,
                "payload_json": json.dumps(
                    overrides[override_key]["payload"] if override_key in overrides else obj.get("payload", {}),
                    indent=2,
                ),
                "is_overridden": override_key in overrides,
                "override_note": overrides.get(override_key, {}).get("note", ""),
                "override_by": overrides.get(override_key, {}).get("saved_by", ""),
                "override_at": overrides.get(override_key, {}).get("saved_at", ""),
                "hint": "",
                "vdom": obj.get("vdom", "root"),
            })

        if section == "pool_members":
            for entry in category_objects:
                entry["hint"] = "Confirms the real server is actually attached to its pool."
        elif section == "virtual_servers":
            for entry in category_objects:
                entry["hint"] = "Frontend listener definition built from Avi VS VIP and service mapping."

        saved_review = saved_groups.get(section, {})
        review_state = saved_review.get("decision", "review_pending")
        if review_state not in TRANSFORM_REVIEW_STATES:
            review_state = "review_pending"
        if review_state != "review_pending":
            reviewed_count += 1

        categories.append({
            "step": idx,
            "key": section,
            "label": meta["label"],
            "family": meta["family"],
            "instruction": meta["instruction"],
            "count": count,
            "expected": expected,
            "status": status,
            "summary": summary,
            "objects": category_objects,
            "review_state": review_state,
            "review_rationale": saved_review.get("rationale", ""),
            "review_notes": saved_review.get("notes", ""),
            "review_saved_at": saved_review.get("saved_at", ""),
            "review_saved_by": saved_review.get("saved_by", ""),
        })

    transform_events = _load_transform_events(dirs, env)
    issues = []
    for event in transform_events:
        level = event.get("level", "")
        if level not in {"ERROR", "MANUAL", "CRITICAL", "WARN"}:
            continue
        issues.append({
            "level": level,
            "object_type": event.get("object_type", ""),
            "object_name": event.get("object_name", ""),
            "message": event.get("message", ""),
        })

    high_level_notes = []
    if expected_counts["pool_members"] and not config_data.get("pool_members"):
        high_level_notes.append("Pool member bindings are missing, so FortiADC pools may be created without attached backends.")
    if expected_counts["virtual_servers"] and not config_data.get("virtual_servers"):
        high_level_notes.append("Virtual servers are missing even though discovery contains transformable VS candidates. Review VIP resolution and blockers.")
    if datascript_vs:
        high_level_notes.append(f"{datascript_vs} virtual service(s) still require manual DataScript handling before they can be transformed.")

    return {
        "summary": {
            "total_objects": total_objects,
            "sections_ready": sections_ready,
            "section_total": len(TRANSFORM_CATEGORY_META),
            "reviewed_categories": reviewed_count,
            "override_count": len(overrides),
            "issue_count": len(issues),
            "auto_vs_candidates": auto_vs_candidates,
            "generated_virtual_servers": len(config_data.get("virtual_servers", [])) if isinstance(config_data, dict) else 0,
            "tenants": sorted(list(tenants)),
        },
        "tenants": sorted(list(tenants)),
        "categories": categories,
        "issues": issues[:24],
        "notes": high_level_notes,
        "structure_note": "The generated file is the tool's FortiADC deployment manifest grouped by API families and payloads. Review each category before treating the configuration as final.",
        "auto_vs_candidates": auto_vs_candidates,
    }


def _write_transform_review_reports(dirs: dict, env: str, transform_review: dict, config_data: dict) -> None:
    reports_dir = Path(dirs.get("reports_dir", "reports"))
    reports_dir.mkdir(exist_ok=True)
    html_path = reports_dir / f"{env}-transform-review.html"
    md_path = reports_dir / f"{env}-transform-review.md"

    categories = transform_review.get("categories", [])
    issues = transform_review.get("issues", [])
    summary = transform_review.get("summary", {})

    cat_rows = []
    for category in categories:
        cat_rows.append(
            "<tr>"
            f"<td style='padding:10px 12px'>{category['step']}. {html.escape(category['label'])}</td>"
            f"<td style='padding:10px 12px'>{html.escape(category['review_state'])}</td>"
            f"<td style='padding:10px 12px'>{category['count']}</td>"
            f"<td style='padding:10px 12px'>{category['expected']}</td>"
            f"<td style='padding:10px 12px;color:#8b8fa8'>{html.escape(category['summary'])}</td>"
            "</tr>"
        )
    issue_rows = []
    for issue in issues:
        issue_rows.append(
            "<tr>"
            f"<td style='padding:10px 12px'>{html.escape(issue.get('level', ''))}</td>"
            f"<td style='padding:10px 12px'>{html.escape(issue.get('object_type', '') or 'object')}</td>"
            f"<td style='padding:10px 12px'>{html.escape(issue.get('object_name', ''))}</td>"
            f"<td style='padding:10px 12px;color:#8b8fa8'>{html.escape(issue.get('message', ''))}</td>"
            "</tr>"
        )
    html_text = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Transform Review — {html.escape(env)}</title>
<style>
*{{box-sizing:border-box}} body{{background:#0f1117;color:#e2e4ed;font-family:'Segoe UI',system-ui,sans-serif;padding:32px;line-height:1.6}}
.panel{{background:#1a1d27;border:1px solid #2e3147;border-radius:10px;padding:16px;margin-bottom:18px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}
.card{{background:#11141d;border:1px solid #2e3147;border-radius:8px;padding:14px}}
table{{width:100%;border-collapse:collapse;background:#11141d;border-radius:8px;overflow:hidden}}
th{{background:#252837;padding:10px 12px;text-align:left;color:#8b8fa8;font-size:12px}}
</style></head><body>
<h1>Transform Review Report</h1>
<div style="color:#8b8fa8;margin-bottom:18px;">Environment: <strong>{html.escape(env)}</strong></div>
<div class="panel"><strong>Structure note:</strong> {html.escape(transform_review.get('structure_note', ''))}</div>
<div class="grid panel">
<div class="card"><div>Generated objects</div><strong>{summary.get('total_objects', 0)}</strong></div>
<div class="card"><div>Reviewed categories</div><strong>{summary.get('reviewed_categories', 0)}/{summary.get('section_total', 0)}</strong></div>
<div class="card"><div>Generated VS</div><strong>{summary.get('generated_virtual_servers', 0)}/{summary.get('auto_vs_candidates', 0)}</strong></div>
<div class="card"><div>Transform issues</div><strong>{summary.get('issue_count', 0)}</strong></div>
</div>
<div class="panel"><h2>Guided Category Review</h2><table><thead><tr><th>Category</th><th>Review</th><th>Generated</th><th>Expected</th><th>Summary</th></tr></thead><tbody>{''.join(cat_rows)}</tbody></table></div>
<div class="panel"><h2>Transform Findings</h2><table><thead><tr><th>Level</th><th>Type</th><th>Name</th><th>Message</th></tr></thead><tbody>{''.join(issue_rows) if issue_rows else '<tr><td colspan="4" style="padding:10px 12px;color:#8b8fa8;">No transform findings recorded.</td></tr>'}</tbody></table></div>
<div class="panel"><h2>Raw Config Snapshot</h2><pre style="white-space:pre-wrap;max-height:700px;overflow:auto;background:#0f1117;border:1px solid #2e3147;border-radius:8px;padding:14px;">{html.escape(json.dumps(config_data, indent=2))}</pre></div>
</body></html>"""
    md_lines = [
        f"# Transform Review — {env}",
        "",
        transform_review.get("structure_note", ""),
        "",
        f"- Generated objects: {summary.get('total_objects', 0)}",
        f"- Reviewed categories: {summary.get('reviewed_categories', 0)}/{summary.get('section_total', 0)}",
        f"- Generated VS: {summary.get('generated_virtual_servers', 0)}/{summary.get('auto_vs_candidates', 0)}",
        f"- Transform issues: {summary.get('issue_count', 0)}",
        "",
        "## Guided Category Review",
    ]
    for category in categories:
        md_lines.extend([
            f"- {category['step']}. {category['label']}: {category['review_state']} | generated {category['count']} | expected {category['expected']}",
            f"  - {category['summary']}",
        ])
    md_lines.extend(["", "## Transform Findings"])
    if issues:
        for issue in issues:
            md_lines.append(f"- {issue.get('level','')}: {issue.get('object_type','object')} {issue.get('object_name','')} — {issue.get('message','')}")
    else:
        md_lines.append("- No transform findings recorded.")
    html_path.write_text(html_text, encoding="utf-8")
    md_path.write_text("\n".join(md_lines), encoding="utf-8")


def _status_badge_for_phase(status: str) -> str:
    status = (status or "pending").lower()
    if status in {"done", "completed", "success"}:
        return "done"
    if status in {"running", "in_progress"}:
        return "in-progress"
    if status in {"failed", "error"}:
        return "failed"
    return "pending"


def _build_subtask_status_map(dirs: dict, manifest: dict) -> dict[str, dict]:
    subtask_map: dict[str, dict] = {}
    for subtask in manifest.get("tenant_filter", {}).get("sub_tasks", []) or []:
        child_env = subtask.get("env", "")
        child_manifest = _manifest_store(child_env).load() if child_env else {}
        child_ledger = pipeline_svc._read_ledger(dirs.get("state_dir", "state"), child_env) if child_env else {}
        child_phases = child_ledger.get("phases", {})
        current_phase = None
        current_status = "pending"
        for phase in pipeline_svc.PHASES:
            raw_status = str(child_phases.get(phase, {}).get("status", "pending")).strip().lower()
            mapped = pipeline_svc.STATUS_MAP.get(raw_status, raw_status or "pending")
            if mapped in {"in_progress", "failed"}:
                current_phase = phase
                current_status = mapped
                break
            if mapped == "pending" and current_phase is None:
                current_phase = phase
                current_status = mapped
        if current_phase is None:
            current_phase = "verify"
            current_status = pipeline_svc.STATUS_MAP.get(
                str(child_phases.get("verify", {}).get("status", "pending")).strip().lower(),
                "pending",
            )
        subtask_map[child_env] = {
            "id": subtask.get("id") or child_env,
            "name": subtask.get("name") or child_env,
            "env": child_env,
            "included_tenants": subtask.get("included_tenants", []),
            "included_vses": subtask.get("included_vses", []),
            "current_phase": current_phase,
            "current_phase_label": pipeline_svc.PHASE_LABELS.get(current_phase, current_phase),
            "current_status": current_status,
            "status_badge": _status_badge_for_phase(current_status),
            "resolved_import_scope": bool(child_manifest.get("resolved_import_scope")),
            "analysis_resolved": bool(child_manifest.get("analysis_triage", {}).get("resolved")),
            "transform_resolved": bool(child_manifest.get("transform_approvals", {}).get("resolved")),
        }
    return subtask_map


def _build_deploy_checklist(deploy: dict) -> list[dict]:
    return [
        {"key": "dry_run_reviewed", "label": "Dry-run reviewed", "done": bool(deploy.get("dry_run_reviewed"))},
        {"key": "cr_id", "label": "CR ID recorded", "done": bool(str(deploy.get("cr_id", "")).strip())},
        {
            "key": "sod",
            "label": "Second approver sign-off",
            "done": bool(deploy.get("governance_confirmed")) and bool(str(deploy.get("sod_approver", "")).strip()),
        },
        {"key": "maintenance_window", "label": "Maintenance window confirmed", "done": bool(deploy.get("maintenance_window"))},
        {"key": "operator_ack", "label": "Rollback readiness acknowledged", "done": bool(deploy.get("operator_ack"))},
    ]


def _build_blocker_callout(phase: str, manifest: dict, **context) -> dict | None:
    if phase == "import_scope":
        if manifest.get("resolved_import_scope"):
            return {
                "level": "success",
                "title": "Import scope committed",
                "body": "This scope is committed. Re-commit only if you intend to refresh downstream analysis and replace the current selection.",
            }
        return {
            "level": "warning",
            "title": "Commit the scope to advance",
            "body": "Import decisions can be saved as draft, but analysis will not become authoritative until you commit the scope for this environment.",
        }
    if phase == "analysis_triage":
        triage_items = context.get("triage_items", []) or []
        if manifest.get("analysis_triage", {}).get("resolved"):
            return {
                "level": "success",
                "title": "Analysis triage resolved",
                "body": "Analysis findings were acknowledged for this environment. You can proceed to transform approval.",
            }
        return {
            "level": "warning",
            "title": "Analysis is still blocking transform",
            "body": f"{len(triage_items)} grouped issue set(s) still require operator triage before transform approval can be finalized.",
        }
    if phase == "transform_approval":
        transform_review = context.get("transform_review", {}) or {}
        if manifest.get("transform_approvals", {}).get("resolved"):
            return {
                "level": "success",
                "title": "Transform approval resolved",
                "body": "The generated FortiADC configuration has been approved and the deployment gate is now the next control point.",
            }
        issue_count = (transform_review.get("summary") or {}).get("issue_count", 0)
        return {
            "level": "warning",
            "title": "Transform approval is still blocking deployment",
            "body": f"Review the generated FortiADC categories, confirm any manual overrides, and clear {issue_count} recorded transform issue(s) before approving deployment.",
        }
    if phase == "deploy_approval":
        deploy = manifest.get("deploy_approval", {})
        checklist = _build_deploy_checklist(deploy)
        complete = sum(1 for item in checklist if item["done"])
        if deploy.get("resolved"):
            return {
                "level": "success",
                "title": "Deployment approval recorded",
                "body": "Execute readiness has been acknowledged and the final validation decision is the next gate.",
            }
        if complete == len(checklist) and deploy.get("mode") != "execute":
            return {
                "level": "warning",
                "title": "Strategy Choice Required",
                "body": "All 5/5 pre-flight checks are satisfied, but you are currently in 'Dry-Run' mode. Switch the execution strategy to 'Production Execution' above to sign and authorize the deployment.",
            }
        return {
            "level": "warning",
            "title": "Deployment is still blocked",
            "body": f"Only {complete}/{len(checklist)} pre-flight checks are complete. Execute approval stays blocked until dry-run, governance, maintenance window, and rollback readiness are all confirmed.",
        }
    if phase == "post_validation_decision":
        post = manifest.get("post_validation_decision", {})
        if post.get("resolved"):
            return {
                "level": "success",
                "title": "Post-validation decision recorded",
                "body": f"The environment is marked with final action '{post.get('action', '') or 'unknown'}'.",
            }
        return {
            "level": "warning",
            "title": "Final validation decision pending",
            "body": "Choose proceed, hold, or rollback after reviewing the cutover outcome. The selected action should match the current operational state of Avi, DNS, and FortiADC.",
        }
    return None


def _resolve_raw_object(raw_snapshot: dict, object_type: str, object_name: str, graph) -> tuple[str, dict | None]:
    normalized_type = (object_type or "").strip().lower()
    
    # 0. Precise Type Normalization (Aliasing)
    _TYPE_ALIASES = {
        "datascript":      "VSDataScriptSet",
        "VSDataScriptSet": "VSDataScriptSet",
        "ssl_certificate": "SSLKeyAndCertificate",
        "pools":           "Pool",
        "pool":            "Pool",
        "virtual_services": "VirtualService",
        "virtualservice":  "VirtualService",
        "health_monitors": "HealthMonitor",
        "healthmonitor":   "HealthMonitor",
        "ssl_certificates": "SSLKeyAndCertificate",
        "ssl_profiles":     "SSLProfile",
        "sslprofile":      "SSLProfile",
        "application_profiles": "ApplicationProfile",
        "app_profile":     "ApplicationProfile",
        "persistence_profiles": "ApplicationPersistenceProfile",
        "persistence":     "ApplicationPersistenceProfile",
        "vsvips":           "VsVip",
        "vsvip":            "VsVip",
        "datascripts":      "VSDataScriptSet",
        "waf_policies":     "WafPolicy",
        "waf_policy":       "WafPolicy",
        "gslb_services":    "GslbService",
        "gslb_service":     "GslbService",
        "network":          "Network",
        "vrfcontext":       "VrfContext",
        "serviceenginegroup": "ServiceEngineGroup",
        "httppolicyset":    "HTTPPolicySet",
    }
    
    # 1. Normalize object_type
    target_type = _TYPE_ALIASES.get(object_type.lower(), object_type)
    
    raw_key = ""
    # 2. Case-insensitive key match in snapshot
    for k in raw_snapshot.keys():
        if k.lower() == target_type.lower():
            raw_key = k
            break
    
    # 3. Fuzzy match fallback
    if not raw_key:
        norm_target = target_type.lower().replace("_", "").replace("-", "")
        for k in raw_snapshot.keys():
            cand = k.lower().replace("_", "").replace("-", "")
            if cand == norm_target or cand.startswith(norm_target) or norm_target.startswith(cand):
                raw_key = k
                break
    
    if not raw_key:
        return "", None

    # 2. Resolve raw_obj with Fuzzy Name Matching
    # Strategy A: Exact Match
    obj = graph.get_by_name(raw_key, object_name)
    if obj:
        return raw_key, obj

    # Strategy B: Strip intelligence decorators (' || ', ':', ' (')
    clean_name = object_name
    # Handle ' || ' (new standardized delimiter)
    if " || " in clean_name:
        # P09 style: "X sets share identical body || name1, name2"
        # We want to resolve to ONE of them (they all belong to same tenant/type usually or are shared)
        parts = clean_name.split(" || ")
        if len(parts) > 1 and "," in parts[1]:
            clean_name = parts[1].split(",")[0].strip()
        else:
            clean_name = parts[0]
        
    # Handle ' (Monitor: ...' or ' (14d remaining)'
    if " (" in clean_name:
        clean_name = clean_name.split(" (")[0]
        
    # Handle ': 0/3 members up' or 'VS:Name'
    if ":" in clean_name:
        # If it's a VS: prefix, we strip the prefix
        if clean_name.lower().startswith("vs:"):
            clean_name = clean_name.split(":", 1)[1]
        else:
            # If it's a suffix decorator like pool: status, we take the prefix
            clean_name = clean_name.split(":", 1)[0]
    
    obj = graph.get_by_name(raw_key, clean_name.strip())
    return raw_key, obj


def _build_analysis_triage_review(env: str, dirs: dict, manifest: dict, unsupported: list) -> list[dict]:
    from core.resolver import ConfigurationGraph

    state_dir = Path(dirs.get("state_dir", "state"))
    snap_path = pipeline_svc.resolve_env_file(state_dir, env, "-raw-snapshot.json")
    raw_snapshot = pipeline_svc.flatten_avi_snapshot(pipeline_svc.read_json(snap_path)) if snap_path else {}
    graph = ConfigurationGraph(raw_snapshot)
    subtask_states = _build_subtask_status_map(dirs, manifest)

    discovery_file = pipeline_svc.resolve_env_file(dirs.get("discovery_dir", "discovery"), env, ".json")
    discovery = pipeline_svc.read_json(discovery_file) if discovery_file else {}
    datascript_index = {
        item.get("name", ""): item
        for item in discovery.get("datascripts", [])
        if isinstance(item, dict) and item.get("name")
    }

    triage_rows = []
    for u in unsupported:
        tenant = "UNKNOWN"
        
        # 1. Handle cross-connects and integrations (often Global)
        if ":" in u.object_type:
            parts = u.object_type.split(":")
            if parts[0].lower() in ("connection", "system", "infrastructure"):
                tenant = "Global / Shared"
        
        raw_key, raw_obj = _resolve_raw_object(raw_snapshot, u.object_type, u.object_name, graph)
        if raw_obj:
            tenant = graph.get_tenant(raw_obj)
        elif tenant == "UNKNOWN" and u.object_type.upper().startswith("GSLB"):
            # GSLB objects are always global in Avi
            tenant = "Global / Shared"
        
        tenant_name = tenant.upper()
        if tenant_name == "ADMIN" or tenant_name == "UNKNOWN" and ":" in u.object_type:
            tenant_name = "Global / Shared"

        vip_text = ""
        if raw_key == "VirtualService" and raw_obj:
            vip_text = ", ".join(graph.get_all_vips_for_vs(raw_obj)) or graph.get_vip_for_vs(raw_obj)

        dependency_lines = []
        if raw_key and raw_obj:
            for dep_type, dep_names in sorted(graph.get_dependencies(raw_key, raw_obj).items()):
                if dep_names:
                    dependency_lines.append(f"{dep_type}: {', '.join(dep_names[:6])}" + (f" (+{len(dep_names) - 6} more)" if len(dep_names) > 6 else ""))

        delegated_to = None
        delegated_state = None
        for child_env, status in subtask_states.items():
            if u.object_name in status.get("included_vses", []):
                delegated_to = status["name"]
                delegated_state = status
                break

        raw_json_payload = raw_obj if raw_obj else {"avi_value": u.avi_value, "forti_note": u.forti_note}
        raw_json = json.dumps(raw_json_payload, indent=2, ensure_ascii=False)
        if len(raw_json) > 6000:
            raw_json = raw_json[:6000].rstrip() + "\n..."

        selection_summary = []
        selection_summary.append(f"Tenant: {tenant_name}")
        if vip_text:
            selection_summary.append(f"VIPs: {vip_text}")
        if raw_key:
            selection_summary.append(f"Raw key: {raw_key}")
        if delegated_state:
            selection_summary.append(
                f"Delegated to sub-task {delegated_state['name']} ({delegated_state['current_phase_label']} / {delegated_state['current_status']})"
            )

        script_preview = ""
        if u.object_type.lower() == "datascript":
            ds_obj = datascript_index.get(u.object_name, {})
            ds_raw = ds_obj.get("datascript", [])
            if isinstance(ds_raw, list):
                script_preview = "\n".join(str(s.get("script", "")) for s in ds_raw if isinstance(s, dict))[:1200]

        triage_rows.append({
            "object_type": u.object_type,
            "object_name": u.object_name,
            "reason": u.reason,
            "action": u.action,
            "severity": u.severity,
            "selection_summary": selection_summary,
            "dependency_lines": dependency_lines,
            "raw_json": raw_json,
            "script_preview": script_preview,
            "delegated_to": delegated_to,
            "delegated_state": delegated_state,
            "tenant_name": tenant_name,
            "resolved_name": raw_obj.get("name") if raw_obj else u.object_name,
            "exists": raw_obj is not None,
            "key": f"{u.object_type}:{u.object_name}"
        })

    return triage_rows


# ── Template Filters ────────────────────────────────────────────────────────

def slugify(value):
    """Simple slugify for template IDs and selectors."""
    if not value:
        return ""
    value = str(value)
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[^\w\s-]', '', value).strip().lower()
    return re.sub(r'[-\s]+', '-', value)

@bp.app_template_filter('slugify')
def slugify_filter(s):
    return slugify(s)


# ── Authentication ───────────────────────────────────────────────────────────

@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.home'))

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user)
            pipeline_svc.log_system_event(_dirs(), f"User '{username}' logged in successfully.", level="INFO")
            return redirect(url_for('main.home'))
        
        pipeline_svc.log_system_event(_dirs(), f"Failed login attempt for username '{username}'.", level="WARN")
        flash("Invalid username or password.")
    
    return render_template("login.html")


@bp.route("/logout")
@login_required
def logout():
    username = current_user.username
    logout_user()
    pipeline_svc.log_system_event(_dirs(), f"User '{username}' logged out.", level="INFO")
    return redirect(url_for('main.login'))


# ── Administration ───────────────────────────────────────────────────────────

@bp.route("/admin/users")
@admin_required
def admin_users():
    users = user_svc.get_all_users()
    return render_template("admin_users.html", users=users, page_title="User Management")


@bp.route("/admin/users/create", methods=["POST"])
@admin_required
def create_user_route():
    username = request.form.get("username")
    password = request.form.get("password")
    roles    = request.form.getlist("roles")
    if not roles: roles = ['user']
    
    ok, msg = user_svc.create_user(username, password, roles)
    if ok:
        pipeline_svc.log_system_event(_dirs(), f"Admin '{current_user.username}' created new user '{username}' with roles: {', '.join(roles)}.")
    flash(msg)
    return redirect(url_for('main.admin_users'))


@bp.route("/admin/users/update/<int:user_id>", methods=["POST"])
@admin_required
def update_user_route(user_id):
    roles    = request.form.getlist("roles")
    password = request.form.get("password")
    
    ok, msg = user_svc.update_user(user_id, roles=roles, password=password)
    if ok:
        pipeline_svc.log_system_event(_dirs(), f"Admin '{current_user.username}' updated user {user_id} (New roles: {', '.join(roles)}).")
    flash(msg)
    return redirect(url_for('main.admin_users'))


@bp.route("/admin/users/delete/<int:user_id>", methods=["POST"])
@admin_required
def delete_user_route(user_id):
    ok, msg = user_svc.delete_user(user_id)
    flash(msg)
    return redirect(url_for('main.admin_users'))


# ── Tab 1: Home / Pipeline ───────────────────────────────────────────────────

@bp.route("/")
@bp.route("/pipeline")
@login_required
def home():
    dirs = _dirs()
    duration = request.args.get("duration", "1h")
    page     = int(request.args.get("page", 1))
    level    = request.args.get("level", "")
    source   = request.args.get("source", "")
    
    since_hours = 0
    if duration == "1h": since_hours = 1
    elif duration == "6h": since_hours = 6
    elif duration == "24h": since_hours = 24
    elif duration == "7d": since_hours = 168

    pipeline,  err1 = _safe(pipeline_svc.get_pipeline_status,  dirs)
    readiness, err2 = _safe(pipeline_svc.get_environment_readiness, dirs)
    recent,    err3 = _safe(pipeline_svc.get_recent_activity,   dirs, 
                                 since_hours=since_hours, 
                                 page=page, 
                                 level=level, 
                                 source_filter=source)
    
    return render_template(
        "home.html",
        page_title   = "Pipeline",
        pipeline     = pipeline,
        readiness    = readiness,
        recent       = recent.get("events", []),
        pagination   = {
            "total":        recent.get("total", 0),
            "pages":        recent.get("pages", 0),
            "current_page": recent.get("current_page", 1),
            "duration":     duration,
            "level":        level,
            "source":       source
        },
        current_duration = duration,
        current_level    = level,
        current_source   = source,
        errors       = [e for e in [err1, err2, err3] if e],
    )


@bp.route("/import", methods=["GET", "POST"])
@login_required
def import_config():
    dirs = _dirs()
    result = None
    error = None

    if request.method == "POST":
        env_name = request.form.get("env_name", "").strip().lower()
        payload_type = request.form.get("payload_type", "normalized_discovery").strip()
        file = request.files.get("config_file")

        if not env_name or not file:
            error = "Environment name and file are required."
        else:
            try:
                # Read file content
                content = file.read().decode("utf-8")
                # Import via service
                res = pipeline_svc.import_avi_config(
                    dirs,
                    content,
                    env_name,
                    payload_type=payload_type or "normalized_discovery",
                )
                if res.get("decision_required"):
                    return redirect(url_for("main.decisions_import_scope", env=env_name))
                if res.get("success"):
                    result = res
                else:
                    error = res.get("error", "Unknown import error")
            except Exception as e:
                error = str(e)

    return render_template(
        "import.html",
        page_title="Import Config",
        result=result,
        errors=[error] if error else [],
    )




def _manifest_store(env: str) -> DecisionManifestStore:
    return DecisionManifestStore(env, state_dir=current_app.config["STATE_DIR"])


@bp.route("/decisions/<env>")
@login_required
def decisions_overview(env):
    store = _manifest_store(env)
    manifest = store.load()
    
    dirs = _dirs()
    ledger = StateLedger(env, state_dir=dirs.get("state_dir", "state"))
    
    gate_import = ensure_phase_gate(manifest, "import", ledger._data)
    gate_analyse = ensure_phase_gate(manifest, "analyse", ledger._data)
    gate_transform = ensure_phase_gate(manifest, "transform", ledger._data)
    gate_deploy = ensure_phase_gate(manifest, "deploy", ledger._data)
    gate_verify = ensure_phase_gate(manifest, "verify", ledger._data)

    gate_sequence = [
        {"key": "import", "label": "Import Scope", "gate": gate_import},
        {"key": "analyse", "label": "Analysis Triage", "gate": gate_analyse},
        {"key": "transform", "label": "Transform Approval", "gate": gate_transform},
        {"key": "deploy", "label": "Deploy Approval", "gate": gate_deploy},
        {"key": "verify", "label": "Post Validation", "gate": gate_verify},
    ]
    current_progress = "Import Scope"
    current_message = gate_import.message
    for item in gate_sequence:
        if not item["gate"].ok:
            current_progress = item["label"]
            current_message = item["gate"].message
            break
    else:
        current_progress = "Complete"
        current_message = "All decision gates are resolved for this environment."

    # Load reports for the sidebar
    all_reports = report_svc.get_all_reports(_dirs())
    env_reports = [r for r in all_reports if r["env"] == env]

    return render_template(
        "decisions_overview.html",
        page_title=f"Decisions: {env}",
        env=env,
        manifest=manifest,
        reports=env_reports,
        gate_import=gate_import,
        gate_analyse=gate_analyse,
        gate_transform=gate_transform,
        gate_deploy=gate_deploy,
        gate_verify=gate_verify,
        gate_sequence=gate_sequence,
        current_progress=current_progress,
        current_message=current_message,
    )



@bp.route("/decisions/<env>/run-phase/<phase>", methods=["POST"])
@login_required
def decisions_run_phase(env, phase):
    """Manual trigger for an automated pipeline phase."""
    dirs = _dirs()
    result = pipeline_svc.run_pipeline_phase(dirs, env, phase)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.accept_mimetypes.accept_json:
        return jsonify(result)
        
    if result.get("success"):
        flash(result.get("message", f"Phase {phase} completed."), "success")
    else:
        flash(result.get("error", f"Phase {phase} failed."), "error")
    
    return redirect(request.referrer or url_for("main.decisions_overview", env=env))


@bp.route("/decisions/<env>/export-summary")
def decisions_export_summary(env):
    """Export the migration impact summary as a CSV file."""
    store = _manifest_store(env)
    manifest = store.load()
    dirs = _dirs()
    
    csv_data = pipeline_svc.export_impact_summary_csv(dirs, env, manifest)
    
    from flask import Response
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-disposition": f"attachment; filename={env}_impact_summary.csv"}
    )


@bp.route("/decisions/<env>/api/scope-impact", methods=["POST"])
def decisions_scope_impact_api(env):
    """AJAX: Return impact_summary filtered by the posted selection."""
    from flask import jsonify
    store = _manifest_store(env)
    manifest = store.load()
    dirs = _dirs()

    data = request.get_json(force=True, silent=True) or {}
    inc_tenants = data.get("included_tenants", [])
    inc_vses    = data.get("included_vses", [])
    # Auto-enable tracer if anything is selected, regardless of the master toggle
    is_enabled  = bool(inc_tenants or inc_vses or data.get("enabled", False))

    # Build a temporary manifest snapshot with the posted selection
    manifest["tenant_filter"] = {
        "enabled": is_enabled,
        "available_tenants": manifest.get("tenant_filter", {}).get("available_tenants", []),
        "included_tenants": inc_tenants,
        "included_vses": inc_vses,
    }

    review = pipeline_svc.get_import_scope_review(dirs, env, manifest)
    _CATS = ["gslb", "slb", "pools", "hmonitors", "certs", "datascripts", "waf", "persistence", "others"]
    return jsonify({
        "impact_summary": {
            t_name: {
                cat: {
                    "inc":       impact[cat].get("inc", 0),
                    "ctx":       impact[cat].get("ctx", 0),
                    "exc":       impact[cat].get("exc", 0),
                    "display":   impact[cat].get("display", "0"),
                    "inc_names": impact[cat].get("inc_names", []),
                    "ctx_names": impact[cat].get("ctx_names", []),
                    "exc_names": impact[cat].get("exc_names", []),
                }
                for cat in _CATS
                if cat in impact
            }
            for t_name, impact in (review.get("impact_summary") or {}).items()
        },
        "column_totals": review.get("column_totals", {}),
    })


@bp.route("/decisions/<env>/import-scope", methods=["GET", "POST"])
@login_required
def decisions_import_scope(env):
    store = _manifest_store(env)
    manifest = store.load()

    # Sub-task bypass: if this is a sub-task that has already bypassed Discovery, 
    # redirect to Analysis Triage immediately.
    is_subtask = bool(manifest.get("meta", {}).get("parent_env"))
    if is_subtask and manifest.get("resolved_import_scope"):
        return redirect(url_for("main.decisions_analysis_triage", env=env))

    dirs = _dirs()

    raw_inv = manifest.get("raw_key_inventory", {}) or {}
    raw_decisions = manifest.get("raw_key_decisions", {}) or {}

    errors: list[str] = []
    if request.method == "POST":
        confirm_flag = str(request.form.get("confirm_import_scope", "0")).strip()
        commit = confirm_flag == "1"
        force_recommit = str(request.form.get("force_recommit", "0")).strip() == "1"
        was_committed = bool(manifest.get("resolved_import_scope")) or bool(
            (Path(dirs.get("discovery_dir", "discovery")) / f"{env}.json").exists()
        )

        manifest["raw_key_decisions"] = raw_decisions

        # Update decisions for every raw key from explicit per-key form fields.
        for raw_key, inv in raw_inv.items():
            grp = (inv or {}).get("group", "context")
            cur = manifest["raw_key_decisions"].get(raw_key, {})
            if isinstance(cur, str):
                cur = {"key": raw_key, "decision": "include_in_pipeline"}
            if not isinstance(cur, dict):
                cur = {}
            
            rationale = cur.get("rationale", "")
            default_decision = (inv or {}).get("default_decision", "context_only")
            
            # Fetch from form, fallback to existing cur['decision'], then to default.
            existing_decision = cur.get("decision", default_decision)
            decision = str(
                request.form.get(f"decision__{raw_key}", existing_decision)
            ).strip()
            allowed = {"include_in_pipeline", "context_only"} if grp == "pipeline" else {"exclude", "context_only"}
            if decision not in allowed:
                decision = existing_decision
                errors.append(f"Invalid decision submitted for {raw_key}; previous value kept.")

            manifest["raw_key_decisions"][raw_key] = {
                "decision": decision,
                "rationale": rationale,
            }

        # ── Capture target selection scope (tenants + individual VSes) ────────
        tf = manifest.setdefault("tenant_filter", {})
        tf.setdefault("enabled", False)
        tf.setdefault("available_tenants", [])
        tf.setdefault("included_tenants", [])
        tf.setdefault("included_vses", [])
        tf.setdefault("notes", {})
        available = tf.get("available_tenants", [])
        included_tenants = [t for t in available if request.form.get(f"tenant_include__{t}", "0") == "1"]
        
        vs_selected = []
        notes = {}
        for k, v in request.form.items():
            if k.startswith("vs_include__") and v == "1":
                vs_selected.append(k.split("vs_include__")[1])
            elif k.startswith("notes__"):
                # notes__tenant__NAME or notes__vs__NAME
                if k.startswith("notes__tenant__"):
                    notes[f"tenant:{k.split('notes__tenant__')[1]}"] = v
                elif k.startswith("notes__vs__"):
                    notes[f"vs:{k.split('notes__vs__')[1]}"] = v

        tf["enabled"] = str(request.form.get("tenant_filter_enabled", "0")).strip() == "1"
        tf["included_tenants"] = included_tenants
        tf["included_vses"] = vs_selected
        tf["notes"] = notes


        # ── Sub-task Creation Handling ─────────────────────────────────────
        subtask_name = request.form.get("create_subtask_name", "").strip()
        if subtask_name:
            store.save(manifest) # Persist manual decisions to parent first
            if not included_tenants and not vs_selected:
                flash("Select at least one tenant or virtual service before creating a sub-task.", "error")
                return redirect(url_for("main.decisions_import_scope", env=env))
            res = pipeline_svc.create_subtask(dirs, env, subtask_name, included_tenants, vs_selected)
            if res.get("success"):
                flash(f"Sub-task '{subtask_name}' created successfully!", "success")
            else:
                flash(f"Error creating sub-task: {res.get('error')}", "error")
            return redirect(url_for("main.decisions_import_scope", env=env))
        # ─────────────────────────────────────────────────────────────────

        tf["included_tenants"] = included_tenants
        tf["included_vses"] = vs_selected
        
        # Auto-enable tracer if anything is selected OR if the master toggle is on
        form_enabled = request.form.get("tenant_filter_enabled", "0") == "1"
        tf["enabled"] = bool(included_tenants or vs_selected or form_enabled)
        # ─────────────────────────────────────────────────────────────────

        manifest["resolved_import_scope"] = commit
        store.save(manifest)
        store.append_audit(
            manifest,
            "import_scope_raw_keys_updated",
            {"commit": commit},
        )

        if not commit:
            flash("Decisions saved (not committed).", "info")
            return redirect(url_for("main.decisions_import_scope", env=env))

        if was_committed and not force_recommit:
            flash("A committed scope already exists. Confirm override to replace the current committed selection and refresh downstream analysis.", "error")
            return redirect(url_for("main.decisions_import_scope", env=env))

        commit_result, commit_err = _safe(pipeline_svc.commit_import_scope, dirs, env)
        if commit_err:
            flash(f"System error during commit: {commit_err}", "error")
            errors.append(f"System error during commit: {commit_err}")
        elif not commit_result.get("success"):
            flash(commit_result.get("error", "commit_import_scope failed"), "error")
            errors.append(commit_result.get("error", "commit_import_scope failed"))
        else:
            flash("Import scope committed successfully! Analyzing environment...", "success")
            return redirect(url_for("main.decisions_analysis_triage", env=env))

    import_scope_review = pipeline_svc.get_import_scope_review(dirs, env, manifest)
    subtask_status_map = _build_subtask_status_map(dirs, manifest)
    import_scope_review["subtask_statuses"] = list(subtask_status_map.values())
    for vs in import_scope_review.get("vs_list", []):
        child_id = vs.get("handled_by_subtask")
        if not child_id:
            continue
        matched = None
        for child in subtask_status_map.values():
            if child["id"] == child_id or child["env"] == child_id or child["name"] == child_id:
                matched = child
                break
        if matched:
            vs["handled_by_subtask_state"] = matched

    return render_template(
        "decisions_gate.html",
        page_title=f"Import Scope Decisions: {env}",
        env=env,
        phase="import_scope",
        manifest=manifest,
        raw_decisions=raw_decisions,
        import_scope_review=import_scope_review,
        blocker_callout=_build_blocker_callout("import_scope", manifest),
        errors=errors,
    )


@bp.route("/decisions/<env>/analysis-triage", methods=["GET", "POST"])
@login_required
def decisions_analysis_triage(env):
    import hashlib
    store = _manifest_store(env)
    manifest = store.load()
    
    from core.state_ledger import StateLedger
    from pathlib import Path
    dirs = _dirs()
    ledger = StateLedger(env, state_dir=str(Path(dirs.get("state_dir", "state"))))
    unsupported = ledger.unsupported_items(unresolved_only=False)
    if not unsupported:
        unsupported_path = Path(dirs.get("state_dir", "state")) / env / "unsupported.json"
        if unsupported_path.exists():
            try:
                from types import SimpleNamespace
                raw_unsupported = json.loads(unsupported_path.read_text(encoding="utf-8"))
                unsupported = [SimpleNamespace(**item) for item in raw_unsupported if isinstance(item, dict)]
            except Exception:
                unsupported = []
    
    # Load discovery for DataScript hashing
    from services import pipeline_service as pipeline_svc
    disc_file = pipeline_svc.resolve_env_file(dirs.get("discovery_dir", "discovery"), env, ".json")
    discovery = pipeline_svc.read_json(disc_file) if disc_file else {}
    ds_list = discovery.get("datascripts", [])
    
    if request.method == "POST":
        triage = manifest.setdefault("analysis_triage", {"resolved": False, "items": {}})
        triage.setdefault("items", {})
        
        # Get current grouping to map group decisions back to individual items
        current_groups = {}
        for u in unsupported:
            content_hint = ""
            if u.object_type.lower() == "datascript":
                ds_obj = next((d for d in ds_list if d.get("name") == u.object_name), None)
                if ds_obj:
                    # Avi DataScripts are lists of (evt, script) dicts. Concatenate for hashing.
                    ds_raw = ds_obj.get("datascript", [])
                    if isinstance(ds_raw, list):
                        script_content = "\n".join([str(s.get("script", "")) for s in ds_raw])[:200]
                    else:
                        script_content = str(ds_raw.get("script", ""))[:100]
                    content_hint = hashlib.md5(script_content.encode()).hexdigest()
            
            g_key = hashlib.md5(f"{u.object_type}:{u.reason}:{u.severity}:{content_hint}".encode()).hexdigest()
            current_groups.setdefault(g_key, []).append(f"{u.object_type}:{u.object_name}")
        
        for key, value in request.form.items():
            if key.startswith("decision__"):
                group_key = key.split("decision__")[1]
                decision = value.strip()
                rationale = request.form.get(f"rationale__{group_key}", "").strip()
                notes = request.form.get(f"notes__{group_key}", "").strip()
                
                if group_key in current_groups and decision in VALID_TRIAGE_ACTIONS:
                    # Apply this group decision to all items in the group
                    for item_key in current_groups[group_key]:
                        # Check for individual item override first
                        item_decision = request.form.get(f"item_decision__{item_key}", decision).strip()
                        item_rationale = request.form.get(f"item_rationale__{item_key}", rationale).strip()
                        
                        triage["items"][item_key] = {
                            "decision": item_decision if item_decision in VALID_TRIAGE_ACTIONS else decision, 
                            "rationale": item_rationale or rationale,
                            "notes": notes,
                            "group_key": group_key 
                        }
                    store.append_audit(manifest, "analysis_triage_group_decision", {"group": group_key, "decision": decision, "count": len(current_groups[group_key])})
                
        if request.form.get("mark_resolved") == "1":
            manifest["analysis_triage"]["resolved"] = True
            store.append_audit(manifest, "analysis_triage_resolved", {})
            pipeline_svc.log_system_event(dirs, f"Analysis Triage Gate resolved by {current_user.username}", level="INFO", env_name=env)
            store.save(manifest)
            
            # --- Auto-trigger Transform ---
            flash("Analysis triage resolved. Triggering automated Transform...", "success")
            dirs = _dirs()
            pipeline_svc.run_pipeline_phase(dirs, env, "transform")
            return redirect(url_for("main.decisions_transform_approval", env=env))
        else:
            manifest["analysis_triage"]["resolved"] = False
            flash("Triage decisions saved.", "info")
            store.save(manifest)
            return redirect(url_for("main.decisions_analysis_triage", env=env))
        
    # Group items for display
    groups = {}
    saved_items = manifest.get("analysis_triage", {}).get("items", {})
    triage_review_rows = _build_analysis_triage_review(env, dirs, manifest, unsupported)
    triage_review_map = {
        f"{row['object_type']}:{row['object_name']}": row
        for row in triage_review_rows
    }
    
    for u in unsupported:
        content_hint = ""
        if u.object_type.lower() == "datascript":
            ds_obj = next((d for d in ds_list if d.get("name") == u.object_name), None)
            if ds_obj:
                # Avi DataScripts are lists of (evt, script) dicts. Concatenate for hashing.
                ds_raw = ds_obj.get("datascript", [])
                if isinstance(ds_raw, list):
                    script_content = "\n".join([str(s.get("script", "")) for s in ds_raw])[:200]
                else:
                    script_content = str(ds_raw.get("script", ""))[:100]
                content_hint = hashlib.md5(script_content.encode()).hexdigest()
        
        g_key = hashlib.md5(f"{u.object_type}:{u.reason}:{u.severity}:{content_hint}".encode()).hexdigest()
        
        if g_key not in groups:
            # Use one of the individual items to see if we have a saved decision for this group
            # (We look at the first one in the group)
            item_key = f"{u.object_type}:{u.object_name}"
            saved = saved_items.get(item_key, {})
            
            groups[g_key] = {
                "key": g_key,
                "type": u.object_type,
                "reason": u.reason,
                "action": u.action,
                "severity": u.severity,
                "affected": [],
                "affected_context": [],
                "current_decision": saved.get("decision", "defer" if (u.object_type.lower() == "pattern" and "gslb" in u.reason.lower()) else "accept"),
                "current_rationale": saved.get("rationale", "GSLB migration is out of scope for automated tool; requires manual design session." if (u.object_type.lower() == "pattern" and "gslb" in u.reason.lower()) else ""),
                "notes": saved.get("notes", "")
            }
        groups[g_key]["affected"].append(u.object_name)
        row = triage_review_map.get(f"{u.object_type}:{u.object_name}")
        if row:
            saved = saved_items.get(f"{u.object_type}:{u.object_name}", {})
            row["saved_decision"] = saved.get("decision")
            row["saved_rationale"] = saved.get("rationale")
            groups[g_key]["affected_context"].append(row)

    # Ensure subsections exist for template safety
    manifest.setdefault("analysis_triage", {"resolved": False, "items": {}})
    manifest.setdefault("transform_approvals", {"resolved": False, "groups": {}})
    manifest.setdefault("deploy_approval", {"resolved": False})
    manifest.setdefault("post_validation_decision", {"resolved": False})

    triage_groups = sorted(groups.values(), key=lambda x: (x["severity"] == "MANUAL", x["type"]))
    for item in triage_groups:
        item["selection_lines"] = []
        item["dependency_lines"] = []
        item["raw_json_chunks"] = []
        item["delegation_lines"] = []
        for ctx in item.get("affected_context", []):
            item["selection_lines"].extend(
                [f"{ctx['object_name']}: {line}" for line in ctx.get("selection_summary", [])]
            )
            item["dependency_lines"].extend(
                [f"{ctx['object_name']}: {line}" for line in ctx.get("dependency_lines", [])]
            )
            item["raw_json_chunks"].append({
                "name": ctx["object_name"],
                "json": ctx["raw_json"],
                "script_preview": ctx.get("script_preview", ""),
            })
            if ctx.get("delegated_state"):
                item["delegation_lines"].append(
                    f"{ctx['object_name']} delegated to {ctx['delegated_state']['name']} ({ctx['delegated_state']['current_phase_label']} / {ctx['delegated_state']['current_status']})"
                )
        item["selection_lines"] = sorted(list(dict.fromkeys(item["selection_lines"])))[:18]
        item["dependency_lines"] = sorted(list(dict.fromkeys(item["dependency_lines"])))[:24]
        item["delegation_lines"] = sorted(list(dict.fromkeys(item["delegation_lines"])))[:12]

    return render_template(
        "decisions_gate.html",
        page_title=f"Analysis Triage Decisions: {env}",
        env=env,
        phase="analysis_triage",
        manifest=manifest,
        triage_items=triage_groups,
        triage_review={
            "summary": {
                "total": len(triage_groups),
                "decided": len([i for i in triage_groups if i.get("current_decision")])
            }
        },
        blocker_callout=_build_blocker_callout("analysis_triage", manifest, triage_items=triage_groups),
    )



@bp.route("/decisions/<env>/transform-approval", methods=["GET", "POST"])
@login_required
def decisions_transform_approval(env):
    store = _manifest_store(env)
    manifest = store.load()
    dirs = _dirs()
    
    if request.method == "POST":
        group = request.form.get("group", "").strip()
        decision = request.form.get("decision", "").strip()
        rationale = request.form.get("rationale", "").strip()
        notes = request.form.get("notes", "").strip()
        if group and decision in {"include", "exclude"}:
            transform = manifest.setdefault("transform_approvals", {"resolved": False, "groups": {}})
            transform.setdefault("groups", {})
            transform["groups"][group] = {"decision": decision, "rationale": rationale}
            store.append_audit(manifest, "transform_group_decision", {"group": group, "decision": decision, "rationale": rationale})
        elif group and decision in TRANSFORM_REVIEW_STATES:
            transform = manifest.setdefault("transform_approvals", {"resolved": False, "groups": {}})
            transform.setdefault("groups", {})
            transform["groups"][group] = {
                "decision": decision,
                "rationale": rationale,
                "notes": notes,
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }
            store.append_audit(
                manifest,
                "transform_category_review_saved",
                {"group": group, "decision": decision, "rationale": rationale, "notes": notes},
            )
            flash(f"Saved transform review draft for {group}.", "info")
            
        if request.form.get("mark_resolved") == "1":
            manifest["transform_approvals"]["resolved"] = True
            store.append_audit(manifest, "transform_resolved", {})
            pipeline_svc.log_system_event(dirs, f"Transform Approval Gate resolved by {current_user.username}", level="INFO", env_name=env)
            store.save(manifest)
            flash("Transform phase approved. You are ready for deployment.", "success")
        else:
            manifest["transform_approvals"]["resolved"] = False
            flash("Transformation mapping saved.", "info")
            store.save(manifest)
            
        return redirect(url_for("main.decisions_transform_approval", env=env))

    # Load generated FortiADC config for review
    config_data = {}
    fadc_dir = Path(dirs.get("fortiadc_dir", "fortiadc"))
    config_file = fadc_dir / f"{env}-config.json"
    if config_file.exists():
        try:
            config_data = pipeline_svc.read_json(config_file)
        except:
            config_data = {"error": "Failed to parse config JSON"}

    transform_review = _build_transform_review(env, dirs, manifest, config_data)
    if config_data:
        _write_transform_review_reports(dirs, env, transform_review, config_data)

    return render_template(
        "decisions_gate.html",
        page_title=f"Transform Approvals: {env}",
        env=env,
        phase="transform_approval",
        manifest=manifest,
        transform_review=transform_review,
        transform_mapping_review=pipeline_svc.get_transform_mapping_review(dirs, env, manifest),
        config_json=json.dumps(config_data, indent=2) if config_data else "",
        blocker_callout=_build_blocker_callout("transform_approval", manifest, transform_review=transform_review),
    )


@bp.route("/decisions/<env>/save-override", methods=["POST"])
@roles_required(['admin', 'user'])
def decisions_save_override(env):
    """API: Stage an operator JSON payload override for a single FortiADC object."""
    store = _manifest_store(env)
    manifest = store.load()

    fortiadc_path = (request.form.get("fortiadc_path") or "").strip()
    mkey          = (request.form.get("mkey") or "").strip()
    raw_payload   = (request.form.get("payload_json") or "").strip()
    note          = (request.form.get("note") or "").strip()

    if not fortiadc_path or not mkey or not raw_payload:
        flash("Override failed: missing path, mkey, or payload.", "error")
        return redirect(request.referrer or url_for("main.decisions_transform_approval", env=env))

    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        flash(f"Override failed: invalid JSON — {exc}", "error")
        return redirect(request.referrer or url_for("main.decisions_transform_approval", env=env))

    store.save_manual_override(manifest, fortiadc_path, mkey, payload, note)
    flash(f"Override staged for '{mkey}'. Re-run Transform to apply.", "success")
    return redirect(url_for("main.decisions_transform_approval", env=env))


@bp.route("/decisions/<env>/delete-override", methods=["POST"])
@roles_required(['admin', 'user'])
def decisions_delete_override(env):
    """Remove a staged override so the transformer output is used again."""
    store = _manifest_store(env)
    manifest = store.load()
    okey = (request.form.get("override_key") or "").strip()
    if okey and okey in manifest.get("manual_overrides", {}):
        del manifest["manual_overrides"][okey]
        store.append_audit(manifest, "manual_override_deleted", {"key": okey})
        flash(f"Override '{okey}' removed.", "info")
    else:
        flash("Override not found.", "error")
    return redirect(url_for("main.decisions_transform_approval", env=env))


@bp.route("/decisions/<env>/snapshots", methods=["GET", "POST"])
@login_required
def decisions_snapshots(env):
    store = _manifest_store(env)
    manifest = store.load()
    if request.method == "POST":
        label = request.form.get("label", "manual").strip() or "manual"
        name = store.create_snapshot(manifest, label)
        if name:
            flash(f"Snapshot '{name}' created successfully.", "success")
        else:
            flash("Failed to create snapshot.", "error")
        return redirect(request.referrer or url_for("main.decisions_transform_approval", env=env))
    
    snapshots = store.list_snapshots()
    return jsonify({"snapshots": snapshots})


@bp.route("/decisions/<env>/restore-snapshot", methods=["POST"])
@roles_required(['admin', 'user'])
def decisions_restore_snapshot(env):
    name = request.form.get("snapshot_name")
    if not name:
        flash("No snapshot name provided.", "error")
        return redirect(request.referrer or url_for("main.decisions_transform_approval", env=env))
    
    store = _manifest_store(env)
    if store.restore_snapshot(name):
        flash(f"Successfully restored state from '{name}'.", "success")
    else:
        flash(f"Failed to restore snapshot '{name}'.", "error")
    return redirect(request.referrer or url_for("main.decisions_transform_approval", env=env))


@bp.route("/decisions/<env>/deploy-approval", methods=["GET", "POST"])
@login_required
def decisions_deploy_approval(env):
    dirs = _dirs()
    store = _manifest_store(env)
    manifest = store.load()
    deploy = manifest.setdefault("deploy_approval", {})
    if request.method == "POST":
        mode = request.form.get("mode", "").strip()
        deploy["mode"] = mode
        deploy["dry_run_reviewed"] = request.form.get("dry_run_reviewed") == "on"
        deploy["cr_id"] = request.form.get("cr_id", "").strip()
        deploy["sod_approver"] = request.form.get("sod_approver", "").strip()
        deploy["operator_ack"] = request.form.get("operator_ack") == "on"
        deploy["governance_confirmed"] = request.form.get("governance_confirmed") == "on"
        deploy["maintenance_window"] = request.form.get("maintenance_window") == "on"
        deploy["notes"] = request.form.get("notes", "").strip()
        
        # Identity-based signature logic
        operator_name = current_user.username
        checklist = _build_deploy_checklist(deploy)
        
        # Enforce Four-Eyes (SoD) principle
        sod_violation = False
        if request.form.get("mark_resolved") == "1":
            if not (current_user.has_role('approver') or current_user.has_role('admin')):
                flash("Only an authorized Approver can sign off on production execution.", "error")
                return redirect(url_for("main.decisions_deploy_approval", env=env))
            
            if deploy["sod_approver"] == operator_name:
                flash(f"Governance Violation: Separation of Duties (SoD) requires the Approver to be different from the Operator ({operator_name}).", "error")
                sod_violation = True

        deploy["resolved"] = (request.form.get("mark_resolved") == "1" 
                           and not sod_violation 
                           and all(item["done"] for item in checklist) 
                           and mode == "execute")
        
        # Store metadata about the signature
        deploy["operator_signature"] = operator_name
        deploy["timestamp"] = datetime.now(timezone.utc).isoformat()

        store.append_audit(manifest, "deploy_approval_updated", dict(deploy))
        
        if request.form.get("mark_resolved") == "1":
            if not deploy["resolved"] and not sod_violation:
                if mode != "execute":
                    flash("Authorization Blocked: To sign and authorize deployment, you must select 'Production Execution' as the strategy.", "error")
                else:
                    flash("Deploy approval is still blocked. Ensure all pre-flight checklist items are checked.", "error")
            elif deploy["resolved"]:
                pipeline_svc.log_system_event(_dirs(), f"Deployment authorized by {current_user.username} (Approver: {deploy.get('sod_approver')})", level="INFO", env_name=env)
                flash("Deployment strategy approved. Final validation gates unlocked.", "success")
        else:
            flash("Deployment parameters saved.", "info")
            
        store.save(manifest)
        return redirect(url_for("main.decisions_deploy_approval", env=env))
    checklist = _build_deploy_checklist(deploy)
    
    # Calculate Technical Manifest for the Approver view
    fadc_dir = Path(dirs.get("fortiadc_dir", "fortiadc"))
    config_file = fadc_dir / f"{env}-config.json"
    tech_manifest = {"vs": 0, "pools": 0, "certs": 0, "overrides": len(manifest.get("manual_overrides", {})), "ready": True}
    if config_file.exists():
        try:
            cfg = pipeline_svc.read_json(config_file)
            tech_manifest["vs"] = len(cfg.get("virtual_servers", []))
            tech_manifest["pools"] = len(cfg.get("real_server_pools", []))
            tech_manifest["certs"] = len(cfg.get("ssl_certificates", []))
        except: pass

    # Check readiness of previous gates
    dirs = _dirs()
    ledger = StateLedger(env, state_dir=dirs.get("state_dir", "state"))
    gates_ok = {
        "import": ensure_phase_gate(manifest, "import", ledger._data).ok,
        "analyse": ensure_phase_gate(manifest, "analyse", ledger._data).ok,
        "transform": ensure_phase_gate(manifest, "transform", ledger._data).ok
    }
    gates_reasons = {
        "import": ensure_phase_gate(manifest, "import", ledger._data).message,
        "analyse": ensure_phase_gate(manifest, "analyse", ledger._data).message,
        "transform": ensure_phase_gate(manifest, "transform", ledger._data).message
    }
    tech_manifest["ready"] = all(gates_ok.values())

    # Get authorized approvers for the dropdown
    all_users = user_svc.get_all_users()
    authorized_approvers = [u for u in all_users if u.has_role('approver') or u.has_role('admin')]

    return render_template(
        "decisions_gate.html",
        page_title=f"Deploy Approval: {env}",
        env=env,
        phase="deploy_approval",
        manifest=manifest,
        deploy_checklist=checklist,
        tech_manifest=tech_manifest,
        gates_readiness=gates_ok,
        gates_reasons=gates_reasons,
        approvers=authorized_approvers,
        blocker_callout=_build_blocker_callout("deploy_approval", manifest),
    )


@bp.route("/decisions/<env>/execute-deploy", methods=["POST"])
@roles_required(['admin', 'user'])
def decisions_execute_deploy(env):
    """
    Final Trigger: Perform the actual REST API push to FortiADC.
    Only allows execution if the manifest is fully approved and signed-off.
    """
    dirs = _dirs()
    store = _manifest_store(env)
    manifest = store.load()
    deploy = manifest.get("deploy_approval", {})
    
    # Final Governance Guardrails
    if not deploy.get("resolved"):
         flash("Execution Denied: Deploy gate is not resolved/signed-off.", "error")
         return redirect(url_for("main.decisions_deploy_approval", env=env))
    
    if deploy.get("mode") != "execute":
         flash("Execution Denied: Environment is currently in 'Dry-Run' mode. Change strategy to 'Production Execution' to proceed.", "error")
         return redirect(url_for("main.decisions_deploy_approval", env=env))

    # Trigger Deployment (removes --dry-run)
    pipeline_svc.log_system_event(dirs, f"USER TRIGGERED LIVE DEPLOYMENT: Push initiated by {current_user.username}", level="WARNING", env_name=env)
    flash(f"Finalizing governance. Triggering live deployment to FortiADC for '{env}'...", "info")
    res = pipeline_svc.run_pipeline_phase(dirs, env, "deploy", live_execution=True)
    
    if res.get("success"):
        pipeline_svc.log_system_event(dirs, f"LIVE DEPLOYMENT COMPLETED: Configuration pushed successfully for {env}", level="SUCCESS", env_name=env)
        flash(f"LIVE DEPLOYMENT SUCCESSFUL: {res.get('message')}", "success")
        # Mark validation phase as next
        return redirect(url_for("main.decisions_overview", env=env))
    else:
        status_detail = res.get("details", "") or res.get("error", "Unknown error")
        flash(f"LIVE DEPLOYMENT FAILED: {status_detail}", "error")
        return redirect(url_for("main.decisions_deploy_approval", env=env))


@bp.route("/decisions/<env>/post-validation", methods=["GET", "POST"])
def decisions_post_validation(env):
    store = _manifest_store(env)
    manifest = store.load()
    if request.method == "POST":
        action = request.form.get("action", "").strip()
        notes = request.form.get("notes", "").strip()
        
        # Capture Manual Milestones
        milestones = {
            "parallel_run":   request.form.get("complete_parallel") == "1",
            "parallel_notes": request.form.get("parallel_notes", "").strip(),
            "dns_cutover":    request.form.get("complete_dns") == "1",
            "dns_notes":      request.form.get("dns_notes", "").strip(),
        }

        if action in VALID_POST_ACTIONS:
            post = manifest.setdefault("post_validation_decision", {})
            post["action"] = action
            post["notes"] = notes
            post["milestones"] = milestones
            post["resolved"] = request.form.get("mark_resolved") == "1"
            
            # Mirror to Technical Ledger
            from core.state_ledger import StateLedger
            ledger = StateLedger(env)
            if milestones["parallel_run"]:
                ledger.phase_done("parallel-run", notes=milestones["parallel_notes"] or "Manual verification complete.")
            if milestones["dns_cutover"]:
                ledger.phase_done("dns-cutover", notes=milestones["dns_notes"] or "Manual traffic switch complete.")
            
            store.append_audit(manifest, "post_validation_decision_set", dict(post))
            if post["resolved"]:
                pipeline_svc.log_system_event(_dirs(), f"Migration context '{env}' closed with final action: {action.upper()}", level="SUCCESS", env_name=env)
                flash("Post-validation confirmed. Migration lifecycle complete.", "success")
            else:
                flash("Operational milestones and validation notes saved.", "info")
            store.save(manifest)
        return redirect(url_for("main.decisions_post_validation", env=env))
    return render_template(
        "decisions_gate.html",
        page_title=f"Post Validation Decision: {env}",
        env=env,
        phase="post_validation_decision",
        manifest=manifest,
        blocker_callout=_build_blocker_callout("post_validation_decision", manifest),
    )


@bp.route("/api/decisions/<env>/status")
def api_decision_status(env):
    store = _manifest_store(env)
    manifest = store.load()
    dirs = _dirs()
    ledger = StateLedger(env, state_dir=dirs.get("state_dir", "state"))
    return jsonify(
        {
            "env": env,
            "status": manifest.get("status", "draft"),
            "gates": {
                "analyse": ensure_phase_gate(manifest, "analyse", ledger._data).__dict__,
                "transform": ensure_phase_gate(manifest, "transform", ledger._data).__dict__,
                "deploy": ensure_phase_gate(manifest, "deploy", ledger._data).__dict__,
                "verify": ensure_phase_gate(manifest, "verify", ledger._data).__dict__,
            },
        }
    )


@bp.route("/api/import/commit", methods=["POST"])
@login_required
def api_import_commit():
    """API endpoint for analyzer-to-tool strict import flow."""
    if request.method == "OPTIONS":
        resp = jsonify({"ok": True})
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        return resp, 200

    body = request.get_json(silent=True) or {}
    env_name = str(body.get("env_name", "")).strip().lower()
    payload_type = str(body.get("payload_type", "normalized_discovery")).strip()
    payload = body.get("payload")

    if not env_name:
        return jsonify({"success": False, "error": "env_name is required"}), 400

    if not isinstance(payload, dict):
        return jsonify({"success": False, "error": "payload must be a JSON object"}), 400

    dirs = _dirs()
    result = pipeline_svc.import_avi_config(
        dirs,
        json.dumps(payload),
        env_name,
        payload_type=payload_type or "normalized_discovery",
    )
    status = 200 if result.get("success") or result.get("decision_required") else 400
    resp = jsonify(result)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp, status


@bp.route("/pipeline/<env>")
def pipeline_detail(env):
    dirs = _dirs()
    history = pipeline_svc.get_environment_history(dirs, env)
    status  = pipeline_svc.get_pipeline_status(dirs)
    # Find this specific env in status
    this_env = next((e for e in status["environments"] if e["name"] == env), None)
    
    if not this_env:
        return redirect(url_for("main.home"))
        
    return render_template(
        "env_detail.html",
        page_title=f"Pipeline: {env}",
        env=this_env,
        history=history,
    )


@bp.route("/env/delete/<env>", methods=["POST"])
@login_required
def delete_env(env):
    dirs = _dirs()
    pipeline_svc.delete_environment(dirs, env)
    return redirect(url_for("main.home"))


# ── Tab 2: Knowledge Base ────────────────────────────────────────────────────

@bp.route("/kb")
@login_required
def kb():
    dirs    = _dirs()
    search  = request.args.get("q", "").strip()
    entries, err1 = _safe(kb_svc.get_kb_entries,  dirs, query=search)
    summary, err2 = _safe(kb_svc.get_kb_summary,  dirs)
    return render_template(
        "kb.html",
        page_title = "Knowledge Base",
        entries    = entries or [],
        summary    = summary,
        search     = search,
        errors     = [e for e in [err1, err2] if e],
    )


# ── Tab 3: Reports & Artifacts ───────────────────────────────────────────────

@bp.route("/reports")
@login_required
def reports():
    dirs = _dirs()
    env  = request.args.get("env", "")
    rpts, err1 = _safe(report_svc.get_all_reports,    dirs)
    arts, err2 = _safe(report_svc.get_artifacts,      dirs, env=env)
    envs, err3 = _safe(report_svc.get_known_envs,     dirs)
    return render_template(
        "reports.html",
        page_title = "Reports & Artifacts",
        reports    = rpts or [],
        artifacts  = arts or [],
        envs       = envs or [],
        selected_env = env,
        errors     = [e for e in [err1, err2, err3] if e],
    )


@bp.route("/reports/view/<path:filename>")
@login_required
def reports_view(filename):
    """Securely serve a specific report file."""
    dirs = _dirs()
    safe_name = _safe_filename(filename)
    if not safe_name:
         abort(400)
    reports_dir = Path(dirs.get("reports_dir", "reports"))
    return send_from_directory(str(reports_dir), safe_name)


@bp.route("/logs/view/<path:filename>")
@login_required
def logs_view(filename):
    """Securely serve a specific log file from the logs directory."""
    dirs = _dirs()
    safe_name = _safe_filename(filename)
    if not safe_name:
         abort(400)
    logs_dir = Path(dirs.get("logs_dir", "logs"))
    return send_from_directory(str(logs_dir), safe_name, mimetype="text/plain")


# ── Tab 4: Analytics ─────────────────────────────────────────────────────────

@bp.route("/analytics")
@login_required
def analytics():
    dirs = _dirs()
    env  = request.args.get("env", "")
    summary,  err1 = _safe(analytics_svc.get_migration_summary, dirs, env=env)
    compat,   err2 = _safe(analytics_svc.get_compatibility_breakdown, dirs, env=env)
    patterns, err3 = _safe(analytics_svc.get_pattern_findings,  dirs, env=env)
    certs,    err4 = _safe(analytics_svc.get_cert_status,       dirs, env=env)
    envs,     err5 = _safe(report_svc.get_known_envs,           dirs)
    return render_template(
        "analytics.html",
        page_title   = "Analytics",
        summary      = summary,
        compat       = compat,
        patterns     = patterns or [],
        certs        = certs or [],
        envs         = envs or [],
        selected_env = env,
        errors       = [e for e in [err1, err2, err3, err4, err5] if e],
    )


# ── Tab 5: Quick Actions ─────────────────────────────────────────────────────

@bp.route("/actions")
@login_required
def actions():
    dirs = _dirs()
    envs, _ = _safe(report_svc.get_known_envs, dirs)
    return render_template(
        "actions.html",
        page_title = "Quick Actions",
        envs       = envs or [],
        last_action = request.args.get("last_action", ""),
        last_status = request.args.get("last_status", ""),
    )


@bp.route("/actions/run", methods=["POST"])
@login_required
def run_action():
    """
    Execute a quick action.
    Actions are read-only analysis operations only — no deploy, no DNS changes.
    Mutations require the wizard or CLI with proper governance controls.
    """
    action = request.form.get("action", "").strip()
    env    = request.form.get("env",    "").strip()
    dirs   = _dirs()

    allowed_readonly = {
        "discover_dry",
        "analyse",
        "ops_check",
        "llm_pack_full",
        "llm_pack_next_steps",
    }

    if action not in allowed_readonly:
        return redirect(url_for("main.actions",
                                last_action=action,
                                last_status="blocked_not_allowed"))

    if not env:
        return redirect(url_for("main.actions",
                                last_action=action,
                                last_status="error_no_env"))

    result, err = _safe(pipeline_svc.run_readonly_action, dirs, action=action, env=env)
    status = "error" if err else ("ok" if result else "no_output")
    return redirect(url_for("main.actions",
                            last_action=f"{action}:{env}",
                            last_status=status))



@bp.route("/extract-datascripts/<env>")
@login_required
def extract_datascripts_route(env: str):
    dirs = _dirs()
    res = pipeline_svc.extract_datascripts(dirs, env)
    return jsonify(res)


# ── AI Assistant Endpoints ───────────────────────────────────────────────────

@bp.route("/api/ai/chat", methods=["POST"])
@login_required
def ai_chat():
    """
    Floating Assistant Chat endpoint.
    Expects JSON: { "message": str, "history": list[dict], "env": str? }
    """
    data    = request.json or {}
    message = data.get("message", "").strip()
    history = data.get("history", [])
    env     = data.get("env", "").strip()
    
    if not message:
        return jsonify({"error": "No message provided"}), 400
        
    dirs = _dirs()
    result = rag_svc.chat(message, history, dirs, env=env)
    return jsonify(result)


@bp.route("/api/ai/index", methods=["GET", "POST"])
@login_required
def ai_index():
    """
    RAG Index management endpoint.
    GET: Return stats.
    POST: Trigger (re)build.
    """
    dirs = _dirs()
    if request.method == "POST":
        # Usually triggered once to include the 'code' source
        rebuild = request.form.get("rebuild", "false").lower() == "true"
        sources = request.form.getlist("sources") or None
        result = rag_svc.rebuild_index(dirs, sources=sources, rebuild=rebuild)
        return jsonify(result)
    
    stats = rag_svc.get_index_stats(dirs)
    return jsonify(stats)


@bp.route("/api/ai/insight", methods=["POST"])
@login_required
def ai_insight():
    """Contextual insight trigger for ✨ buttons."""
    data    = request.json or {}
    query   = data.get("query", "").strip()
    env     = data.get("env",   "").strip()
    dirs    = _dirs()
    
    resp = rag_svc.ask(dirs, query, env=env)
    return jsonify(resp)


@bp.route("/api/ai/save-experience", methods=["POST"])
@login_required
def ai_save_experience():
    """Commit an AI insight to the local KB."""
    data    = request.json or {}
    title   = data.get("title", "Insight Export")
    content = data.get("content", "")
    tags    = data.get("tags", ["verified"])
    dirs    = _dirs()
    
    resp = rag_svc.save_experience(dirs, title, content, tags)
    return jsonify(resp)


# ── V-A-N-R Topology & Strategy Endpoints ────────────────────────────────────

@bp.route("/api/topology/<env>")
@login_required
def api_topology(env: str):
    """Returns hierarchical graph data for the V-A-N-R dashboard."""
    dirs = _dirs()
    topo = pipeline_svc.get_topology_data(dirs, env)
    return jsonify(topo)


@bp.route("/api/vdom/strategies/<env>", methods=["GET", "POST"])
@login_required
def api_vdom_strategies(env: str):
    """GET: return current mappings. POST: update vdom mapping strategy."""
    dirs = _dirs()
    store = _manifest_store(env)
    manifest = store.load()
    
    if request.method == "POST":
        data = request.json or {}
        v_map = manifest.setdefault("vdom_mapping", {"resolved": False, "mappings": {}})
        v_map.setdefault("mappings", {})

        if data.get("batch"):
            updates = data.get("updates", [])
            for u in updates:
                avi_tenant = u.get("avi_tenant")
                strategy = u.get("strategy")
                target = u.get("target")
                params = u.get("params", {})
                if avi_tenant and strategy:
                    v_map["mappings"][avi_tenant] = {
                        "strategy": strategy,
                        "target": target or avi_tenant,
                        "params": params,
                        "note": u.get("note", ""),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "updated_by": current_user.username
                    }
            store.save(manifest)
            store.append_audit(manifest, "vdom_mapping_batch_updated", {"count": len(updates)})
            return jsonify({"success": True, "count": len(updates)})

        avi_tenant = data.get("avi_tenant")
        strategy = data.get("strategy")
        target = data.get("target")
        params = data.get("params", {})
        
        if not avi_tenant or not strategy:
            return jsonify({"error": "Missing required fields"}), 400
            
        v_map["mappings"][avi_tenant] = {
            "strategy": strategy,
            "target": target or avi_tenant,
            "params": params,
            "note": data.get("note", ""),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": current_user.username
        }
        store.save(manifest)
        store.append_audit(manifest, "vdom_mapping_updated", {"tenant": avi_tenant, "strategy": strategy})
        return jsonify({"success": True})

    return jsonify(manifest.get("vdom_mapping", {}))


@bp.route("/api/network/strategies/<env>", methods=["GET", "POST"])
@login_required
def api_network_strategies(env: str):
    """Manages modular network mapping (VLANs, SNAT, Routing)."""
    dirs = _dirs()
    store = _manifest_store(env)
    manifest = store.load()
    
    if request.method == "POST":
        data = request.json or {}
        component_key = data.get("key") # e.g. "vlan:100" or "snat:pool_1"
        strategy = data.get("strategy")
        target = data.get("target")
        
        if not component_key or not strategy:
            return jsonify({"error": "Missing required fields"}), 400
            
        n_map = manifest.setdefault("network_mapping", {"resolved": False, "mappings": {}})
        n_map.setdefault("mappings", {})
        n_map["mappings"][component_key] = {
            "strategy": strategy,
            "target": target,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        store.save(manifest)
        return jsonify({"success": True})
        
    return jsonify(manifest.get("network_mapping", {}))


@bp.route("/api/fortiadc/existing-vdoms/<env>")
@login_required
def api_fortiadc_existing_vdoms(env: str):
    """Captures VDOMs already present in the target FortiADC baseline."""
    dirs = _dirs()
    vdoms = []
    source = "disconnected"
    
    # 1. Attempt Live Connection
    client = None
    try:
        client = _get_fortiadc_client(env, dry_run=False)
        if client:
            resp = client.get("system/vdom")
            payload = resp.get("payload", [])
            live_vdoms = set()
            if isinstance(payload, list):
                for v in payload:
                    if isinstance(v, dict) and "vdom" in v:
                        live_vdoms.add(v["vdom"])
            elif isinstance(payload, dict) and "vdom" in payload:
                live_vdoms.add(payload["vdom"])
            
            if live_vdoms:
                vdoms = sorted(list(live_vdoms))
                source = "live"
    except Exception:
        # Fall back silently to local sources on connectivity error
        pass
    finally:
        if client:
            client.close()
            
    # 2. If Live Connection failed or returned nothing, fall back to local cached baseline & manual decisions
    if not vdoms:
        vdoms, local_source = pipeline_svc.get_existing_vdoms(dirs, env)
        source = local_source
        
    # Improve source labeling for the UI
    pretty_source = "Local Planning Data"
    if source == "disconnected":
        pretty_source = "disconnected"
    elif source == "cached target baseline":
        pretty_source = "Cached Target Baseline (FortiADC Config)"
    elif source == "manual orchestrator baseline":
        pretty_source = "Manual Decisions (Operator Added VDOMs)"
    elif source == "live":
        pretty_source = "Live FortiADC API"
        
    return jsonify({"vdoms": vdoms, "source": pretty_source})

@bp.route("/api/vdom/probe/<env>")
@login_required
def api_vdom_probe(env: str):
    """Probes the live target FortiADC API for connectivity."""
    client = None
    try:
        client = _get_fortiadc_client(env, dry_run=False)
        if not client:
            return jsonify({
                "success": False, 
                "error": "Configuration error: Target environment or config.yaml not found.",
                "message": "Configuration error: Target environment or config.yaml not found."
            }), 400
            
        # Try fetching system/vdom to confirm operational API access
        resp = client.get("system/vdom")
        payload = resp.get("payload", [])
        vdom_list = []
        if isinstance(payload, list):
            for v in payload:
                if isinstance(v, dict) and "vdom" in v:
                    vdom_list.append(v["vdom"])
        elif isinstance(payload, dict):
            vdom_list = [payload.get("vdom", "root")]
            
        return jsonify({
            "success": True,
            "message": f"Successfully connected to FortiADC at {client.host}!",
            "source": "live",
            "vdoms": vdom_list or ["root"]
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Connection failed: {str(e)}",
            "message": f"Connection failed: {str(e)}"
        })
    finally:
        if client:
            client.close()
@bp.route("/api/fortiadc/existing-config/<env>")
@login_required
def api_fortiadc_existing_config(env: str):
    """Returns the full cached/saved target FortiADC state for reference evaluation."""
    dirs = _dirs()
    config = pipeline_svc.get_existing_fortiadc_config(dirs, env)
    return jsonify(config)
@bp.route("/logs/view/<path:filename>")
@login_required
def view_execution_log(filename: str):
    """Serves raw execution pipeline log files securely from the logs directory."""
    dirs = _dirs()
    logs_dir = Path(dirs.get("logs_dir", "logs"))
    
    try:
        safe_path = logs_dir.resolve()
        file_path = (logs_dir / filename).resolve()
        
        # Path traversal guard
        if not str(file_path).startswith(str(safe_path)):
            abort(403)
            
        if not file_path.exists():
            return f"Log file not found on target system: {filename}", 404
            
        content = file_path.read_text(encoding="utf-8", errors="replace")
        return f"<pre style='color: #c9d1d9; background: #0d1117; padding: 12px; font-size: 0.85rem; font-family: monospace; white-space: pre-wrap; margin: 0;'>{html.escape(content)}</pre>"
    except Exception as e:
        return f"Error loading execution path: {str(e)}", 500
