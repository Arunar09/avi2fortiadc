"""
reporters/markdown.py
Markdown migration report — suitable for Change Request attachment.
"""
from __future__ import annotations
from datetime import datetime, timezone
from core.events import EventBus, Level
from analyzers.dependency_graph import VSProfile
from analyzers.compatibility import CompatibilityResult, Compat
from analyzers.impact import ImpactAssessment


def generate_markdown(
    env_name: str,
    bus: EventBus,
    vs_profiles: dict[str, VSProfile],
    compat_results: list[CompatibilityResult],
    impact: list[ImpactAssessment],
    discovery: dict,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    counts = bus.summary_counts()
    status_counts = {s.value: sum(1 for r in compat_results if r.status == s)
                     for s in Compat}
    auto   = sum(1 for p in vs_profiles.values() if p.can_auto_migrate)
    manual_vs = len(vs_profiles) - auto

    md = [
        f"# Avi → FortiADC Migration Report",
        f"**Environment:** {env_name}  ",
        f"**Generated:** {now}  ",
        f"**Tool Version:** 0.1.0",
        "",
        "---",
        "",
        "## Executive Summary",
        "",
        f"| Category | Count |",
        f"|----------|-------|",
        f"| Total Virtual Services | {len(vs_profiles)} |",
        f"| Auto-migratable VS | {auto} |",
        f"| VS requiring manual work | {manual_vs} |",
        f"| SSL Certificates | {len(discovery.get('ssl_certificates', []))} |",
        f"| DataScripts (all MANUAL) | {len(discovery.get('datascripts', []))} |",
        f"| External connections discovered | {len(discovery.get('connections', []))} |",
        "",
        "### Message Counts",
        "",
        f"| Level | Count | Meaning |",
        f"|-------|-------|---------|",
        f"| 🚫 CRITICAL | {counts[Level.CRITICAL.value]} | Blocking — must fix before migration |",
        f"| ⚠️  MANUAL  | {counts[Level.MANUAL.value]}  | Human action required |",
        f"| ⚠️  WARN    | {counts[Level.WARN.value]}    | Review recommended |",
        f"| ❌ ERROR   | {counts[Level.ERROR.value]}   | Non-blocking failure |",
        f"| ✅ INFO    | {counts[Level.INFO.value]}    | Successful operation |",
        "",
        "---",
        "",
        "## Compatibility Matrix",
        "",
        f"| Status | Count | Meaning |",
        f"|--------|-------|---------|",
        f"| ✅ AUTO    | {status_counts['AUTO']}    | Can be migrated automatically |",
        f"| ⚠️  WARN   | {status_counts['WARN']}   | Migrate with review |",
        f"| 🔧 MANUAL  | {status_counts['MANUAL']} | Human action required before migration |",
        f"| 🚫 BLOCKED | {status_counts['BLOCKED']} | Cannot migrate until prerequisite resolved |",
        "",
        "---",
        "",
        "## Manual Action Items",
        "",
        "_These must be completed before migration can proceed._",
        "",
    ]

    manual_events = [e for e in bus.all_events if e.level == Level.MANUAL]
    critical_events = [e for e in bus.all_events if e.level == Level.CRITICAL]

    for i, e in enumerate(critical_events + manual_events, 1):
        icon = "🚫" if e.level == Level.CRITICAL else "🔧"
        md.append(f"### {icon} Item {i:03d}: {e.object_type}/{e.object_name}")
        md.append(f"**Issue:** {e.message}  ")
        action = e.detail.get("action", e.detail.get("suggestion", "See detail."))
        md.append(f"**Action:** {action}  ")
        md.append(f"**Blocking:** {'Yes' if e.blocking else 'No'}  ")
        if e.detail.get("options"):
            md.append("**Options:**")
            for opt in e.detail["options"]:
                md.append(f"- {opt}")
        md.append("")

    md += [
        "---",
        "",
        "## Virtual Service Impact Assessment",
        "",
        "| VS Name | Risk | Members | Blockers | Recommendation |",
        "|---------|------|---------|----------|----------------|",
    ]
    for ia in impact[:30]:
        profile = vs_profiles.get(ia.vs_name)
        blockers = len(profile.blockers) if profile else 0
        risk_icon = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴", "CRITICAL": "🚫"}.get(ia.risk_level, "")
        md.append(f"| {ia.vs_name} | {risk_icon} {ia.risk_level} | "
                  f"{ia.members_up}/{ia.member_count} up | {blockers} | "
                  f"{ia.recommendation[:60]} |")

    md += [
        "",
        "---",
        "",
        "## External Systems Requiring Reconfiguration",
        "",
        "| System | Connection Type | Action Required |",
        "|--------|----------------|----------------|",
    ]
    for conn in discovery.get("connections", []):
        ctype = conn.get("type", "?")
        cname = conn.get("name", "?")
        action_map = {
            "ipam_dns":  "Configure DNS forwarder in FortiADC",
            "cloud":     "Pre-allocate static VIPs for all VS",
            "auth":      "Reconfigure LDAP/SAML in FortiADC",
            "snmp":      "Add SNMP trap receivers in FortiADC",
            "syslog":    "Configure syslog in FortiADC log settings",
            "gslb":      "Redesign using FortiGSLB or DNS-based LB",
        }
        md.append(f"| {cname} | {ctype} | {action_map.get(ctype, 'Review and reconfigure')} |")

    md += ["", "---", "", "## Migration Strategy Recommendations",
           "",
           "1. **Phase 1: Validation** — Migrate a non-production or sandpit environment to validate parity.",
           "2. **Phase 2: Staging** — Parallel run for 7 days to ensure traffic patterns match Avi analytics.",
           "3. **Phase 3: Production Cutover** — Final cutover with rollback plan (keep Avi VS disabled but intact).",
           "",
           "_Only decommission the Avi objects after traffic monitoring confirms FortiADC stability._",
           "",
           ]

    return "\n".join(md)
